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
    voices: [{ name: '系统默认', voiceURI: 'default', voiceKey: 'system:default', kind: 'system', lang: 'zh-CN', default: true }],
    systemVoices: [{ name: '系统默认', voiceURI: 'default', voiceKey: 'system:default', kind: 'system', lang: 'zh-CN', default: true }],
    voicePacks: [],
    realtimeVoices: [],
    selectedVoiceKey: 'system:default',
    selectedVoiceURI: 'default',
    rate: 1,
    volume: 1,
    queueLength: 0,
    error: '',
    ...overrides,
  };

  return {
    getState: () => ({ ...state }),
    subscribe: vi.fn((fn) => {
      listener = fn;
      fn({ ...state });
      return () => {};
    }),
    refreshVoices: vi.fn(),
    setEnabled: vi.fn(),
    setVoice: vi.fn(),
    setRate: vi.fn(),
    setVolume: vi.fn(),
    skip: vi.fn(),
    emit: (patch) => {
      Object.assign(state, patch);
      listener?.({ ...state });
    },
  };
};

describe('SpeechToolbar', () => {
  it('forwards enable, voice, rate, volume, and skip controls', async () => {
    const service = makeService();
    const wrapper = mount(SpeechToolbar, { props: { service } });

    await wrapper.get('[data-test="speech-enabled"]').setValue(true);
    await wrapper.get('[data-test="speech-voice"]').setValue('system:default');
    await wrapper.get('[data-test="speech-rate"]').setValue('1.2');
    await wrapper.get('[data-test="speech-volume"]').setValue('0.6');
    await wrapper.get('[data-test="speech-skip"]').trigger('click');

    expect(service.setEnabled).toHaveBeenCalledWith(true);
    expect(service.setVoice).toHaveBeenCalledWith('system:default');
    expect(service.setRate).toHaveBeenCalledWith(1.2);
    expect(service.setVolume).toHaveBeenCalledWith(0.6);
    expect(service.skip).toHaveBeenCalled();
  });

  it('renders service state changes for speaking and queue length', async () => {
    const service = makeService();
    const wrapper = mount(SpeechToolbar, { props: { service } });

    service.emit({ enabled: true, status: 'speaking', queueLength: 2 });
    await wrapper.vm.$nextTick();

    expect(wrapper.get('[data-test="speech-status"]').text()).toContain('正在播报');
    expect(wrapper.get('[data-test="speech-queue"]').text()).toContain('2');
  });

  it('shows GPU preparation states and keeps the switch disabled while loading', async () => {
    const service = makeService({ status: 'loading_gpu' });
    const wrapper = mount(SpeechToolbar, { props: { service } });
    expect(wrapper.get('[data-test="speech-status"]').text()).toContain('启动 GPU');
    expect(wrapper.get('[data-test="speech-enabled"]').attributes('disabled')).toBeDefined();
    expect(wrapper.get('[data-test="speech-voice"]').attributes('disabled')).toBeDefined();

    service.emit({ status: 'gpu_error', error: '显存不足' });
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain('显存不足');
  });

  it('groups realtime CPU voices separately and shows CPU preparation without GPU wording', async () => {
    const service = makeService({
      status: 'loading_cpu',
      voices: [
        { name: '系统默认', voiceKey: 'system:default', kind: 'system', lang: 'zh-CN' },
        { name: '灰原哀实时', voiceKey: 'aivmx:model:0', kind: 'aivmx', lang: 'zh-CN' },
        { name: '高质量音色', voiceKey: 'pack:test', kind: 'pack', lang: 'ja' },
      ],
    });
    const wrapper = mount(SpeechToolbar, { props: { service } });

    expect(wrapper.get('[data-test="speech-status"]').text()).toContain('启动 CPU');
    expect(wrapper.find('optgroup[label="实时 CPU · 零显存"]').exists()).toBe(true);
    expect(wrapper.find('optgroup[label="高质量 GPU"]').exists()).toBe(true);
    expect(wrapper.get('[data-test="speech-enabled"]').attributes('disabled')).toBeDefined();

    service.emit({ status: 'cpu_error', error: 'CPU 运行时缺失' });
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain('CPU 运行时缺失');
  });

  it('shows an unsupported explanation and disables enable control', () => {
    const wrapper = mount(SpeechToolbar, {
      props: {
        service: makeService({ supported: false, error: '系统语音不可用' }),
      },
    });

    expect(wrapper.text()).toContain('系统语音不可用');
    expect(wrapper.get('[data-test="speech-enabled"]').attributes('disabled')).toBeDefined();
  });

  it('shows simulation only in development mode', async () => {
    const hidden = mount(SpeechToolbar, {
      props: { service: makeService(), devMode: false },
    });
    expect(hidden.find('[data-test="simulate-danmu"]').exists()).toBe(false);

    const visible = mount(SpeechToolbar, {
      props: { service: makeService(), devMode: true },
    });
    await visible.get('[data-test="simulate-danmu"]').trigger('click');
    expect(visible.emitted('simulate')).toHaveLength(1);
  });

  it('places an import button beside the voice selector', async () => {
    const wrapper = mount(SpeechToolbar, { props: { service: makeService() } });

    await wrapper.get('[data-test="import-voice"]').trigger('click');

    expect(wrapper.emitted('import-voice')).toHaveLength(1);
  });
});
