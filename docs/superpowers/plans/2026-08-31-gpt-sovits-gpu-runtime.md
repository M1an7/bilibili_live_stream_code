# GPT-SoVITS GPU Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Follow superpowers:test-driven-development for every production change and superpowers:verification-before-completion before integration.

**Goal:** Make imported GPT-SoVITS v2Pro/v2ProPlus voices synthesize real Japanese danmu on the local RTX 3060 through an independently installed CUDA FP16 runtime, while keeping the main EXE and idle GPU usage small.

**Architecture:** The main process validates signed runtime packages, manages a token-authenticated loopback sidecar, routes `pack:` voices to a personalized speech service, and streams PCM through an injectable PortAudio player. The runtime pins official GPT-SoVITS source and dependencies in a separate ZIP. Voice readiness is recorded only after a real GPU load, warm-up, preview, and audio validation.

**Tech Stack:** Python 3.12 main app, Python 3.10 runtime, PyTorch CUDA 12.6, official GPT-SoVITS v2Pro pipeline, `cryptography` Ed25519, `sounddevice`/PortAudio, Vue 3, Vitest, Python `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-31-gpt-sovits-gpu-runtime-design.md`

---

## Task 1: Runtime Contract, Signature Verification, and Atomic Installation

**Files:**
- Create: `backend/runtime/__init__.py`
- Create: `backend/runtime/manifest.py`
- Create: `backend/runtime/registry.py`
- Create: `backend/runtime/installer.py`
- Create: `backend/runtime/keys.py`
- Create: `tests/test_gpu_runtime_contract.py`
- Modify: `backend/voice/storage.py`
- Modify: `requirements.txt`

**Interfaces:**
- `RuntimeManifest.from_dict(payload)` / `to_dict()`
- `RuntimeVerifier.verify_directory(path, allow_unsigned=False)`
- `RuntimeRegistry.find_compatible(model_version)` / `status()`
- `RuntimeInstaller.install_zip(path, progress=None)` / `install_directory(path, progress=None)`

- [x] Write failing tests for platform, engine API, relative paths, Ed25519 signatures, hashes, ZIP traversal, unsigned production rejection, development override, disk-space errors, and atomic rollback.
- [x] Run `python3 -m unittest tests.test_gpu_runtime_contract -v` and record RED.
- [x] Implement strict runtime manifests and embedded public-key verification. Tests generate ephemeral signing keys; no private release key enters Git.
- [x] Extend storage with `voice_state` and configurable runtime root while preserving existing defaults.
- [x] Implement staging, safe ZIP extraction, directory import, progress, and atomic replacement.
- [x] Run focused and full backend tests.
- [x] Commit: `feat: add signed gpu runtime installation`

## Task 2: Token-Authenticated Sidecar Client and GPU Runtime State Machine

**Files:**
- Create: `backend/runtime/client.py`
- Create: `backend/runtime/manager.py`
- Create: `tests/fixtures/fake_gpu_sidecar.py`
- Create: `tests/test_gpu_runtime_manager.py`

**Interfaces:**
- `SidecarClient.health()` / `load_voice()` / `synthesize()` / `cancel()` / `shutdown()`
- `GpuRuntimeManager.prepare(record)` / `synthesize(text, ...)` / `cancel()` / `shutdown()`
- States: `missing|stopped|starting|ready|busy|stopping|failed`

- [x] Write failing tests using a Python fake sidecar for random token authentication, loopback handshake, startup timeout, one restart, structured CUDA errors, cancellation, process-tree cleanup, and no process while idle.
- [x] Run focused tests and record RED.
- [x] Implement HTTP streaming client with bounded reads and structured errors.
- [x] Implement the locked state machine, random port/token, safe subprocess environment, log redaction, metrics, and idempotent shutdown.
- [x] Verify fake-sidecar tests and full backend suite.
- [x] Commit: `feat: manage authenticated gpu speech sidecar`

## Task 3: Streaming PCM Player, Voice Health, and Personalized Speech Service

**Files:**
- Create: `backend/services/streaming_audio_player.py`
- Create: `backend/services/personalized_speech_service.py`
- Create: `backend/voice/health.py`
- Create: `tests/test_personalized_speech_service.py`
- Modify: `backend/voice/registry.py`
- Modify: `requirements.txt`

**Interfaces:**
- `StreamingAudioPlayer.play(stream, sample_rate, volume, token)` / `stop()`
- `VoiceHealthStore.get()` / `promote_ready()` / `invalidate()`
- `PersonalizedSpeechService.prepare(voice_key)` / `speak()` / `stop()` / `shutdown()`

- [x] Write failing tests for first-chunk playback, PCM validation, volume, cancellation token, late chunks, silence rejection, preview write, manifest hash update, readiness invalidation, and no silent fallback.
- [x] Run focused tests and record RED.
- [x] Implement lazy `sounddevice` loading and injectable output streams so unit tests need no audio device.
- [x] Implement health records bound to voice/runtime digests and real preview promotion.
- [x] Implement personalized prepare/speak/stop lifecycle over `GpuRuntimeManager`.
- [x] Run focused and full tests.
- [x] Commit: `feat: play gpu synthesized personalized speech`

## Task 4: Desktop API Routing and Cleanup

**Files:**
- Create: `tests/test_speech_api_routing.py`
- Modify: `backend/api_service.py`
- Modify: `main.py`

**Interfaces:**
- `choose_runtime_source(kind)`
- `start_runtime_install(request)` / `get_runtime_job(job_id)`
- `get_gpu_runtime_status()`
- `prepare_voice(voice_key)` / `preview_voice(voice_key, text)`
- `release_personalized_voice()`
- `speak_text(text, voice_uri, rate, volume, voice_key="")`

- [x] Write failing routing tests proving `system:` only reaches SAPI and `pack:` only reaches the GPU service.
- [x] Add runtime install jobs and structured error mapping.
- [x] Wire runtime/voice/personalized services while keeping PyTorch/CUDA imports in the sidecar process.
- [x] Extend cleanup to cancel playback and terminate the GPU sidecar.
- [x] Run all backend tests.
- [x] Commit: `feat: route personalized voices through gpu runtime`

## Task 5: Frontend Preparation Flow and GPU Runtime UI

**Files:**
- Modify: `frontend/src/api/bridge.js`
- Modify: `frontend/src/services/speechService.js`
- Modify: `frontend/src/services/speechService.spec.js`
- Modify: `frontend/src/components/SpeechToolbar.vue`
- Modify: `frontend/src/components/SpeechToolbar.spec.js`
- Modify: `frontend/src/components/VoiceImportModal.vue`
- Modify: `frontend/src/components/VoiceImportModal.spec.js`
- Modify: `frontend/src/components/DanmuPanel.vue`
- Modify: `frontend/src/components/DanmuPanel.spec.js`

- [x] Write failing tests proving the complete `voice_key` reaches the backend, enabling a pack waits for GPU preparation, failures stay disabled, and system voice behavior remains unchanged.
- [x] Add bridge methods for runtime install, status, prepare, preview, and release.
- [x] Add `loading_gpu`, `warming`, `gpu_error` states and preserve queue semantics.
- [x] Add runtime ZIP/directory/data-root controls to step 4 and show GPU, runtime, peak VRAM, and first-chunk metrics.
- [x] Ensure `runtime_required` packs are management-visible and `ready` packs appear in the selector.
- [x] Run all frontend tests and production build.
- [x] Commit: `feat: add gpu voice preparation experience`

## Task 6: Pinned Official GPT-SoVITS GPU Sidecar

**Files:**
- Create: `runtime/gpt_sovits_gpu/sidecar.py`
- Create: `runtime/gpt_sovits_gpu/protocol.py`
- Create: `runtime/gpt_sovits_gpu/requirements-runtime.lock`
- Create: `runtime/gpt_sovits_gpu/PINNED_GPT_SOVITS_COMMIT`
- Create: `runtime/gpt_sovits_gpu/README.md`
- Create: `tests/test_gpu_sidecar_protocol.py`

- [ ] Pin a verified official GPT-SoVITS commit and document upstream license/source.
- [ ] Write protocol tests with a fake TTS pipeline for authentication, allowed-root paths, Japanese requests, PCM chunks, cancellation, and shutdown.
- [ ] Implement the FastAPI sidecar wrapper without exposing official arbitrary-path/control endpoints.
- [ ] Load v2Pro/v2ProPlus custom weights with CUDA FP16 and fixed batch size 1.
- [ ] Cache reference features where supported and emit metrics without logging protected content.
- [ ] Run protocol tests in the lightweight development environment.
- [ ] Commit: `feat: add pinned gpt-sovits gpu sidecar`

## Task 7: Windows CU126 Runtime Builder and Packaging Boundaries

**Files:**
- Create: `scripts/build_gpu_runtime.ps1`
- Create: `scripts/sign_runtime_manifest.py`
- Create: `scripts/verify_gpu_runtime.ps1`
- Create: `tests/test_gpu_runtime_packaging.py`
- Modify: `scripts/build_windows.ps1`
- Modify: `.gitignore`
- Modify: `README.md`

- [ ] Write failing packaging tests for separate ZIP output, pinned source, Python 3.10, CUDA 12.6 PyTorch, required base models, Open JTalk, FFmpeg, manifest hashes/signature, and exclusion from the main EXE.
- [ ] Implement a resumable Windows builder under a user-selected data/build directory with disk checks after each phase.
- [ ] Clone only the pinned upstream commit and verify it before dependency/model installation.
- [ ] Build the runtime manifest, sign when a release key is supplied, and support an explicit unsigned development artifact.
- [ ] Ensure the main EXE packaging excludes `.runtime-dev`, CUDA DLLs, PyTorch, weights, and protected voice files.
- [ ] Document runtime installation and GPU modes.
- [ ] Run packaging tests.
- [ ] Commit: `build: add separate gpt-sovits gpu runtime package`

## Task 8: Real RTX 3060 Acceptance, Final Verification, and Integration

**Files:**
- Create: `scripts/test_real_gpu_voice.py`
- Create: `docs/gpu-runtime-benchmark.md`
- Modify: `docs/superpowers/plans/2026-08-31-gpt-sovits-gpu-runtime.md`

- [ ] Build or install the development GPU runtime without placing it on the system disk by default.
- [ ] Import and prepare the actual `voice/haibara_jp` assets.
- [ ] Synthesize fixed Japanese preview and typical short danmu; save only authorized local preview output.
- [ ] Record startup, load, warm-up, first PCM, whole utterance, peak VRAM, and post-shutdown process/VRAM release.
- [ ] Confirm audible output manually is left to the user, while automated checks enforce valid non-silent PCM and duration.
- [ ] Run `python3 -m unittest discover -s tests -v`.
- [ ] Run `cd frontend && npm test`.
- [ ] Run `cd frontend && npm run build`.
- [ ] Run `git diff --check` and packaging boundary checks.
- [ ] Start the hot-reload UI preview and verify the GPU controls fit 1000×720.
- [ ] Mark the plan complete, commit benchmark/results, merge locally to `master`, and rerun all tests on the merged tree.

## Completion Gate

Do not call the GPU version complete unless:

- A `pack:` voice is routed end-to-end to the GPU service.
- The real `haibara-jp` weights load on RTX 3060 in CUDA FP16.
- A valid non-silent Japanese preview is produced.
- Warm first-chunk latency is measured and is at most 3 seconds.
- GPU sidecar exits and relinquishes the app-owned CUDA process after release.
- System speech still works when the runtime is absent or broken.
- Runtime remains separate from the main EXE and protected voice files remain outside Git.
- Full backend/frontend/build/package verification passes on the merged commit.
