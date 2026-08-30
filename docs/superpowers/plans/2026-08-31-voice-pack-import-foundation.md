# Voice Pack Import Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe, testable desktop workflow that accepts GPT-SoVITS v2Pro/v2ProPlus training results, builds the standard voice-pack layout, installs it in a non-selectable `runtime_required` state, and exposes an import wizard beside the speech voice selector.

**Architecture:** A new `backend.voice` package owns application-data paths, manifest parsing, structural validation, staging, atomic installation, and background import jobs. The pywebview API exposes only structured job and catalog methods; the Vue wizard gathers files and polls jobs. This milestone never deserializes PyTorch weights and never advertises an imported pack as usable: the later signed CPU-runtime milestone performs restricted weight inspection, preview generation, and promotion to `ready`.

**Tech Stack:** Python 3.12 standard library (`dataclasses`, `hashlib`, `json`, `pathlib`, `shutil`, `threading`, `uuid`, `wave`), pywebview API, Vue 3, Vitest, Python `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-31-voice-pack-import-runtime-design.md`

## Global Constraints

- Voice data lives under `%LOCALAPPDATA%\BiliLiveTool` on Windows and the platform data directory in development; it never lives beside the EXE.
- This milestone accepts only model versions `v2Pro` and `v2ProPlus` and engine API version `1`.
- A voice-specific package contains no Python, DLL, EXE, script, plugin, or install command.
- `voice_id` matches `^[a-z0-9]+(?:-[a-z0-9]+)*$`; display names may contain Chinese, Japanese, and spaces.
- Every imported file is copied into a random staging directory before validation and atomically moved into `voices` only after structural validation succeeds.
- A pack has at most 4096 members, each file is at most 2 GiB, total unpacked size is at most 4 GiB, and only the contract file types are accepted.
- Symlinks, reparse points, absolute manifest paths, `..`, path traversal, hash mismatches, missing authorization files, and unsupported model versions are rejected.
- PyTorch weights are opaque bytes in this milestone. They are not loaded by the main process and remain `runtime_required` until the future restricted CPU runtime validates and previews them.
- Only packs with health state `ready` may appear as selectable personalized voices. `runtime_required`, `invalid`, and `missing` packs are management-only entries.
- System speech remains available and unchanged when import, validation, or runtime operations fail.

---

### Task 1: Application Data Paths and Manifest Contract

**Files:**
- Create: `backend/voice/__init__.py`
- Create: `backend/voice/storage.py`
- Create: `backend/voice/manifest.py`
- Create: `tests/test_voice_storage_and_manifest.py`

**Interfaces:**
- Produces: `VoiceStoragePaths.resolve(platform_name=None, env=None, home=None) -> VoiceStoragePaths`
- Produces: `VoiceStoragePaths.ensure() -> VoiceStoragePaths`
- Produces: `VoiceManifest.from_dict(payload: dict) -> VoiceManifest`
- Produces: `VoiceManifest.to_dict() -> dict`
- Produces: `VoiceManifest.relative_files() -> dict[str, str]`
- Produces: `VoiceContractError(code: str, message: str, field: str = "")`

- [ ] **Step 1: Write failing storage and manifest tests**

```python
def test_windows_storage_uses_local_app_data(self):
    paths = VoiceStoragePaths.resolve(
        platform_name="win32",
        env={"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"},
    )
    self.assertEqual(paths.root, Path(r"C:\Users\tester\AppData\Local") / "BiliLiveTool")

def test_manifest_rejects_path_traversal(self):
    payload = valid_manifest_payload()
    payload["models"]["gpt"] = "../outside.ckpt"
    with self.assertRaisesRegex(VoiceContractError, "相对路径"):
        VoiceManifest.from_dict(payload)

def test_manifest_requires_japanese_output_for_this_pack(self):
    payload = valid_manifest_payload()
    payload["supported_output_languages"] = []
    with self.assertRaisesRegex(VoiceContractError, "输出语言"):
        VoiceManifest.from_dict(payload)
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m unittest tests.test_voice_storage_and_manifest -v`

Expected: FAIL because `backend.voice.storage` and `backend.voice.manifest` do not exist.

- [ ] **Step 3: Implement path resolution and strict manifest parsing**

`storage.py` defines immutable paths for `voices`, `runtimes`, `cache/speech`, `staging`, and `logs`. `resolve()` uses `BILILIVE_DATA_HOME` as a test/development override, `%LOCALAPPDATA%` on Windows, `~/Library/Application Support` on macOS, and `$XDG_DATA_HOME` or `~/.local/share` on Linux.

`manifest.py` defines constants and validates all required fields before constructing the dataclass:

```python
VOICE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SUPPORTED_MODEL_VERSIONS = {"v2Pro", "v2ProPlus"}
EXPECTED_ENGINE = "gpt-sovits-cpu"
EXPECTED_ENGINE_API_VERSION = 1

@dataclass(frozen=True)
class VoiceManifest:
    schema_version: int
    voice_id: str
    display_name: str
    engine: str
    engine_api_version: int
    model_version: str
    source_language: str
    supported_output_languages: tuple[str, ...]
    models: dict[str, str]
    reference_audio: str
    reference_text: str
    preview_audio: str | None
    license_file: str
    usage: tuple[str, ...]
    created_at: str
    files: dict[str, str]
```

Every contract path is normalized with `PurePosixPath`; absolute paths, empty components, `.` and `..` are rejected. `usage` must contain `ai_training`, `synthetic_speech`, and `public_livestream`. `source_language` and output languages accept `ja`, while the data model remains capable of holding future approved languages.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `python -m unittest tests.test_voice_storage_and_manifest -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit the contract layer**

```bash
git add backend/voice tests/test_voice_storage_and_manifest.py
git commit -m "feat: add voice pack storage and manifest contract"
```

---

### Task 2: Structural Validator and Training-Result Builder

**Files:**
- Create: `backend/voice/validator.py`
- Create: `backend/voice/builder.py`
- Create: `tests/test_voice_pack_builder.py`
- Modify: `backend/voice/__init__.py`

**Interfaces:**
- Consumes: `VoiceStoragePaths`, `VoiceManifest`, `VoiceContractError`
- Produces: `VoicePackValidator.validate_directory(path: Path) -> VoiceValidationResult`
- Produces: `VoicePackBuilder.build(request: VoiceBuildRequest, progress=None, cancelled=None) -> BuiltVoicePack`
- Produces: `VoiceBuildRequest(voice_id, display_name, model_version, gpt_path, sovits_path, reference_audio_path, reference_text, license_path, source_language="ja", supported_output_languages=("ja",))`
- Produces: `VoiceValidationResult(valid, health, code, message, manifest)`
- Produces: `BuiltVoicePack(staging_path, manifest, validation)`

- [ ] **Step 1: Write failing tests for a valid build and rejected inputs**

```python
def test_builds_standard_pack_with_real_hashes(self):
    request = self.make_request()
    built = self.builder.build(request)
    manifest = json.loads((built.staging_path / "manifest.json").read_text("utf-8"))
    self.assertEqual(manifest["models"]["gpt"], "model/gpt.ckpt")
    self.assertTrue(manifest["files"]["model/gpt.ckpt"].startswith("sha256:"))
    self.assertEqual(len(manifest["files"]["model/gpt.ckpt"]), 71)
    self.assertEqual(built.validation.health, "runtime_required")

def test_rejects_missing_license(self):
    request = replace(self.make_request(), license_path=self.root / "missing.txt")
    with self.assertRaisesRegex(VoiceContractError, "授权"):
        self.builder.build(request)

def test_rejects_symlinked_source(self):
    link = self.root / "linked.pth"
    link.symlink_to(self.sovits)
    request = replace(self.make_request(), sovits_path=link)
    with self.assertRaisesRegex(VoiceContractError, "符号链接"):
        self.builder.build(request)

def test_rejects_non_pcm_wav_without_touching_source(self):
    self.reference.write_bytes(b"not-a-wave")
    with self.assertRaisesRegex(VoiceContractError, "WAV"):
        self.builder.build(self.make_request())
```

- [ ] **Step 2: Run the builder tests and verify RED**

Run: `python -m unittest tests.test_voice_pack_builder -v`

Expected: FAIL because validator and builder modules do not exist.

- [ ] **Step 3: Implement structural validation**

`VoicePackValidator` performs two passes: contract/path/type/size checks, then streaming SHA-256 checks. The allowed installed files are exactly:

```python
ALLOWED_EXACT = {
    "manifest.json", "model/gpt.ckpt", "model/sovits.pth",
    "reference.wav", "reference.txt", "preview.wav", "LICENSE.txt",
}
```

`preview.wav` is optional while health is `runtime_required`. The validator returns `health="runtime_required"` after every structural and hash check passes; it never imports `torch` and never opens `.ckpt` or `.pth` as structured objects.

- [ ] **Step 4: Implement the training-result builder**

The builder validates all source paths before copying, creates `staging/<uuid4>/`, copies weights as opaque streams, validates the reference with the standard-library `wave` reader, writes UTF-8 `reference.txt`, copies the license, computes hashes, writes `manifest.json`, and runs the validator on the staged package. Progress callbacks use stages `prepare`, `copy`, `hash`, and `validate` with integer percentages. Cancellation raises `VoiceJobCancelled` and removes only the random staging directory created by that build.

- [ ] **Step 5: Run builder tests and verify GREEN**

Run: `python -m unittest tests.test_voice_pack_builder -v`

Expected: all tests PASS and test temporary directories are removed.

- [ ] **Step 6: Commit builder and validation**

```bash
git add backend/voice tests/test_voice_pack_builder.py
git commit -m "feat: build and validate staged voice packs"
```

---

### Task 3: Registry, Background Jobs, and Desktop API

**Files:**
- Create: `backend/voice/registry.py`
- Create: `backend/voice/jobs.py`
- Create: `tests/test_voice_registry_and_jobs.py`
- Modify: `backend/api_service.py`
- Modify: `main.py`

**Interfaces:**
- Consumes: `VoicePackBuilder`, `VoicePackValidator`, `VoiceStoragePaths`
- Produces: `VoicePackRegistry.install_staged(built: BuiltVoicePack) -> VoicePackRecord`
- Produces: `VoicePackRegistry.list_packs() -> list[dict]`
- Produces: `VoiceJobManager.start_build(request: dict) -> str`
- Produces: `VoiceJobManager.get(job_id: str) -> dict`
- Produces API: `start_voice_pack_build(request)`, `get_voice_job(job_id)`, `list_voice_packs()`

- [ ] **Step 1: Write failing registry and job tests**

```python
def test_atomic_install_is_management_only_until_runtime_ready(self):
    record = self.registry.install_staged(self.built)
    self.assertEqual(record.health, "runtime_required")
    self.assertFalse(record.selectable)
    self.assertTrue((self.paths.voices / record.voice_id / "manifest.json").is_file())

def test_failed_update_preserves_existing_pack(self):
    first = self.registry.install_staged(self.built)
    with self.assertRaises(VoiceContractError):
        self.registry.install_staged(self.invalid_update)
    self.assertEqual(self.registry.get(first.voice_id).manifest.created_at, first.manifest.created_at)

def test_background_job_reports_structured_completion(self):
    job_id = self.jobs.start_build(self.request_dict)
    result = wait_for_terminal_job(self.jobs, job_id)
    self.assertEqual(result["status"], "completed")
    self.assertEqual(result["result"]["health"], "runtime_required")
```

- [ ] **Step 2: Run registry/job tests and verify RED**

Run: `python -m unittest tests.test_voice_registry_and_jobs -v`

Expected: FAIL because registry and job manager do not exist.

- [ ] **Step 3: Implement atomic registry behavior**

The registry validates staged content again, moves it to `voices/.incoming-<uuid>`, then renames to `voices/<voice-id>`. Updates move the old directory to `.backup-<uuid>` and restore it if the final rename fails. Registry records expose `voice_key="pack:<voice-id>"`, health, selectable flag, display name, model version, language, and a short mapped error. Startup scanning never follows symlinks and maps malformed packages to `health="invalid"` without preventing application startup.

- [ ] **Step 4: Implement background jobs**

`VoiceJobManager` uses daemon worker threads and a lock-protected dictionary. `start_build()` immediately returns a random UUID job ID. `get()` returns only JSON-safe fields:

```python
{
    "job_id": job_id,
    "status": "queued|running|completed|failed|cancelled",
    "stage": "prepare|copy|hash|validate|install|done",
    "progress": 0,
    "message": "",
    "result": None,
    "error": None,
}
```

Completed jobs install through the registry. Failures remove their staging directory and preserve existing installed packs.

- [ ] **Step 5: Wire ApiService and shutdown cleanup**

`ApiService.__init__` constructs paths, validator, builder, registry, and jobs. Public methods wrap exceptions as `{"code": -1, "msg": ..., "error": {"code": ...}}`; successful methods return `{"code": 0, "data": ...}`. `main.cleanup_services()` calls `api.voice_jobs.shutdown()` after stopping speech.

- [ ] **Step 6: Run backend tests and verify GREEN**

Run: `python -m unittest discover -s tests -v`

Expected: all backend tests PASS.

- [ ] **Step 7: Commit registry, jobs, and API**

```bash
git add backend/voice backend/api_service.py main.py tests/test_voice_registry_and_jobs.py
git commit -m "feat: expose background voice pack imports"
```

---

### Task 4: Frontend Bridge and Voice Catalog State

**Files:**
- Modify: `frontend/src/api/bridge.js`
- Modify: `frontend/src/services/speechService.js`
- Modify: `frontend/src/services/speechService.spec.js`

**Interfaces:**
- Consumes API: `start_voice_pack_build`, `get_voice_job`, `list_voice_packs`
- Produces bridge methods: `startVoicePackBuild(request)`, `getVoiceJob(jobId)`, `listVoicePacks()`
- Produces speech state: `systemVoices`, `voicePacks`, `selectedVoiceKey`
- Maintains compatibility: existing `voiceURI` settings migrate to `system:<voiceURI>`

- [ ] **Step 1: Add failing speech catalog tests**

```javascript
it('migrates an old system voice URI into a system voice key', async () => {
  storage.setItem('bili-live-speech-settings-v1', JSON.stringify({ voiceURI: 'Haruka' }));
  service = createSpeechService({ synth, Utterance, storage });
  await service.initialize();
  expect(service.getState().selectedVoiceKey).toBe('system:Haruka');
});

it('keeps runtime-required packs out of selectable voices', async () => {
  backend.listVoicePacks.mockResolvedValue({ code: 0, data: [
    { voice_key: 'pack:test', display_name: '测试音色', health: 'runtime_required', selectable: false },
  ] });
  await service.initialize();
  expect(service.getState().voicePacks).toHaveLength(1);
  expect(service.getState().voices.some(v => v.voiceKey === 'pack:test')).toBe(false);
});
```

- [ ] **Step 2: Run frontend tests and verify RED**

Run: `cd frontend && npm test -- src/services/speechService.spec.js`

Expected: FAIL because the state still exposes only `voiceURI`.

- [ ] **Step 3: Add bridge methods and catalog migration**

Bridge methods pass plain JSON payloads to pywebview and preserve structured errors. Speech service maps discovered system voices to `voiceKey="system:<voiceURI>"`, stores `selectedVoiceKey`, and derives the legacy system URI only when calling browser/SAPI speech. It loads pack management records through the backend but appends only `selectable && health === "ready"` packs to the voice selector.

- [ ] **Step 4: Run focused and full frontend tests**

Run: `cd frontend && npm test -- src/services/speechService.spec.js`

Run: `cd frontend && npm test`

Expected: all tests PASS.

- [ ] **Step 5: Commit bridge and catalog changes**

```bash
git add frontend/src/api/bridge.js frontend/src/services/speechService.js frontend/src/services/speechService.spec.js
git commit -m "feat: add personalized voice catalog state"
```

---

### Task 5: Import Button and Training-Result Wizard

**Files:**
- Create: `frontend/src/components/VoiceImportModal.vue`
- Create: `frontend/src/components/VoiceImportModal.spec.js`
- Modify: `frontend/src/components/SpeechToolbar.vue`
- Modify: `frontend/src/components/SpeechToolbar.spec.js`
- Modify: `frontend/src/components/DanmuPanel.vue`
- Modify: `frontend/src/components/DanmuPanel.spec.js`

**Interfaces:**
- Consumes: bridge import/job/catalog methods
- Produces event: `SpeechToolbar` emits `import-voice`
- Produces modal props: `visible`, `bridge`; events: `close`, `installed`

- [ ] **Step 1: Add failing toolbar and wizard tests**

```javascript
it('places an import button beside the voice selector', async () => {
  const wrapper = mount(SpeechToolbar, { props: { service: makeService() } });
  await wrapper.get('[data-test="import-voice"]').trigger('click');
  expect(wrapper.emitted('import-voice')).toHaveLength(1);
});

it('submits a v2Pro Japanese training-result build', async () => {
  const wrapper = mount(VoiceImportModal, { props: { visible: true, bridge } });
  await fillWizard(wrapper, validForm);
  await wrapper.get('[data-test="voice-build-submit"]').trigger('click');
  expect(bridge.startVoicePackBuild).toHaveBeenCalledWith(expect.objectContaining({
    model_version: 'v2Pro', source_language: 'ja', supported_output_languages: ['ja'],
  }));
});
```

- [ ] **Step 2: Run component tests and verify RED**

Run: `cd frontend && npm test -- src/components/SpeechToolbar.spec.js src/components/VoiceImportModal.spec.js`

Expected: FAIL because the button and modal do not exist.

- [ ] **Step 3: Add the toolbar entry point**

Add a compact `导入` button immediately after the voice select. Preserve existing enable, queue, skip, rate, volume, and development simulation controls. The select groups system and ready personalized voices with `<optgroup>` labels; runtime-required packs are not options.

- [ ] **Step 4: Implement the four-step wizard**

The modal contains:

1. GPT `.ckpt`, SoVITS `.pth`, and `v2Pro|v2ProPlus`.
2. Reference WAV, exact Japanese prompt text, and language fixed to Japanese.
3. Display name, generated/editable voice ID, authorization file, and required permission checkbox.
4. Summary, submit, progress, and terminal result.

The initial implementation uses ordinary path text inputs in browser development and backend/native picker paths when pywebview picker methods become available. Client validation blocks missing fields, wrong extensions, unsupported versions, empty Japanese text, and unconfirmed permissions. After submission it polls every 250 ms, stops polling on terminal state or unmount, and displays `runtime_required` as `文件已安全导入，等待安装 CPU 运行时后试听并启用`.

- [ ] **Step 5: Integrate the modal in DanmuPanel**

DanmuPanel owns modal visibility. On `installed`, it refreshes the speech catalog without enabling speech or changing the current selected system voice.

- [ ] **Step 6: Run frontend tests and production build**

Run: `cd frontend && npm test`

Run: `cd frontend && npm run build`

Expected: tests PASS and Vite build exits 0.

- [ ] **Step 7: Commit the import UI**

```bash
git add frontend/src/components frontend/src/components/DanmuPanel.vue
git commit -m "feat: add voice training result import wizard"
```

---

### Task 6: End-to-End Foundation Verification and Developer Preview

**Files:**
- Modify: `README.md`
- Modify: `tests/test_windows_packaging.py`
- Modify: `docs/superpowers/plans/2026-08-31-voice-pack-import-foundation.md`

**Interfaces:**
- Verifies all interfaces produced by Tasks 1–5.

- [ ] **Step 1: Add packaging assertions before changing documentation**

Extend `tests/test_windows_packaging.py` to require the packaged app to include `backend.voice` modules and to keep model files out of the one-file EXE.

- [ ] **Step 2: Run packaging test and verify RED if hidden imports are missing**

Run: `python -m unittest tests.test_windows_packaging -v`

Expected: FAIL until the Windows build script contains the voice package hidden import or collection entry.

- [ ] **Step 3: Update packaging and user documentation**

Document the standard training-result inputs, the application-data location, and the deliberate `runtime_required` state. Explicitly state that this milestone does not synthesize with the imported pack yet and never falls back silently to another voice.

- [ ] **Step 4: Run complete verification**

Run: `python -m unittest discover -s tests -v`

Run: `cd frontend && npm test`

Run: `cd frontend && npm run build`

Run: `git diff --check`

Expected: every command exits 0, with no failed tests or whitespace errors.

- [ ] **Step 5: Start the hot-reload preview for user review**

Run: `cd frontend && npm run dev -- --host 127.0.0.1`

Open the development URL with the existing speech preview query. Confirm visually that the `导入` button is beside the voice selector, all four wizard steps fit the 1000×720 desktop viewport, and closing the modal restores keyboard focus.

- [ ] **Step 6: Mark executed plan checkboxes and commit milestone**

```bash
git add README.md tests/test_windows_packaging.py scripts/build_windows.ps1 docs/superpowers/plans/2026-08-31-voice-pack-import-foundation.md
git commit -m "docs: complete voice pack import foundation"
```

## Deferred to the Next Plan

- Signed GPT-SoVITS CPU runtime installation and manifest signature verification.
- Restricted `weights_only` model inspection and v2Pro/v2ProPlus tensor compatibility checks.
- CPU sidecar lifecycle, health, voice loading, prewarm, streaming PCM, and cancellation.
- Real preview generation and promotion from `runtime_required` to `ready`.
- Python-owned production speech scheduler, cache, and audio player.
- ZIP/directory import of externally created standard packages after the same runtime promotion gate is available.
