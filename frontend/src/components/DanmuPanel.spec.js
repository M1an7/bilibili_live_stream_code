// @vitest-environment jsdom
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => {
  const state = {
    supported: true,
    enabled: true,
    status: 'ready',
    voices: [{ name: '系统默认', voiceURI: 'default', voiceKey: 'system:default', kind: 'system', lang: 'zh-CN', default: true }],
    systemVoices: [{ name: '系统默认', voiceURI: 'default', voiceKey: 'system:default', kind: 'system', lang: 'zh-CN', default: true }],
    voicePacks: [],
    selectedVoiceKey: 'system:default',
    selectedVoiceURI: 'default',
    rate: 1,
    volume: 1,
    queueLength: 0,
    error: '',
  };
  return {
    startDanmuMonitor: vi.fn(),
    sendDanmu: vi.fn(),
    speechService: {
      getState: vi.fn(() => ({ ...state })),
      subscribe: vi.fn((listener) => {
        listener({ ...state });
        return () => {};
      }),
      refreshVoices: vi.fn(),
      refreshVoicePacks: vi.fn(),
      refreshAivmxVoices: vi.fn().mockResolvedValue([]),
      setEnabled: vi.fn().mockResolvedValue(true),
      setVoice: vi.fn(),
      setRate: vi.fn(),
      setVolume: vi.fn(),
      skip: vi.fn(),
      enqueue: vi.fn(() => true),
    },
  };
});

vi.mock('@/api/bridge', () => ({
  useBridge: () => ({
    startDanmuMonitor: mocks.startDanmuMonitor,
    sendDanmu: mocks.sendDanmu,
    chooseVoiceSource: vi.fn(),
    startVoicePackBuild: vi.fn(),
    getVoiceJob: vi.fn(),
    cancelVoiceJob: vi.fn(),
  }),
}));

vi.mock('@/services/speechService', () => ({
  speechService: mocks.speechService,
}));

import DanmuPanel from './DanmuPanel.vue';
import SpeechToolbar from './SpeechToolbar.vue';
import VoiceImportModal from './VoiceImportModal.vue';

const Host = {
  components: { DanmuPanel },
  template: '<KeepAlive><DanmuPanel /></KeepAlive>',
};

describe('DanmuPanel speech integration', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.onDanmuMessage = null;
  });

  it('renders speech controls and routes incoming ordinary danmu', async () => {
    const wrapper = mount(Host);
    await nextTick();

    expect(wrapper.findComponent(SpeechToolbar).exists()).toBe(true);
    expect(mocks.startDanmuMonitor).toHaveBeenCalledOnce();

    window.onDanmuMessage({
      type: 'danmu',
      uname: '测试观众',
      msg: '  你好主播  ',
      face: '',
    });
    await nextTick();

    expect(wrapper.text()).toContain('你好主播');
    expect(mocks.speechService.enqueue).toHaveBeenCalledWith(
      '你好主播',
      { createdAt: expect.any(Number) },
    );
  });

  it('opens the personalized voice import wizard from the toolbar', async () => {
    const wrapper = mount(Host);
    await nextTick();

    wrapper.findComponent(SpeechToolbar).vm.$emit('import-voice');
    await nextTick();

    expect(wrapper.findComponent(VoiceImportModal).props('visible')).toBe(true);
    expect(wrapper.text()).toContain('导入 AIVMX 实时音色');
    expect(wrapper.find('[data-test="voice-mode-cpu"]').classes()).toContain('active');
  });

  it('refreshes a successfully previewed realtime voice without selecting or enabling it', async () => {
    const wrapper = mount(Host);
    await nextTick();
    wrapper.findComponent(SpeechToolbar).vm.$emit('import-voice');
    await nextTick();

    const modal = wrapper.findComponent(VoiceImportModal);
    modal.vm.$emit('refresh-voices', { voice_key: 'aivmx:11111111-2222-4333-8444-555555555555:0' });
    await vi.waitFor(() => expect(mocks.speechService.refreshAivmxVoices).toHaveBeenCalled());

    expect(mocks.speechService.setVoice).not.toHaveBeenCalled();
    expect(mocks.speechService.setEnabled).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(modal.props('visible')).toBe(false));
  });
});
