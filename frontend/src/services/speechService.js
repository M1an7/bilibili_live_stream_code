const SETTINGS_KEY = 'bili-live-speech-settings-v1';
const DEFAULT_SETTINGS = { voiceURI: '', rate: 1, volume: 1 };
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
    return {
      voiceURI: typeof saved.voiceURI === 'string' ? saved.voiceURI : '',
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
  storage = null,
  now = Date.now,
} = {}) => {
  const engineAvailable = Boolean(
    synth
    && Utterance
    && typeof synth.speak === 'function'
    && typeof synth.cancel === 'function'
    && typeof synth.getVoices === 'function',
  );
  const saved = loadSettings(storage);
  const listeners = new Set();
  const voiceObjects = new Map();
  let queue = [];
  let current = null;
  let playbackToken = 0;
  let removeVoiceListener = null;

  const state = {
    supported: engineAvailable,
    enabled: false,
    status: engineAvailable ? 'idle' : 'unsupported',
    voices: [],
    selectedVoiceURI: saved.voiceURI,
    rate: saved.rate,
    volume: saved.volume,
    queueLength: 0,
    error: engineAvailable ? '' : '系统语音不可用',
  };

  const snapshot = () => ({
    ...state,
    voices: state.voices.map(voice => ({ ...voice })),
  });

  const notify = () => {
    const next = snapshot();
    listeners.forEach(listener => listener(next));
  };

  const persist = () => {
    if (!storage) return;
    try {
      storage.setItem(SETTINGS_KEY, JSON.stringify({
        voiceURI: state.selectedVoiceURI,
        rate: state.rate,
        volume: state.volume,
      }));
    } catch {
      // Storage failures must never interrupt live speech.
    }
  };

  const refreshVoices = () => {
    if (!engineAvailable) return [];
    const discovered = synth.getVoices() || [];
    voiceObjects.clear();
    state.voices = discovered.map((voice) => {
      voiceObjects.set(voice.voiceURI, voice);
      return {
        name: voice.name,
        voiceURI: voice.voiceURI,
        lang: voice.lang,
        default: Boolean(voice.default),
      };
    });

    if (!voiceObjects.has(state.selectedVoiceURI)) {
      const preferred = discovered.find(voice => voice.default)
        || discovered.find(voice => String(voice.lang).toLowerCase().startsWith('zh'))
        || discovered[0];
      state.selectedVoiceURI = preferred?.voiceURI || '';
    }
    notify();
    return state.voices;
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
    if (!state.enabled || current || !engineAvailable) return;
    purgeStale();
    updateQueueState();
    const item = queue.shift();

    if (!item) {
      state.status = 'ready';
      notify();
      return;
    }

    const utterance = new Utterance(item.text);
    utterance.voice = voiceObjects.get(state.selectedVoiceURI) || null;
    utterance.rate = state.rate;
    utterance.volume = state.volume;
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

    utterance.onend = () => finish();
    utterance.onerror = event => finish(event?.error || '语音播放失败');
    synth.speak(utterance);
    notify();
  };

  const clear = () => {
    queue = [];
    updateQueueState();
    playbackToken += 1;
    const wasPlaying = Boolean(current);
    current = null;
    if (engineAvailable && wasPlaying) synth.cancel();
    state.status = state.enabled ? 'ready' : 'idle';
    notify();
  };

  const setEnabled = (enabled) => {
    if (enabled && !engineAvailable) return false;
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
    if (!engineAvailable || !state.enabled) return false;
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
    synth.cancel();
    state.status = 'ready';
    pump();
    return true;
  };

  const setVoice = (voiceURI) => {
    state.selectedVoiceURI = typeof voiceURI === 'string' ? voiceURI : '';
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
  if (engineAvailable) {
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
    subscribe(listener) {
      listeners.add(listener);
      listener(snapshot());
      return () => listeners.delete(listener);
    },
    refreshVoices,
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

export const speechService = createSpeechService({
  synth: typeof window !== 'undefined' ? window.speechSynthesis : null,
  Utterance: typeof window !== 'undefined' ? window.SpeechSynthesisUtterance : null,
  storage: typeof window !== 'undefined' ? window.localStorage : null,
});
