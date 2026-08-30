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

  it('renders service state changes for speaking and queue length', async () => {
    const service = makeService();
    const wrapper = mount(SpeechToolbar, { props: { service } });

    service.emit({ enabled: true, status: 'speaking', queueLength: 2 });
    await wrapper.vm.$nextTick();

    expect(wrapper.get('[data-test="speech-status"]').text()).toContain('正在播报');
    expect(wrapper.get('[data-test="speech-queue"]').text()).toContain('2');
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
});
