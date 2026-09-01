const SETTINGS_KEY = 'bili-live-speech-settings-v1';
const DEFAULT_SETTINGS = { selectedVoiceKey: '', rate: 1, volume: 1 };
const OVERLOAD_WAIT_MS = 2_000;
const STALE_AFTER_MS = 3_000;

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

const estimateDurationMs = (text, rate) => (
  Math.max(300, Math.ceil((String(text).length / (5 * rate)) * 1_000))
);

const loadSettings = (storage) => {
  if (!storage) return { ...DEFAULT_SETTINGS };

  try {
    const saved = JSON.parse(storage.getItem(SETTINGS_KEY) || '{}');
    const selectedVoiceKey = typeof saved.selectedVoiceKey === 'string'
      ? saved.selectedVoiceKey
      : (typeof saved.voiceURI === 'string' && saved.voiceURI ? `system:${saved.voiceURI}` : '');
    return {
      selectedVoiceKey,
      rate: clamp(Number(saved.rate) || DEFAULT_SETTINGS.rate, 0.5, 2),
      volume: clamp(Number.isFinite(Number(saved.volume)) ? Number(saved.volume) : DEFAULT_SETTINGS.volume, 0, 1),
    };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
};

export const createSpeechService = ({
  synth = null,
  Utterance = null,
  backend = null,
  storage = null,
  now = Date.now,
} = {}) => {
  const browserAvailable = Boolean(
    synth
    && Utterance
    && typeof synth.speak === 'function'
    && typeof synth.cancel === 'function'
    && typeof synth.getVoices === 'function',
  );
  let activeEngine = browserAvailable ? 'browser' : null;
  const saved = loadSettings(storage);
  const listeners = new Set();
  const voiceObjects = new Map();
  let queue = [];
  let current = null;
  let playbackToken = 0;
  let prepareGeneration = 0;
  let removeVoiceListener = null;

  const state = {
    supported: browserAvailable,
    enabled: false,
    status: browserAvailable ? 'idle' : 'unsupported',
    voices: [],
    systemVoices: [],
    voicePacks: [],
    realtimeVoices: [],
    selectedVoiceKey: saved.selectedVoiceKey,
    selectedVoiceURI: saved.selectedVoiceKey.startsWith('system:') ? saved.selectedVoiceKey.slice(7) : '',
    rate: saved.rate,
    volume: saved.volume,
    queueLength: 0,
    runtime: null,
    error: browserAvailable ? '' : (backend ? '正在检测桌面语音...' : '系统语音不可用'),
  };

  const selectedIsPack = () => state.selectedVoiceKey.startsWith('pack:');
  const selectedIsAivmx = () => state.selectedVoiceKey.startsWith('aivmx:');
  const selectedIsPersonalized = () => selectedIsPack() || selectedIsAivmx();
  const engineAvailable = () => Boolean(activeEngine || (backend && selectedIsPersonalized()));

  const snapshot = () => ({
    ...state,
    voices: state.voices.map(voice => ({ ...voice })),
    systemVoices: state.systemVoices.map(voice => ({ ...voice })),
    voicePacks: state.voicePacks.map(voice => ({ ...voice })),
    realtimeVoices: state.realtimeVoices.map(voice => ({ ...voice })),
  });

  const notify = () => {
    const next = snapshot();
    listeners.forEach(listener => listener(next));
  };

  const persist = () => {
    if (!storage) return;
    try {
      storage.setItem(SETTINGS_KEY, JSON.stringify({
        selectedVoiceKey: state.selectedVoiceKey,
        rate: state.rate,
        volume: state.volume,
      }));
    } catch {
      // Storage failures must never interrupt live speech.
    }
  };

  const rebuildSelectableVoices = () => {
    const readyPacks = state.voicePacks
      .filter(pack => pack.selectable && pack.health === 'ready')
      .map(pack => ({
        name: pack.display_name,
        voiceURI: '',
        voiceKey: pack.voice_key,
        lang: (pack.supported_output_languages || []).join(', '),
        default: false,
        kind: 'pack',
      }));
    const readyRealtime = state.realtimeVoices
      .filter(voice => voice.selectable && voice.health === 'ready')
      .map(voice => ({
        name: voice.display_name,
        voiceURI: '',
        voiceKey: voice.voice_key,
        lang: (voice.supported_output_languages || ['zh-CN']).join(', '),
        default: false,
        kind: 'aivmx',
        resourceMode: 'cpu_zero_vram',
      }));
    state.voices = [...state.systemVoices, ...readyRealtime, ...readyPacks];
  };

  const refreshVoices = () => {
    if (activeEngine !== 'browser') return state.voices;
    const discovered = synth.getVoices() || [];
    voiceObjects.clear();
    state.systemVoices = discovered.map((voice) => {
      voiceObjects.set(voice.voiceURI, voice);
      return {
        name: voice.name,
        voiceURI: voice.voiceURI,
        voiceKey: `system:${voice.voiceURI}`,
        lang: voice.lang,
        default: Boolean(voice.default),
        kind: 'system',
      };
    });
    rebuildSelectableVoices();

    if (!selectedIsPersonalized() && !state.voices.some(voice => voice.voiceKey === state.selectedVoiceKey)) {
      const preferred = discovered.find(voice => voice.default)
        || discovered.find(voice => String(voice.lang).toLowerCase().startsWith('zh'))
        || discovered[0];
      state.selectedVoiceKey = preferred ? `system:${preferred.voiceURI}` : '';
      state.selectedVoiceURI = preferred?.voiceURI || '';
    }
    notify();
    return state.voices;
  };

  const refreshVoicePacks = async () => {
    if (!backend || typeof backend.listVoicePacks !== 'function') return state.voicePacks;
    try {
      const result = await backend.listVoicePacks();
      state.voicePacks = result?.code === 0 && Array.isArray(result.data)
        ? result.data.map(pack => ({ ...pack }))
        : [];
    } catch {
      state.voicePacks = [];
    }
    rebuildSelectableVoices();
    notify();
    return state.voicePacks;
  };

  const refreshAivmxVoices = async () => {
    if (!backend || typeof backend.listAivmxVoices !== 'function') return state.realtimeVoices;
    try {
      const result = await backend.listAivmxVoices();
      state.realtimeVoices = result?.code === 0 && Array.isArray(result.data)
        ? result.data.map(voice => ({ ...voice }))
        : [];
    } catch {
      state.realtimeVoices = [];
    }
    rebuildSelectableVoices();
    notify();
    return state.realtimeVoices;
  };

  const selectPreferredVoice = () => {
    const available = new Set(state.voices.map(voice => voice.voiceKey));
    if (available.has(state.selectedVoiceKey)) {
      state.selectedVoiceURI = state.selectedVoiceKey.startsWith('system:')
        ? state.selectedVoiceKey.slice(7)
        : '';
      return;
    }
    const preferred = state.systemVoices.find(voice => voice.default)
      || state.systemVoices.find(voice => String(voice.lang).toLowerCase().startsWith('zh'))
      || state.systemVoices[0]
      || state.voices[0];
    state.selectedVoiceKey = preferred?.voiceKey || '';
    state.selectedVoiceURI = preferred?.voiceURI || '';
  };

  const initialize = async () => {
    if (browserAvailable) {
      refreshVoices();
      await Promise.all([refreshVoicePacks(), refreshAivmxVoices()]);
      selectPreferredVoice();
      notify();
      return snapshot();
    }
    if (!backend || typeof backend.getCapabilities !== 'function') {
      return snapshot();
    }

    try {
      const result = await backend.getCapabilities();
      const capabilities = result?.code === 0 ? result.data : null;
      await Promise.all([refreshVoicePacks(), refreshAivmxVoices()]);
      const hasReadyPersonalized = [state.voicePacks, state.realtimeVoices]
        .some(items => items.some(voice => voice.selectable && voice.health === 'ready'));
      if (!capabilities?.supported) {
        activeEngine = hasReadyPersonalized ? 'backend' : null;
        state.supported = hasReadyPersonalized;
        state.status = hasReadyPersonalized ? 'idle' : 'unsupported';
        state.error = capabilities?.error || result?.msg || '桌面系统语音不可用';
        selectPreferredVoice();
        notify();
        return snapshot();
      }

      activeEngine = 'backend';
      state.supported = true;
      state.status = state.enabled ? 'ready' : 'idle';
      state.systemVoices = Array.isArray(capabilities.voices)
        ? capabilities.voices.map(voice => ({
          ...voice,
          voiceKey: `system:${voice.voiceURI}`,
          kind: 'system',
        }))
        : [];
      rebuildSelectableVoices();
      state.error = capabilities.error || '';
      selectPreferredVoice();
      notify();
    } catch (error) {
      state.supported = false;
      state.status = 'unsupported';
      state.error = error?.message || '桌面系统语音检测失败';
      notify();
    }
    return snapshot();
  };

  const purgeStale = () => {
    const cutoff = now() - STALE_AFTER_MS;
    queue = queue.filter(item => item.createdAt >= cutoff);
  };

  const queuedDuration = () => queue.reduce(
    (total, item) => total + item.estimatedDuration,
    0,
  );

  const currentRemaining = () => {
    if (!current) return 0;
    const elapsed = Math.max(0, now() - current.startedAt);
    return Math.max(0, current.estimatedDuration - elapsed);
  };

  const updateQueueState = () => {
    state.queueLength = queue.length;
  };

  const pump = () => {
    if (!state.enabled || current || !engineAvailable()) return;
    purgeStale();
    updateQueueState();
    const item = queue.shift();

    if (!item) {
      state.status = 'ready';
      notify();
      return;
    }

    const token = ++playbackToken;
    const useBackend = item.voiceKey.startsWith('pack:') || item.voiceKey.startsWith('aivmx:') || activeEngine === 'backend';
    current = { ...item, startedAt: now(), token, playbackEngine: useBackend ? 'backend' : 'browser' };
    state.status = 'speaking';
    state.error = '';
    updateQueueState();

    const finish = (error = '') => {
      if (!current || current.token !== token || playbackToken !== token) return;
      current = null;
      if (error) state.error = error;
      pump();
    };

    if (!useBackend) {
      const utterance = new Utterance(item.text);
      utterance.voice = voiceObjects.get(item.voiceURI) || null;
      utterance.rate = item.rate;
      utterance.volume = item.volume;
      utterance.onend = () => finish();
      utterance.onerror = event => finish(event?.error || '语音播放失败');
      synth.speak(utterance);
      notify();
      return;
    }

    notify();
    Promise.resolve(backend.speak(item.text, {
      voiceURI: item.voiceURI,
      voiceKey: item.voiceKey,
      rate: item.rate,
      volume: item.volume,
    })).then((result) => {
      if (result?.code !== 0) {
        finish(result?.msg || '桌面语音播放失败');
        return;
      }
      finish();
    }).catch(error => finish(error?.message || '桌面语音播放失败'));
  };

  const clear = () => {
    queue = [];
    updateQueueState();
    playbackToken += 1;
    const wasPlaying = Boolean(current);
    const playbackEngine = current?.playbackEngine;
    current = null;
    if (wasPlaying && playbackEngine === 'browser') synth.cancel();
    if (wasPlaying && playbackEngine === 'backend') void backend.stop?.();
    state.status = state.enabled ? 'ready' : 'idle';
    notify();
  };

  const setEnabled = (enabled) => {
    if (enabled && !engineAvailable()) return false;
    const nextEnabled = Boolean(enabled);
    if (!nextEnabled) {
      const preparing = ['loading_gpu', 'loading_cpu', 'warming'].includes(state.status);
      if (state.enabled === nextEnabled && !preparing) return true;
      prepareGeneration += 1;
      const releasePersonalized = selectedIsPersonalized() || preparing;
      state.enabled = false;
      clear();
      if (releasePersonalized) {
        const release = selectedIsAivmx() ? backend?.releaseAivmx : backend?.release;
        return Promise.resolve(release?.()).then(() => true).catch(() => true);
      }
      return true;
    }
    if (state.enabled === nextEnabled) return true;
    if (selectedIsPersonalized()) {
      const generation = ++prepareGeneration;
      const requestedVoiceKey = state.selectedVoiceKey;
      const cpuMode = selectedIsAivmx();
      const stale = () => generation !== prepareGeneration || state.selectedVoiceKey !== requestedVoiceKey;
      const release = cpuMode ? backend?.releaseAivmx : backend?.release;
      const prepare = cpuMode ? backend?.prepareAivmx : backend?.prepare;
      const releaseStale = () => Promise.resolve(release?.()).catch(() => {});
      state.enabled = false;
      state.status = cpuMode ? 'loading_cpu' : 'loading_gpu';
      state.error = '';
      notify();
      return Promise.resolve(prepare?.(requestedVoiceKey)).then(async (result) => {
        if (stale()) {
          await releaseStale();
          return false;
        }
        if (result?.code !== 0) {
          state.enabled = false;
          state.status = cpuMode ? 'cpu_error' : 'gpu_error';
          state.error = result?.msg || (cpuMode ? 'CPU 音色准备失败' : 'GPU 音色准备失败');
          notify();
          return false;
        }
        state.runtime = result?.data?.runtime || null;
        state.status = 'warming';
        notify();
        if (cpuMode) await refreshAivmxVoices();
        else await refreshVoicePacks();
        if (stale()) {
          await releaseStale();
          return false;
        }
        state.enabled = true;
        state.status = 'ready';
        state.error = '';
        pump();
        notify();
        return true;
      }).catch((error) => {
        if (stale()) {
          void releaseStale();
          return false;
        }
        state.enabled = false;
        state.status = cpuMode ? 'cpu_error' : 'gpu_error';
        state.error = error?.message || (cpuMode ? 'CPU 音色准备失败' : 'GPU 音色准备失败');
        notify();
        return false;
      });
    }
    state.enabled = true;
    state.status = 'ready';
    refreshVoices();
    pump();
    notify();
    return true;
  };

  const enqueue = (text, { createdAt = now() } = {}) => {
    if (!engineAvailable() || !state.enabled) return false;
    const normalized = typeof text === 'string' ? text.trim() : '';
    if (!normalized) return false;

    purgeStale();
    if (currentRemaining() + queuedDuration() > OVERLOAD_WAIT_MS) {
      queue = [];
    }
    queue.push({
      text: normalized,
      createdAt,
      estimatedDuration: estimateDurationMs(normalized, state.rate),
      voiceKey: state.selectedVoiceKey,
      voiceURI: state.selectedVoiceURI,
      rate: state.rate,
      volume: state.volume,
    });
    updateQueueState();
    notify();
    pump();
    return true;
  };

  const skip = () => {
    if (!current) return false;
    const playbackEngine = current.playbackEngine;
    playbackToken += 1;
    current = null;
    state.status = 'ready';
    if (playbackEngine === 'browser') {
      synth.cancel();
      pump();
      return true;
    }

    notify();
    Promise.resolve().then(() => backend.stop()).then((result) => {
      if (result?.code !== 0) {
        state.error = result?.msg || '停止桌面语音失败';
        notify();
        return;
      }
      pump();
    }).catch((error) => {
      state.error = error?.message || '停止桌面语音失败';
      notify();
    });
    return true;
  };

  const setVoice = (voiceKey) => {
    const preparing = ['loading_gpu', 'loading_cpu', 'warming'].includes(state.status);
    const previousKey = state.selectedVoiceKey;
    const releasePersonalized = (state.enabled || preparing) && (previousKey.startsWith('pack:') || previousKey.startsWith('aivmx:'));
    prepareGeneration += 1;
    const normalized = typeof voiceKey === 'string' ? voiceKey : '';
    state.selectedVoiceKey = normalized && !normalized.includes(':') ? `system:${normalized}` : normalized;
    state.selectedVoiceURI = state.selectedVoiceKey.startsWith('system:')
      ? state.selectedVoiceKey.slice(7)
      : '';
    if (state.enabled || preparing) {
      state.enabled = false;
      clear();
    }
    state.error = '';
    if (releasePersonalized) {
      const release = previousKey.startsWith('aivmx:') ? backend?.releaseAivmx : backend?.release;
      void Promise.resolve(release?.()).catch(() => {});
    }
    persist();
    notify();
  };

  const setRate = (rate) => {
    state.rate = clamp(Number(rate) || 1, 0.5, 2);
    persist();
    notify();
  };

  const setVolume = (volume) => {
    const numeric = Number(volume);
    state.volume = clamp(Number.isFinite(numeric) ? numeric : 1, 0, 1);
    persist();
    notify();
  };

  const voiceChangeHandler = () => refreshVoices();
  if (browserAvailable) {
    if (typeof synth.addEventListener === 'function') {
      synth.addEventListener('voiceschanged', voiceChangeHandler);
      removeVoiceListener = () => synth.removeEventListener?.('voiceschanged', voiceChangeHandler);
    } else {
      synth.onvoiceschanged = voiceChangeHandler;
      removeVoiceListener = () => {
        if (synth.onvoiceschanged === voiceChangeHandler) synth.onvoiceschanged = null;
      };
    }
  }

  return {
    getState: snapshot,
    initialize,
    subscribe(listener) {
      listeners.add(listener);
      listener(snapshot());
      return () => listeners.delete(listener);
    },
    refreshVoices,
    refreshVoicePacks,
    refreshAivmxVoices,
    setEnabled,
    setVoice,
    setRate,
    setVolume,
    enqueue,
    skip,
    clear,
    destroy() {
      prepareGeneration += 1;
      clear();
      if (selectedIsAivmx()) void backend?.releaseAivmx?.();
      else if (selectedIsPack()) void backend?.release?.();
      removeVoiceListener?.();
      listeners.clear();
    },
  };
};

let desktopBridgePromise = null;
const getDesktopBridge = () => {
  if (!desktopBridgePromise) {
    desktopBridgePromise = import('../api/bridge').then(({ useBridge }) => useBridge());
  }
  return desktopBridgePromise;
};

const desktopBackend = typeof window !== 'undefined' ? {
  async getCapabilities() {
    const bridge = await getDesktopBridge();
    return bridge.getSpeechCapabilities();
  },
  async speak(text, options) {
    const bridge = await getDesktopBridge();
    return bridge.speakText(text, options.voiceURI, options.rate, options.volume, options.voiceKey);
  },
  async stop() {
    const bridge = await getDesktopBridge();
    return bridge.stopSpeech();
  },
  async listVoicePacks() {
    const bridge = await getDesktopBridge();
    return bridge.listVoicePacks();
  },
  async listAivmxVoices() {
    const bridge = await getDesktopBridge();
    return bridge.listAivmxVoices();
  },
  async prepare(voiceKey) {
    const bridge = await getDesktopBridge();
    return bridge.prepareVoice(voiceKey);
  },
  async release() {
    const bridge = await getDesktopBridge();
    return bridge.releasePersonalizedVoice();
  },
  async prepareAivmx(voiceKey) {
    const bridge = await getDesktopBridge();
    return bridge.prepareAivmxVoice(voiceKey);
  },
  async releaseAivmx() {
    const bridge = await getDesktopBridge();
    return bridge.releaseAivmxVoice();
  },
} : null;

export const speechService = createSpeechService({
  synth: typeof window !== 'undefined' ? window.speechSynthesis : null,
  Utterance: typeof window !== 'undefined' ? window.SpeechSynthesisUtterance : null,
  backend: desktopBackend,
  storage: typeof window !== 'undefined' ? window.localStorage : null,
});
