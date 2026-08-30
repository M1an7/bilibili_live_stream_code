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
  let removeVoiceListener = null;

  const state = {
    supported: browserAvailable,
    enabled: false,
    status: browserAvailable ? 'idle' : 'unsupported',
    voices: [],
    systemVoices: [],
    voicePacks: [],
    selectedVoiceKey: saved.selectedVoiceKey,
    selectedVoiceURI: saved.selectedVoiceKey.startsWith('system:') ? saved.selectedVoiceKey.slice(7) : '',
    rate: saved.rate,
    volume: saved.volume,
    queueLength: 0,
    error: browserAvailable ? '' : (backend ? '正在检测桌面语音...' : '系统语音不可用'),
  };

  const engineAvailable = () => Boolean(activeEngine);

  const snapshot = () => ({
    ...state,
    voices: state.voices.map(voice => ({ ...voice })),
    systemVoices: state.systemVoices.map(voice => ({ ...voice })),
    voicePacks: state.voicePacks.map(voice => ({ ...voice })),
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
    state.voices = [...state.systemVoices, ...readyPacks];
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

    if (!state.voices.some(voice => voice.voiceKey === state.selectedVoiceKey)) {
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
      await refreshVoicePacks();
      return snapshot();
    }
    if (!backend || typeof backend.getCapabilities !== 'function') {
      return snapshot();
    }

    try {
      const result = await backend.getCapabilities();
      const capabilities = result?.code === 0 ? result.data : null;
      if (!capabilities?.supported) {
        state.supported = false;
        state.status = 'unsupported';
        state.error = capabilities?.error || result?.msg || '桌面系统语音不可用';
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
      await refreshVoicePacks();
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
    current = { ...item, startedAt: now(), token };
    state.status = 'speaking';
    state.error = '';
    updateQueueState();

    const finish = (error = '') => {
      if (!current || current.token !== token || playbackToken !== token) return;
      current = null;
      if (error) state.error = error;
      pump();
    };

    if (activeEngine === 'browser') {
      const utterance = new Utterance(item.text);
      utterance.voice = voiceObjects.get(state.selectedVoiceURI) || null;
      utterance.rate = state.rate;
      utterance.volume = state.volume;
      utterance.onend = () => finish();
      utterance.onerror = event => finish(event?.error || '语音播放失败');
      synth.speak(utterance);
      notify();
      return;
    }

    notify();
    Promise.resolve(backend.speak(item.text, {
      voiceURI: state.selectedVoiceURI,
      rate: state.rate,
      volume: state.volume,
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
    current = null;
    if (wasPlaying && activeEngine === 'browser') synth.cancel();
    if (wasPlaying && activeEngine === 'backend') void backend.stop?.();
    state.status = state.enabled ? 'ready' : 'idle';
    notify();
  };

  const setEnabled = (enabled) => {
    if (enabled && !engineAvailable()) return false;
    const nextEnabled = Boolean(enabled);
    if (state.enabled === nextEnabled) return true;
    state.enabled = nextEnabled;
    if (!nextEnabled) {
      clear();
      return true;
    }
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
    });
    updateQueueState();
    notify();
    pump();
    return true;
  };

  const skip = () => {
    if (!current) return false;
    playbackToken += 1;
    current = null;
    state.status = 'ready';
    if (activeEngine === 'browser') {
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
    const normalized = typeof voiceKey === 'string' ? voiceKey : '';
    state.selectedVoiceKey = normalized && !normalized.includes(':') ? `system:${normalized}` : normalized;
    state.selectedVoiceURI = state.selectedVoiceKey.startsWith('system:')
      ? state.selectedVoiceKey.slice(7)
      : '';
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
    setEnabled,
    setVoice,
    setRate,
    setVolume,
    enqueue,
    skip,
    clear,
    destroy() {
      clear();
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
    return bridge.speakText(text, options.voiceURI, options.rate, options.volume);
  },
  async stop() {
    const bridge = await getDesktopBridge();
    return bridge.stopSpeech();
  },
  async listVoicePacks() {
    const bridge = await getDesktopBridge();
    return bridge.listVoicePacks();
  },
} : null;

export const speechService = createSpeechService({
  synth: typeof window !== 'undefined' ? window.speechSynthesis : null,
  Utterance: typeof window !== 'undefined' ? window.SpeechSynthesisUtterance : null,
  backend: desktopBackend,
  storage: typeof window !== 'undefined' ? window.localStorage : null,
});
