# System Danmu Speech Milestone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an observable, testable end-to-end path from ordinary Bilibili danmu events to the local system's default speech voice, with an adaptive queue and live Vite UI preview.

**Architecture:** A focused browser-side `speechService` wraps the Web Speech API and owns queueing, voice selection, persistence, and playback state. `DanmuPanel` routes only ordinary danmu messages into that service and renders a separate `SpeechToolbar`; a development-only preview path supplies a fake logged-in session and a simulated danmu button so UI changes can be reviewed through Vite HMR before the real pywebview smoke test.

**Tech Stack:** Vue 3 Composition API, browser Web Speech API, Vite 5, Vitest 1.6, Vue Test Utils 2, jsdom 24.

**Spec:** `docs/superpowers/specs/2026-08-30-danmu-voice-broadcast-design.md`, section 4.0 only.

## Global Constraints

- Work directly in the current checkout; do not create a git worktree.
- Milestone 0 speaks ordinary `type === "danmu"` events only and does not read usernames.
- Use the operating system speech voices; add no model, cloud service, or GPU dependency.
- Windows is the acceptance platform; other platforms enable the feature only when `speechSynthesis` and at least one voice are available.
- Default queue behavior is FIFO under low load and latest-message-first recovery when estimated wait exceeds 2 seconds; queued items older than 3 seconds are stale.
- Development preview controls must be inaccessible in production builds.
- Keep the existing danmu display and send behavior working when speech is unavailable or disabled.
- Persist selected voice, rate, and volume locally; enabling speech is session-only and defaults to off after application restart.
- All implementation changes follow red-green-refactor and end with focused tests.

---

## File Structure

- Create `frontend/src/services/speechService.js`: Web Speech adapter, explicit queue, state subscription, voice discovery, persistence, skip, stop, and cleanup.
- Create `frontend/src/services/speechService.spec.js`: deterministic tests using fake synthesis and fake utterance implementations.
- Create `frontend/src/services/danmuSpeechRouter.js`: pure boundary that accepts only non-empty ordinary danmu and passes text to the speech service.
- Create `frontend/src/services/danmuSpeechRouter.spec.js`: routing tests independent of Vue and pywebview.
- Create `frontend/src/components/SpeechToolbar.vue`: compact speech controls and development-only simulation control.
- Create `frontend/src/components/SpeechToolbar.spec.js`: component tests for supported, unsupported, enabled, settings, skip, and development states.
- Modify `frontend/src/components/DanmuPanel.vue`: render the toolbar, route incoming messages, and expose the simulated danmu action.
- Modify `frontend/src/App.vue`: enable the `?speech-preview=1` development-only fake session and open the danmu panel directly.
- Modify `frontend/package.json`: add Vitest scripts and test dependencies.
- Modify `frontend/package-lock.json`: lock the new development dependencies.
- Modify `README.md`: document the live preview URL and final desktop smoke-test steps.

---

### Task 1: Deterministic System Speech Service

**Files:**
- Create: `frontend/src/services/speechService.js`
- Create: `frontend/src/services/speechService.spec.js`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`

**Interfaces:**
- Produces: `createSpeechService(options): SpeechService`
- Produces: singleton `speechService: SpeechService`
- `SpeechService.getState()` returns `{ supported, enabled, status, voices, selectedVoiceURI, rate, volume, queueLength, error }`.
- `SpeechService.subscribe(listener)` returns an unsubscribe function and calls `listener(snapshot)` on every state change.
- `SpeechService.refreshVoices()`, `setEnabled(boolean)`, `setVoice(string)`, `setRate(number)`, `setVolume(number)`, `enqueue(string, { createdAt?: number })`, `skip()`, `clear()`, and `destroy()` are the only UI-facing mutations.
- Consumes: injected `synth`, `Utterance`, `storage`, and `now` in tests; defaults to browser globals in production.

- [ ] **Step 1: Add the test runner dependencies and scripts**

Update `frontend/package.json` scripts to include:

```json
{
  "test": "vitest run",
  "test:watch": "vitest"
}
```

Add these development dependencies:

```json
{
  "@vue/test-utils": "^2.4.6",
  "jsdom": "^24.1.3",
  "vitest": "^1.6.0"
}
```

Run:

```bash
cd frontend
npm install
```

Expected: `package-lock.json` records the exact dependency graph and `npm test -- --passWithNoTests` exits successfully.

- [ ] **Step 2: Write the failing service tests**

Create `frontend/src/services/speechService.spec.js` with fake browser primitives and these core tests:

```js
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createSpeechService } from './speechService';

class FakeUtterance {
  constructor(text) {
    this.text = text;
    this.voice = null;
    this.rate = 1;
    this.volume = 1;
    this.onend = null;
    this.onerror = null;
  }
}

const makeSynth = () => ({
  spoken: [],
  cancelled: 0,
  voices: [{ name: '系统默认', voiceURI: 'default', lang: 'zh-CN', default: true }],
  getVoices() { return this.voices; },
  speak(utterance) { this.spoken.push(utterance); },
  cancel() { this.cancelled += 1; },
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
});

const makeStorage = () => {
  const values = new Map();
  return {
    getItem: vi.fn(key => values.get(key) ?? null),
    setItem: vi.fn((key, value) => values.set(key, value)),
  };
};

describe('createSpeechService', () => {
  let synth;
  let storage;
  let clock;
  let service;

  beforeEach(() => {
    synth = makeSynth();
    storage = makeStorage();
    clock = 1_000;
    service = createSpeechService({
      synth,
      Utterance: FakeUtterance,
      storage,
      now: () => clock,
    });
  });

  it('defaults to disabled and discovers the system default voice', () => {
    service.refreshVoices();
    expect(service.getState()).toMatchObject({
      supported: true,
      enabled: false,
      selectedVoiceURI: 'default',
    });
  });

  it('speaks queued text in FIFO order while load is low', () => {
    service.setEnabled(true);
    service.enqueue('第一条');
    service.enqueue('第二条');
    expect(synth.spoken.map(item => item.text)).toEqual(['第一条']);
    synth.spoken[0].onend();
    expect(synth.spoken.map(item => item.text)).toEqual(['第一条', '第二条']);
  });

  it('drops queued ordinary messages when estimated wait exceeds two seconds', () => {
    service.setEnabled(true);
    service.enqueue('这是一条足够长的正在播放的弹幕');
    service.enqueue('将被过载策略丢弃的旧消息');
    service.enqueue('应该保留的最新消息');
    synth.spoken[0].onend();
    expect(synth.spoken.at(-1).text).toBe('应该保留的最新消息');
  });

  it('drops queued items that have been stale for three seconds', () => {
    service.setEnabled(true);
    service.enqueue('当前消息');
    service.enqueue('过期消息', { createdAt: clock });
    clock += 3_001;
    synth.spoken[0].onend();
    expect(synth.spoken).toHaveLength(1);
  });

  it('persists voice, rate, and volume but not enabled state', () => {
    service.setEnabled(true);
    service.setVoice('default');
    service.setRate(1.2);
    service.setVolume(0.6);
    const writes = storage.setItem.mock.calls.map(([, value]) => JSON.parse(value));
    expect(writes.at(-1)).toEqual({ voiceURI: 'default', rate: 1.2, volume: 0.6 });
  });

  it('skip cancels current speech and starts the next item', () => {
    service.setEnabled(true);
    service.enqueue('第一条');
    service.enqueue('第二条');
    service.skip();
    expect(synth.cancelled).toBe(1);
    expect(synth.spoken.at(-1).text).toBe('第二条');
  });

  it('reports unsupported without a speech engine and never throws', () => {
    const unsupported = createSpeechService({ synth: null, Utterance: null, storage });
    expect(unsupported.getState()).toMatchObject({ supported: false, enabled: false });
    expect(unsupported.setEnabled(true)).toBe(false);
    expect(unsupported.enqueue('不会播放')).toBe(false);
  });
});
```

- [ ] **Step 3: Run the service tests and verify red**

Run:

```bash
cd frontend
npm test -- src/services/speechService.spec.js
```

Expected: FAIL because `./speechService` does not exist.

- [ ] **Step 4: Implement the minimal speech service**

Create `frontend/src/services/speechService.js`. Use these constants and duration estimator:

```js
const SETTINGS_KEY = 'bili-live-speech-settings-v1';
const DEFAULT_SETTINGS = { voiceURI: '', rate: 1, volume: 1 };
const OVERLOAD_WAIT_MS = 2_000;
const STALE_AFTER_MS = 3_000;

const estimateDurationMs = (text, rate) =>
  Math.max(300, Math.ceil((String(text).length / (5 * rate)) * 1_000));
```

Implement one explicit queue instead of delegating queue ownership to `speechSynthesis`. Only call `synth.speak()` for the current item. Before selecting the next item, remove entries with `now() - createdAt > STALE_AFTER_MS`. When current remaining estimate plus queued estimates exceeds `OVERLOAD_WAIT_MS`, replace queued ordinary entries with the newly arrived entry. Guard each utterance with a monotonically increasing playback token so a delayed `onend` after `skip()` cannot start a second item.

Load persisted settings with `JSON.parse` inside `try/catch`, clamp `rate` to `0.5..2` and `volume` to `0..1`, and persist only `{ voiceURI, rate, volume }`. Register and unregister the `voiceschanged` listener through `addEventListener` when available, with `onvoiceschanged` as the fallback.

Export the browser singleton without throwing during tests or server-side evaluation:

```js
export const speechService = createSpeechService({
  synth: typeof window !== 'undefined' ? window.speechSynthesis : null,
  Utterance: typeof window !== 'undefined' ? window.SpeechSynthesisUtterance : null,
  storage: typeof window !== 'undefined' ? window.localStorage : null,
});
```

- [ ] **Step 5: Run the service tests and verify green**

Run:

```bash
cd frontend
npm test -- src/services/speechService.spec.js
```

Expected: all service tests PASS.

- [ ] **Step 6: Commit the service boundary**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/services/speechService.js frontend/src/services/speechService.spec.js
git commit -m "feat: add queued system speech service"
```

---

### Task 2: Danmu-to-Speech Routing Boundary

**Files:**
- Create: `frontend/src/services/danmuSpeechRouter.js`
- Create: `frontend/src/services/danmuSpeechRouter.spec.js`

**Interfaces:**
- Consumes: `SpeechService.enqueue(text, { createdAt })` from Task 1.
- Produces: `routeDanmuToSpeech(message, service, now = Date.now): boolean`.
- Returns `true` only when a non-empty ordinary danmu was accepted for speech.

- [ ] **Step 1: Write the failing router tests**

Create `frontend/src/services/danmuSpeechRouter.spec.js`:

```js
import { describe, expect, it, vi } from 'vitest';
import { routeDanmuToSpeech } from './danmuSpeechRouter';

describe('routeDanmuToSpeech', () => {
  it('routes trimmed ordinary danmu without the username', () => {
    const service = { enqueue: vi.fn(() => true) };
    expect(routeDanmuToSpeech(
      { type: 'danmu', uname: '观众甲', msg: '  你好主播  ' },
      service,
      () => 123,
    )).toBe(true);
    expect(service.enqueue).toHaveBeenCalledWith('你好主播', { createdAt: 123 });
  });

  it.each(['gift', 'interact', 'system'])(
    'does not route %s events in milestone zero',
    type => {
      const service = { enqueue: vi.fn() };
      expect(routeDanmuToSpeech({ type, msg: '忽略' }, service)).toBe(false);
      expect(service.enqueue).not.toHaveBeenCalled();
    },
  );

  it('rejects missing and blank messages', () => {
    const service = { enqueue: vi.fn() };
    expect(routeDanmuToSpeech({ type: 'danmu', msg: '   ' }, service)).toBe(false);
    expect(routeDanmuToSpeech(null, service)).toBe(false);
  });
});
```

- [ ] **Step 2: Run the router tests and verify red**

Run:

```bash
cd frontend
npm test -- src/services/danmuSpeechRouter.spec.js
```

Expected: FAIL because `./danmuSpeechRouter` does not exist.

- [ ] **Step 3: Implement the minimal router**

Create `frontend/src/services/danmuSpeechRouter.js`:

```js
export const routeDanmuToSpeech = (message, service, now = Date.now) => {
  if (!message || message.type !== 'danmu') return false;
  const text = typeof message.msg === 'string' ? message.msg.trim() : '';
  if (!text || !service || typeof service.enqueue !== 'function') return false;
  return service.enqueue(text, { createdAt: now() }) !== false;
};
```

- [ ] **Step 4: Run focused and full service tests**

Run:

```bash
cd frontend
npm test -- src/services/danmuSpeechRouter.spec.js src/services/speechService.spec.js
```

Expected: all tests PASS.

- [ ] **Step 5: Commit the router**

```bash
git add frontend/src/services/danmuSpeechRouter.js frontend/src/services/danmuSpeechRouter.spec.js
git commit -m "feat: route ordinary danmu to speech"
```

---

### Task 3: Interactive Speech Toolbar

**Files:**
- Create: `frontend/src/components/SpeechToolbar.vue`
- Create: `frontend/src/components/SpeechToolbar.spec.js`

**Interfaces:**
- Consumes: the Task 1 `SpeechService` object through required prop `service`.
- Consumes: optional Boolean prop `devMode`, default `false`.
- Produces: `simulate` event when the development simulation button is clicked.
- Does not import `DanmuPanel`, the bridge, or a real model.

- [ ] **Step 1: Write the failing toolbar tests**

Create `frontend/src/components/SpeechToolbar.spec.js` using jsdom:

```js
// @vitest-environment jsdom
import { mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import SpeechToolbar from './SpeechToolbar.vue';

const makeService = (overrides = {}) => {
  let listener = null;
  const state = {
    supported: true,
    enabled: false,
    status: 'idle',
    voices: [{ name: '系统默认', voiceURI: 'default', lang: 'zh-CN', default: true }],
    selectedVoiceURI: 'default',
    rate: 1,
    volume: 1,
    queueLength: 0,
    error: '',
    ...overrides,
  };
  return {
    getState: () => ({ ...state }),
    subscribe: vi.fn(fn => { listener = fn; fn({ ...state }); return () => {}; }),
    refreshVoices: vi.fn(),
    setEnabled: vi.fn(),
    setVoice: vi.fn(),
    setRate: vi.fn(),
    setVolume: vi.fn(),
    skip: vi.fn(),
    emit: patch => { Object.assign(state, patch); listener?.({ ...state }); },
  };
};

describe('SpeechToolbar', () => {
  it('controls enable, voice, rate, volume, and skip', async () => {
    const service = makeService();
    const wrapper = mount(SpeechToolbar, { props: { service } });
    await wrapper.get('[data-test="speech-enabled"]').setValue(true);
    await wrapper.get('[data-test="speech-voice"]').setValue('default');
    await wrapper.get('[data-test="speech-rate"]').setValue('1.2');
    await wrapper.get('[data-test="speech-volume"]').setValue('0.6');
    await wrapper.get('[data-test="speech-skip"]').trigger('click');
    expect(service.setEnabled).toHaveBeenCalledWith(true);
    expect(service.setVoice).toHaveBeenCalledWith('default');
    expect(service.setRate).toHaveBeenCalledWith(1.2);
    expect(service.setVolume).toHaveBeenCalledWith(0.6);
    expect(service.skip).toHaveBeenCalled();
  });

  it('shows an unsupported explanation and disables enable control', () => {
    const wrapper = mount(SpeechToolbar, {
      props: { service: makeService({ supported: false, error: '系统语音不可用' }) },
    });
    expect(wrapper.text()).toContain('系统语音不可用');
    expect(wrapper.get('[data-test="speech-enabled"]').attributes('disabled')).toBeDefined();
  });

  it('shows simulation only in development mode', async () => {
    const hidden = mount(SpeechToolbar, { props: { service: makeService(), devMode: false } });
    expect(hidden.find('[data-test="simulate-danmu"]').exists()).toBe(false);
    const visible = mount(SpeechToolbar, { props: { service: makeService(), devMode: true } });
    await visible.get('[data-test="simulate-danmu"]').trigger('click');
    expect(visible.emitted('simulate')).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run the toolbar tests and verify red**

Run:

```bash
cd frontend
npm test -- src/components/SpeechToolbar.spec.js
```

Expected: FAIL because `SpeechToolbar.vue` does not exist.

- [ ] **Step 3: Implement the toolbar**

Create a compact two-row toolbar with Vue `script setup`. Subscribe on mount, unsubscribe on unmount, and call `service.refreshVoices()` once. Use the exact `data-test` attributes from the tests. Render:

- Row 1: broadcast checkbox, status pill, voice `<select>`, queue count, skip button.
- Row 2: rate range `0.5..2` with step `0.1`, volume range `0..1` with step `0.05`, and the simulation button when `devMode` is true.
- Unsupported state: visible error text and disabled checkbox/select/ranges/skip.

Use component-scoped styles consistent with the existing Bilibili blue `#00aeec`, with a pale blue toolbar background, 12–13 px labels, rounded controls, and responsive wrapping below 760 px. Do not add an icon library.

- [ ] **Step 4: Run the toolbar and service tests**

Run:

```bash
cd frontend
npm test -- src/components/SpeechToolbar.spec.js src/services/speechService.spec.js
```

Expected: all tests PASS.

- [ ] **Step 5: Commit the interactive toolbar**

```bash
git add frontend/src/components/SpeechToolbar.vue frontend/src/components/SpeechToolbar.spec.js
git commit -m "feat: add danmu speech controls"
```

---

### Task 4: Danmu Integration and Live Vite Preview

**Files:**
- Modify: `frontend/src/components/DanmuPanel.vue`
- Modify: `frontend/src/App.vue`
- Modify: `README.md`

**Interfaces:**
- Consumes: `speechService` from Task 1.
- Consumes: `routeDanmuToSpeech` from Task 2.
- Consumes: `SpeechToolbar` from Task 3.
- Produces: development URL `http://localhost:5173/?speech-preview=1` with a fake logged-in user, the danmu page visible, and a simulated ordinary danmu button.

- [ ] **Step 1: Add the toolbar and a single incoming-message handler**

In `DanmuPanel.vue`, import the three Task 1–3 units and replace the anonymous callback body with one named function:

```js
import SpeechToolbar from './SpeechToolbar.vue';
import { speechService } from '@/services/speechService';
import { routeDanmuToSpeech } from '@/services/danmuSpeechRouter';

const handleIncomingMessage = (data) => {
  addMessage(data);
  routeDanmuToSpeech(data, speechService);
};

const simulateDanmu = () => {
  handleIncomingMessage({
    type: 'danmu',
    uname: '界面测试观众',
    msg: `这是第 ${messages.value.length + 1} 条模拟弹幕`,
    face: '',
  });
};
```

Assign `window.onDanmuMessage = handleIncomingMessage` in `onActivated`. Render the toolbar immediately below the existing header:

```vue
<SpeechToolbar
  :service="speechService"
  :dev-mode="import.meta.env.DEV"
  @simulate="simulateDanmu"
/>
```

Do not alter display rendering, scrolling, sending, or the 200-message cap.

- [ ] **Step 2: Add the development-only preview session**

In `App.vue`, derive:

```js
const isSpeechPreview = import.meta.env.DEV
  && new URLSearchParams(window.location.search).get('speech-preview') === '1';
```

At the start of `onMounted`, when `isSpeechPreview` is true, call `fillUserState` with a local fake user and skip `loadSavedConfig`, `refreshCurrentUser`, and `syncRoomProfileToForm`:

```js
fillUserState({
  uid: 'speech-preview',
  roomId: 'speech-preview',
  uname: '界面预览',
  face: '',
  level: 6,
  money: 0,
  bcoin: 0,
  following: 0,
  follower: 0,
  dynamic_count: 0,
  current_exp: 0,
  next_exp: 1,
});
activeTab.value = 'danmu';
```

Continue registering tray callbacks after the preview initialization. This branch must be guarded by `import.meta.env.DEV` so production users cannot create a fake login with a query parameter.

- [ ] **Step 3: Run all frontend tests and build**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: all tests PASS and Vite produces `frontend/dist` without warnings that prevent output.

- [ ] **Step 4: Start the live preview for user feedback**

Run in a persistent terminal:

```bash
cd frontend
npm run dev -- --host 0.0.0.0
```

Open `http://localhost:5173/?speech-preview=1`. Expected: the danmu page is visible, system speech controls appear, enabling broadcast then clicking “模拟弹幕” adds a message and speaks it, and saved Vue/CSS changes hot-reload without refreshing.

- [ ] **Step 5: Document preview and real smoke-test steps**

Add a README subsection containing these exact user actions:

```markdown
### 语音播报界面预览

1. 在 `frontend` 目录运行 `npm run dev -- --host 0.0.0.0`。
2. 打开 `http://localhost:5173/?speech-preview=1`。
3. 开启“语音播报”，点击“模拟弹幕”测试系统默认音色。
4. 生产构建后通过 `python main.py` 登录账号，进入弹幕页，用真实弹幕完成最终验证。
```

- [ ] **Step 6: Commit integration and preview documentation**

```bash
git add frontend/src/components/DanmuPanel.vue frontend/src/App.vue README.md
git commit -m "feat: connect danmu to system speech preview"
```

---

### Task 5: Desktop Smoke Test and Milestone Verification

**Files:**
- Modify only if verification exposes a defect in a Task 1–4 file.

**Interfaces:**
- Verifies the complete milestone contract; creates no new public API.

- [ ] **Step 1: Run the full automated verification**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: all tests PASS and production assets build successfully.

- [ ] **Step 2: Verify development-only behavior**

With the Vite server open at `http://localhost:5173/?speech-preview=1`:

1. Confirm the fake user and danmu page appear.
2. Confirm “模拟弹幕” appears only in development preview.
3. Enable speech and hear the simulated message.
4. Change voice, rate, and volume; send another simulated message and confirm the new settings apply.
5. Queue several simulated messages quickly and confirm old queued speech is discarded instead of lagging indefinitely.
6. Click skip and confirm the next retained message starts.

- [ ] **Step 3: Verify the production desktop path with the user**

Run:

```bash
python main.py
```

The user logs in, opens the danmu page, enables speech, and sends a real ordinary danmu from another account. Expected: the danmu still renders, the system voice speaks only its message text, switching tabs does not stop the already-started monitor, and disabling speech stops current and queued audio.

- [ ] **Step 4: Record final evidence and commit only necessary fixes**

If a defect was found, add a failing regression test first, verify red, implement the smallest fix, verify green, and commit the focused files with:

```bash
git commit -m "fix: stabilize system danmu speech"
```

If no defect was found, do not create an empty verification commit. Record the exact test/build results in the completion handoff.
