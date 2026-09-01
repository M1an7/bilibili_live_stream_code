# AIVMX CPU Desktop Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the desktop app safely import one multilingual Style-Bert-VITS2 `.aivmx`, preview and synthesize Chinese through a CPU-only sidecar, and route live danmu to it without creating a CUDA context.

**Architecture:** The desktop process performs bounded metadata parsing and atomic installation without loading ONNX. A separately installed, signed Windows CPU runtime owns `onnxruntime`, Style-Bert-VITS2, Chinese G2P/BERT assets, model validation, synthesis, and process memory; the existing queue routes `aivmx:` keys explicitly and never falls back to system or GPU speech.

**Tech Stack:** Python 3.12 desktop backend, Python 3.11 CPU sidecar, ONNX Runtime CPU, Style-Bert-VITS2 2.7.0-compatible source, aivmlib 1.2.2-compatible metadata, Vue 3, Vitest, Python `unittest`, PyInstaller/PowerShell packaging.

**Spec:** `docs/superpowers/specs/2026-08-31-multilingual-aivmx-cpu-voice-design.md`

## Global Constraints

- CPU mode must set `CUDA_VISIBLE_DEVICES=-1`; every model session must use only `CPUExecutionProvider`; any CUDA/TensorRT provider is a launch failure.
- The imported model remains one unmodified `.aivmx`; the public repository and release never contain the user's model or source audio.
- Chinese danmu remains Chinese and is synthesized with `Languages.ZH`; there is no translation or silent engine fallback.
- The user confirms training, synthesis, and public-livestream rights at install time.
- The shared runtime is stored separately from the main EXE and voice file and can live on the configured data disk.
- Stopping CPU personalized speech terminates the sidecar and releases its RAM; both idle and active GPU-memory increments must remain 0 MB.
- Existing system voices and GPT-SoVITS GPU packs remain compatible.
- The existing worktree contains approved uncommitted GPU work; stage or commit nothing outside the exact files changed by this plan.

---

### Task 1: Safe AIVMX Metadata Contract and Atomic Registry

**Files:**
- Create: `backend/aivmx/__init__.py`
- Create: `backend/aivmx/contract.py`
- Create: `backend/aivmx/protobuf.py`
- Create: `backend/aivmx/registry.py`
- Create: `backend/aivmx/jobs.py`
- Modify: `backend/voice/storage.py`
- Test: `tests/test_aivmx_registry.py`

**Interfaces:**
- Produces: `AivmxContractError(code, message, field)`, `AivmxMetadataReader.read(path) -> AivmxMetadata`, `AivmxVoiceRegistry.install(source, permissions_confirmed, progress=None) -> AivmxVoiceRecord`, and `AivmxInstallJobManager`.
- Produces stable keys in the form `aivmx:<model-uuid>:<style-id>` and an installed `install.json` containing source SHA-256, timestamp, permission confirmation, manifest summary, and the unmodified model filename.

- [x] **Step 1: Write failing contract and registry tests**

  Build a minimal ONNX `ModelProto` wire payload in the test with field 14 metadata entries for `aivm_manifest`, `aivm_hyper_parameters`, and `aivm_style_vectors`. Assert acceptance of manifest version `1.0`, architecture `Style-Bert-VITS2`, model format `ONNX`, `zh-CN`, a non-empty license, UUIDs, and at least one style. Assert rejection of JP-Extra, absent Chinese support, absent license, bad UUID, links, wrong extension, files larger than the configured limit, and unconfirmed rights. Assert a failed replacement preserves the previously installed model.

- [x] **Step 2: Run the focused test and verify RED**

  Run `python3 -m unittest tests.test_aivmx_registry -v`. Expected: import failure because `backend.aivmx` does not exist.

- [x] **Step 3: Implement the bounded protobuf reader and contracts**

  Parse only top-level protobuf wire fields and skip the large graph field by seeking; collect field-14 key/value metadata entries with strict byte and nesting limits. Decode and validate the three AIVM metadata values with standard-library JSON/base64 and expose only immutable, display-safe fields.

- [x] **Step 4: Implement streamed hashing, staged copy, and atomic registry replacement**

  Copy in 1 MiB chunks into `<data>/aivmx-voices/.staging`, verify the staged hash, write `install.json`, then use incoming/backup directories and `os.replace`. Scan only UUID-named directories that are real directories and contain real regular files.

- [x] **Step 5: Run Task 1 tests and the existing voice tests**

  Run `python3 -m unittest tests.test_aivmx_registry tests.test_voice_storage_and_manifest tests.test_voice_registry_and_jobs -v`. Expected: PASS.

### Task 2: Signed CPU Runtime Contract, Installer, and Registry

**Files:**
- Create: `backend/cpu_runtime/__init__.py`
- Create: `backend/cpu_runtime/manifest.py`
- Create: `backend/cpu_runtime/registry.py`
- Create: `backend/cpu_runtime/installer.py`
- Create: `backend/cpu_runtime/jobs.py`
- Test: `tests/test_cpu_runtime_contract.py`

**Interfaces:**
- Produces: `CpuRuntimeManifest`, `CpuRuntimeVerifier.verify_directory(path)`, `CpuRuntimeRegistry.find_compatible('Style-Bert-VITS2', 'zh-CN')`, `CpuRuntimeInstaller`, and `CpuRuntimeInstallJobManager`.
- Manifest engine is exactly `style-bert-vits2-onnx-cpu`, API version `1`, `gpu: false`, provider list exactly `['CPUExecutionProvider']`, and includes pinned Style-Bert-VITS2/aivmlib commits plus SHA-256 for every runtime file.

- [x] **Step 1: Write failing strict-manifest and atomic-install tests**

  Assert valid signed and explicit development-unsigned directories register. Reject CUDA providers, `gpu: true`, PyTorch/CUDA/cuDNN file names, incorrect platform/API/commit, links, undeclared/changed files, traversal ZIP members, insufficient space, and failed replacement rollback.

- [x] **Step 2: Run the focused test and verify RED**

  Run `python3 -m unittest tests.test_cpu_runtime_contract -v`. Expected: import failure because `backend.cpu_runtime` does not exist.

- [x] **Step 3: Implement CPU-specific manifest verification and installation**

  Reuse the release Ed25519 public key and canonical JSON rules without weakening the existing GPU contract. Hash files concurrently in bounded batches and install to `<runtime-root>/cpu/<runtime-id>` through same-volume staging.

- [x] **Step 4: Run Task 2 tests**

  Run `python3 -m unittest tests.test_cpu_runtime_contract -v`. Expected: PASS.

### Task 3: CPU Sidecar Protocol and Real Style-Bert-VITS2 ONNX Engine

**Files:**
- Create: `runtime/style_bert_vits2_cpu/__init__.py`
- Create: `runtime/style_bert_vits2_cpu/protocol.py`
- Create: `runtime/style_bert_vits2_cpu/sidecar.py`
- Create: `runtime/style_bert_vits2_cpu/PINNED_STYLE_BERT_VITS2_COMMIT`
- Create: `runtime/style_bert_vits2_cpu/PINNED_AIVMLIB_COMMIT`
- Create: `runtime/style_bert_vits2_cpu/requirements-windows.lock`
- Test: `tests/test_cpu_sidecar_protocol.py`

**Interfaces:**
- The HTTP surface remains `/v1/health`, `/v1/voices/load`, `/v1/tts`, `/v1/cancel`, `/v1/shutdown` with the existing framed PCM protocol.
- `CpuVoiceEngine.load_voice({'model_uuid': str, 'style_id': int})` re-hashes the registered file, reads metadata with official aivmlib, loads ONNX with `load_external_data=False`, rejects every external tensor, preloads only Chinese BERT/tokenizer, and warms the selected style.
- `CpuVoiceEngine.synthesize` accepts Chinese text, clamps speed to `0.5..2.0`, calls `Languages.ZH`, and returns 16-bit mono PCM at the model sample rate.

- [x] **Step 1: Write failing protocol tests with injected fake model pipelines**

  Assert bearer authentication, loopback-only binding, path confinement, hash recheck, UUID/style validation, Chinese-only routing, PCM framing, cancellation, deterministic shutdown, provider reporting, zero VRAM metrics, external-data rejection, and structured errors.

- [x] **Step 2: Run focused tests and verify RED**

  Run `python3 -m unittest tests.test_cpu_sidecar_protocol -v`. Expected: import failure because the CPU sidecar is absent.

- [x] **Step 3: Implement protocol and lazily imported real engine**

  Keep tests independent from heavy inference packages by injecting metadata/model factories. The production factory imports `aivmlib`, `onnx`, `onnxruntime`, `numpy`, and pinned Style-Bert-VITS2 only after the sidecar process starts; it sets the model's ONNX flag for the `.aivmx` suffix and preloads the local `bert/chinese-roberta-wwm-ext-large-onnx` directory.

- [x] **Step 4: Run Task 3 tests**

  Run `python3 -m unittest tests.test_cpu_sidecar_protocol -v`. Expected: PASS.

### Task 4: CPU Runtime Manager and Multilingual Speech Service

**Files:**
- Create: `backend/cpu_runtime/manager.py`
- Create: `backend/services/aivmx_speech_service.py`
- Modify: `backend/runtime/client.py`
- Test: `tests/fixtures/fake_cpu_sidecar.py`
- Test: `tests/test_cpu_runtime_manager.py`
- Test: `tests/test_aivmx_speech_service.py`

**Interfaces:**
- `CpuRuntimeManager.prepare(record)`, `load_voice(request)`, `synthesize(text, language='zh', **options)`, `cancel()`, `shutdown()`, and `status()` mirror the proven GPU lifecycle but use CPU-only environment and metrics.
- `AivmxSpeechService.prepare/preview/speak/stop/shutdown` owns readiness and playback without changing GPU voice health state.

- [x] **Step 1: Write failing lifecycle and service tests**

  Assert no process before prepare; random stdin token; random loopback port; `CUDA_VISIBLE_DEVICES=-1`; four-thread default; offline environment; CPU-only health; exactly one crash restart; Chinese preview; non-silent PCM; hash/runtime invalidation; no system/GPU fallback; and process termination on disable.

- [x] **Step 2: Run focused tests and verify RED**

  Run `python3 -m unittest tests.test_cpu_runtime_manager tests.test_aivmx_speech_service -v`. Expected: missing class/module failures.

- [x] **Step 3: Implement manager and service**

  Reuse the framed client after changing user-facing strings from GPU-specific to neutral “语音运行时”. Record `first_pcm_ms`, warmup, RSS/peak RSS, providers, and fixed `vram_mb: 0`; fail preparation if the sidecar reports any non-CPU execution provider.

- [x] **Step 4: Run Task 4 tests**

  Run `python3 -m unittest tests.test_cpu_runtime_manager tests.test_aivmx_speech_service -v`. Expected: PASS.

### Task 5: Desktop API and Explicit Three-Way Speech Routing

**Files:**
- Modify: `backend/api_service.py`
- Test: `tests/test_speech_api_routing.py`
- Test: `tests/test_aivmx_api.py`

**Interfaces:**
- Adds `choose_voice_source('aivmx')`, `inspect_aivmx`, `start_aivmx_install`, `get_aivmx_job`, `list_aivmx_voices`, CPU runtime install/status methods, and `prepare_aivmx_voice`/`preview_aivmx_voice`/`release_aivmx_voice`.
- `speak_text` routes `system:` only to system speech, `aivmx:` only to `AivmxSpeechService`, and `pack:` only to GPT-SoVITS.

- [x] **Step 1: Add failing API tests**

  Assert all three prefixes reach exactly one engine, unknown personalized prefixes are rejected, AIVMX failures remain structured, stop cancels all engines, and app close shuts both sidecars down.

- [x] **Step 2: Run focused API tests and verify RED**

  Run `python3 -m unittest tests.test_speech_api_routing tests.test_aivmx_api -v`. Expected: missing AIVMX API behavior.

- [x] **Step 3: Wire registries, jobs, managers, and services into `ApiService`**

  Preserve the existing configured runtime root and create its `cpu` child. Ensure switching runtime root shuts down both services and rolls configuration back atomically if either reinitialization fails.

- [x] **Step 4: Run Task 5 tests**

  Run `python3 -m unittest tests.test_speech_api_routing tests.test_aivmx_api -v`. Expected: PASS.

### Task 6: AIVMX Import UI, CPU Status, and Queue Selection

**Files:**
- Create: `frontend/src/components/AivmxImportPanel.vue`
- Create: `frontend/src/components/AivmxImportPanel.spec.js`
- Modify: `frontend/src/components/VoiceImportModal.vue`
- Modify: `frontend/src/components/VoiceImportModal.spec.js`
- Modify: `frontend/src/components/SpeechToolbar.vue`
- Modify: `frontend/src/components/SpeechToolbar.spec.js`
- Modify: `frontend/src/services/speechService.js`
- Modify: `frontend/src/services/speechService.spec.js`
- Modify: `frontend/src/api/bridge.js`

**Interfaces:**
- Import modal exposes two modes: `实时 CPU 音色` and `高质量 GPU 音色`; CPU mode chooses one `.aivmx`, shows name/creator/languages/license/styles/hash and requires the rights checkbox.
- Selectable voice groups are `系统音色`, `实时 CPU · 零显存`, and `高质量 GPU`; CPU states are `loading_cpu`, `warming`, and `cpu_error`.

- [x] **Step 1: Write failing component and service tests**

  Assert one-file selection, metadata display, rights gate, installation progress, CPU runtime-required state, CPU preview, `aivmx:` selection persistence, enable-time warmup, Chinese queue routing, disable-time release, and no GPU wording for CPU failures.

- [x] **Step 2: Run focused frontend tests and verify RED**

  Run `npm test -- --run src/components/AivmxImportPanel.spec.js src/components/VoiceImportModal.spec.js src/components/SpeechToolbar.spec.js src/services/speechService.spec.js`. Expected: missing component/behavior failures.

- [x] **Step 3: Implement the smallest UI and service changes**

  Keep the existing GPU wizard intact under its tab. CPU mode never asks for a reference WAV/config/weight file and reports `CPU 推理 · 显存 0 MB`; it displays measured RAM and first-audio latency when available.

- [x] **Step 4: Run Task 6 and all frontend tests**

  Run `npm test -- --run`. Expected: PASS.

### Task 7: Reproducible Windows CPU Runtime Builder and Offline Verification

**Files:**
- Create: `scripts/build_cpu_runtime.ps1`
- Create: `scripts/verify_cpu_runtime.ps1`
- Create: `scripts/test_real_cpu_voice.py`
- Modify: `scripts/sign_runtime_manifest.py`
- Modify: `scripts/build_windows.ps1`
- Test: `tests/test_cpu_runtime_packaging.py`
- Modify: `README.md`

**Interfaces:**
- Builder accepts `-OutputRoot`, `-CacheRoot`, and `-Python`; pins upstream commits, installs only CPU ONNX dependencies, downloads only the Chinese ONNX BERT/tokenizer, excludes Torch/CUDA/cuDNN and user models, writes licenses/source offer, manifests every file, and creates a ZIP outside the runtime directory.
- Real test accepts runtime directory plus `.aivmx`, synthesizes fixed Chinese lines twice, writes WAV, reports cold/hot latency, RSS/peak RSS/providers/VRAM, and fails if providers are not CPU-only.

- [x] **Step 1: Write failing packaging assertions**

  Assert pinned hashes/commits, CPU-only dependency lock, Chinese-only public model assets, no protected voice paths, separate runtime output, signed verification defaults, and main EXE exclusions.

- [x] **Step 2: Run packaging tests and verify RED**

  Run `python3 -m unittest tests.test_cpu_runtime_packaging -v`. Expected: missing builder files.

- [x] **Step 3: Implement builder, verifier, benchmark, and documentation**

  Use same-volume temporary construction, validate free space before downloads, preserve MIT/AGPL/third-party licenses, and prevent the main PyInstaller command from collecting `onnxruntime`, BERT, upstream runtime source, or any `.aivmx`.

- [x] **Step 4: Run packaging tests**

  Run `python3 -m unittest tests.test_cpu_runtime_packaging tests.test_windows_packaging -v`. Expected: PASS.

### Task 8: Real Local Import, Synthesis, Desktop Launch, and Regression

**Files:**
- Create: `docs/cpu-runtime-benchmark.md`
- Modify: `docs/superpowers/plans/2026-09-01-aivmx-cpu-desktop-integration.md`

**Interfaces:**
- Uses the private local file `voice/haibara_sbv2/haibara_jp_e100_s11500.aivmx` only as an external test input; it is never staged or committed.

- [x] **Step 1: Run complete automated suites**

  Run `python3 -m unittest discover -s tests -v` in an environment that permits loopback sockets, then `npm test -- --run` and `npm run build`. Expected: all tests/build pass with no new warnings.

- [x] **Step 2: Build or install the developer CPU runtime on the data disk**

  Run the PowerShell builder with caches and output outside the system drive when configured. Verify its signature/file set and confirm the installed Python has `onnxruntime` CPU but no `torch`, CUDA, cuDNN, or GPU provider package.

- [x] **Step 3: Import and synthesize the private AIVMX**

  Run `scripts/test_real_cpu_voice.py` with the installed runtime and local AIVMX. Verify non-silent Chinese WAV, provider `CPUExecutionProvider`, sidecar VRAM 0 MB, and record cold/hot latency plus RSS. Treat median above 1 second but at or below 3 seconds as accepted fallback; report that it misses the ideal target rather than hiding it.

- [x] **Step 4: Start the desktop development build for user inspection**

  Launch the existing Vue/pywebview development workflow with isolated `BILILIVE_DATA_HOME`, import the AIVMX through the new button, preview the fixed Chinese line, then leave the app running for the user to test live UI and simulated danmu.

- [x] **Step 5: Mark plan checkboxes with evidence**

  Record exact test counts, runtime build path, model SHA-256, providers, latency, RSS, and VRAM in `docs/cpu-runtime-benchmark.md`. Do not mark native Windows/OBS endurance checks complete unless they were actually run.
