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
    expect(writes.at(-1)).toEqual({ selectedVoiceKey: 'system:default', rate: 1.2, volume: 0.6 });
  });

  it('migrates an old system voice URI into a system voice key', async () => {
    storage.setItem('bili-live-speech-settings-v1', JSON.stringify({ voiceURI: 'Haruka' }));
    synth.voices = [{ name: 'Haruka', voiceURI: 'Haruka', lang: 'ja-JP', default: true }];
    service = createSpeechService({ synth, Utterance: FakeUtterance, storage });

    await service.initialize();

    expect(service.getState()).toMatchObject({
      selectedVoiceKey: 'system:Haruka',
      selectedVoiceURI: 'Haruka',
    });
  });

  it('keeps runtime-required packs visible for management but out of selectable voices', async () => {
    const backend = {
      listVoicePacks: vi.fn().mockResolvedValue({ code: 0, data: [
        {
          voice_key: 'pack:test',
          display_name: '测试音色',
          health: 'runtime_required',
          selectable: false,
        },
      ] }),
    };
    service = createSpeechService({ synth, Utterance: FakeUtterance, storage, backend });

    await service.initialize();

    expect(service.getState().voicePacks).toHaveLength(1);
    expect(service.getState().voices.some(voice => voice.voiceKey === 'pack:test')).toBe(false);
  });

  it('skip cancels current speech and starts the next item', () => {
    service.setEnabled(true);
    service.enqueue('第一条');
    service.enqueue('第二条');
    service.skip();

    expect(synth.cancelled).toBe(1);
    expect(synth.spoken.at(-1).text).toBe('第二条');
  });

  it('disabling speech stops playback and clears queued messages', () => {
    service.setEnabled(true);
    service.enqueue('第一条');
    service.enqueue('第二条');
    service.setEnabled(false);

    expect(synth.cancelled).toBe(1);
    expect(service.getState()).toMatchObject({
      enabled: false,
      status: 'idle',
      queueLength: 0,
    });
  });

  it('reports unsupported without a speech engine and never throws', () => {
    const unsupported = createSpeechService({ synth: null, Utterance: null, storage });

    expect(unsupported.getState()).toMatchObject({ supported: false, enabled: false });
    expect(unsupported.setEnabled(true)).toBe(false);
    expect(unsupported.enqueue('不会播放')).toBe(false);
  });

  it('falls back to the desktop speech backend when Web Speech is unavailable', async () => {
    let finishSpeech;
    const backend = {
      getCapabilities: vi.fn().mockResolvedValue({
        code: 0,
        data: {
          supported: true,
          engine: 'espeak-ng',
          voices: [
            { name: 'Mandarin', voiceURI: 'cmn', lang: 'zh-CN', default: true },
          ],
        },
      }),
      speak: vi.fn(() => new Promise((resolve) => {
        finishSpeech = resolve;
      })),
      stop: vi.fn().mockResolvedValue({ code: 0 }),
    };
    const desktop = createSpeechService({
      synth: null,
      Utterance: null,
      backend,
      storage,
      now: () => clock,
    });

    await desktop.initialize();
    expect(desktop.getState()).toMatchObject({
      supported: true,
      selectedVoiceURI: 'cmn',
    });

    desktop.setEnabled(true);
    desktop.enqueue('桌面语音测试');
    expect(backend.speak).toHaveBeenCalledWith('桌面语音测试', {
      voiceURI: 'cmn',
      voiceKey: 'system:cmn',
      rate: 1,
      volume: 1,
    });

    finishSpeech({ code: 0 });
    await vi.waitFor(() => {
      expect(desktop.getState().status).toBe('ready');
    });
  });

  it('waits for GPU preparation before enabling a personalized voice and passes its complete key', async () => {
    let finishSpeech;
    const backend = {
      getCapabilities: vi.fn().mockResolvedValue({ code: 0, data: { supported: true, voices: [] } }),
      listVoicePacks: vi.fn().mockResolvedValue({ code: 0, data: [{
        voice_key: 'pack:haibara-jp', display_name: '灰原哀（日语）', health: 'ready', selectable: true,
      }] }),
      prepare: vi.fn().mockResolvedValue({ code: 0, data: { health: 'ready' } }),
      speak: vi.fn(() => new Promise(resolve => { finishSpeech = resolve; })),
      stop: vi.fn().mockResolvedValue({ code: 0 }),
      release: vi.fn().mockResolvedValue({ code: 0 }),
    };
    const desktop = createSpeechService({ synth: null, Utterance: null, backend, storage });
    await desktop.initialize();
    desktop.setVoice('pack:haibara-jp');

    const enabling = desktop.setEnabled(true);
    expect(desktop.getState()).toMatchObject({ enabled: false, status: 'loading_gpu' });
    await enabling;
    expect(backend.prepare).toHaveBeenCalledWith('pack:haibara-jp');
    expect(desktop.getState()).toMatchObject({ enabled: true, status: 'ready' });

    desktop.enqueue('こんにちは');
    expect(backend.speak).toHaveBeenCalledWith('こんにちは', {
      voiceURI: '', voiceKey: 'pack:haibara-jp', rate: 1, volume: 1,
    });
    finishSpeech({ code: 0 });
    await vi.waitFor(() => expect(desktop.getState().status).toBe('ready'));
    await desktop.setEnabled(false);
    expect(backend.release).toHaveBeenCalled();
  });

  it('keeps personalized speech disabled when GPU preparation fails', async () => {
    const backend = {
      getCapabilities: vi.fn().mockResolvedValue({ code: 0, data: { supported: true, voices: [] } }),
      listVoicePacks: vi.fn().mockResolvedValue({ code: 0, data: [{
        voice_key: 'pack:test', display_name: '测试', health: 'ready', selectable: true,
      }] }),
      prepare: vi.fn().mockResolvedValue({ code: -1, msg: 'CUDA 显存不足' }),
    };
    const desktop = createSpeechService({ synth: null, Utterance: null, backend, storage });
    await desktop.initialize();
    desktop.setVoice('pack:test');

    expect(await desktop.setEnabled(true)).toBe(false);
    expect(desktop.getState()).toMatchObject({ enabled: false, status: 'gpu_error', error: 'CUDA 显存不足' });
  });

  it('releases an active GPU voice when switching to a system voice', async () => {
    const backend = {
      getCapabilities: vi.fn().mockResolvedValue({ code: 0, data: { supported: true, voices: [{ voiceURI: 'default', name: 'Default' }] } }),
      listVoicePacks: vi.fn().mockResolvedValue({ code: 0, data: [{
        voice_key: 'pack:test', display_name: '测试', health: 'ready', selectable: true,
      }] }),
      prepare: vi.fn().mockResolvedValue({ code: 0, data: { health: 'ready' } }),
      stop: vi.fn().mockResolvedValue({ code: 0 }),
      release: vi.fn().mockResolvedValue({ code: 0 }),
    };
    const desktop = createSpeechService({ synth: null, Utterance: null, backend, storage });
    await desktop.initialize();
    desktop.setVoice('pack:test');
    await desktop.setEnabled(true);

    desktop.setVoice('system:default');

    await vi.waitFor(() => expect(backend.release).toHaveBeenCalledTimes(1));
    expect(desktop.getState()).toMatchObject({ enabled: false, selectedVoiceKey: 'system:default' });
  });

  it('waits for desktop stop confirmation before speaking the item after skip', async () => {
    let finishStop;
    const backend = {
      getCapabilities: vi.fn().mockResolvedValue({
        code: 0,
        data: { supported: true, voices: [] },
      }),
      speak: vi.fn(() => new Promise(() => {})),
      stop: vi.fn(() => new Promise((resolve) => {
        finishStop = resolve;
      })),
    };
    const desktop = createSpeechService({
      synth: null,
      Utterance: null,
      backend,
      storage,
      now: () => clock,
    });
    await desktop.initialize();
    desktop.setEnabled(true);
    desktop.enqueue('第一条');
    desktop.enqueue('第二条');

    desktop.skip();
    expect(backend.speak).toHaveBeenCalledTimes(1);

    await vi.waitFor(() => {
      expect(backend.stop).toHaveBeenCalledTimes(1);
    });
    finishStop({ code: 0 });
    await vi.waitFor(() => {
      expect(backend.speak).toHaveBeenCalledTimes(2);
    });
  });
});
