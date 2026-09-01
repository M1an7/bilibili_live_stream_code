// @vitest-environment jsdom
import { mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import VoiceImportModal from './VoiceImportModal.vue';


const bridge = () => ({
  chooseVoiceSource: vi.fn(),
  startVoicePackBuild: vi.fn().mockResolvedValue({ code: 0, data: { job_id: 'job-1' } }),
  getVoiceJob: vi.fn().mockResolvedValue({
    code: 0,
    data: {
      status: 'completed',
      stage: 'done',
      progress: 100,
      message: '文件已安全导入，等待安装 CPU 运行时后试听并启用',
      result: { voice_id: 'haibara-jp', health: 'runtime_required' },
    },
  }),
  cancelVoiceJob: vi.fn(),
  chooseRuntimeSource: vi.fn(),
  configureRuntimeRoot: vi.fn(),
  startRuntimeInstall: vi.fn().mockResolvedValue({ code: 0, data: { job_id: 'runtime-1' } }),
  getRuntimeJob: vi.fn().mockResolvedValue({
    code: 0,
    data: { status: 'completed', phase: 'done', progress: 100, message: 'GPU 运行时安装完成' },
  }),
  getGpuRuntimeStatus: vi.fn().mockResolvedValue({
    code: 0, data: { state: 'ready', runtime_root: 'D:/BiliLiveRuntime', runtimes: [{ runtime_id: 'cu126', precision: 'fp16' }] },
  }),
  listVoicePacks: vi.fn().mockResolvedValue({ code: 0, data: [] }),
  prepareVoice: vi.fn().mockResolvedValue({
    code: 0, data: { health: 'ready', runtime: { metrics: { peak_vram_mb: 820, first_pcm_ms: 680 } } },
  }),
  previewVoice: vi.fn().mockResolvedValue({
    code: 0, data: { health: 'ready', runtime: { metrics: { peak_vram_mb: 820, first_pcm_ms: 680 } } },
  }),
  listAivmxVoices: vi.fn().mockResolvedValue({ code: 0, data: [] }),
  getCpuRuntimeStatus: vi.fn().mockResolvedValue({ code: 0, data: { state: 'missing', process: { state: 'stopped' } } }),
});


describe('VoiceImportModal', () => {
  it('opens on realtime CPU import and preserves the high-quality GPU wizard in a separate tab', async () => {
    const wrapper = mount(VoiceImportModal, { props: { visible: true, bridge: bridge() } });

    expect(wrapper.get('[data-test="voice-mode-cpu"]').classes()).toContain('active');
    expect(wrapper.text()).toContain('导入 AIVMX 实时音色');
    expect(wrapper.find('[data-test="gpt-path"]').exists()).toBe(false);

    await wrapper.get('[data-test="voice-mode-gpu"]').trigger('click');
    expect(wrapper.text()).toContain('从 GPT-SoVITS 训练结果创建');
    expect(wrapper.find('[data-test="gpt-path"]').exists()).toBe(true);
  });

  it('can GPU-preview an installed pack after the app restarts', async () => {
    const api = bridge();
    api.listVoicePacks.mockResolvedValue({ code: 0, data: [{
      voice_id: 'haibara-jp',
      voice_key: 'pack:haibara-jp',
      display_name: '灰原哀（日语）',
      health: 'runtime_required',
      selectable: false,
      message: '音色已导入，等待兼容的 GPU 运行时',
    }] });
    const wrapper = mount(VoiceImportModal, { props: { visible: true, bridge: api } });

    await wrapper.get('[data-test="voice-mode-gpu"]').trigger('click');

    await vi.waitFor(() => expect(wrapper.text()).toContain('已安装音色'));
    expect(wrapper.text()).toContain('灰原哀（日语）');
    await wrapper.get('[data-test="prepare-existing-haibara-jp"]').trigger('click');

    await vi.waitFor(() => expect(api.previewVoice).toHaveBeenCalledWith('pack:haibara-jp'));
    expect(wrapper.emitted('installed')?.at(-1)?.[0]).toMatchObject({ voice_id: 'haibara-jp', health: 'ready' });
  });

  it('relays a CPU voice dropdown refresh without activating speech', async () => {
    const api = bridge();
    api.listAivmxVoices.mockResolvedValue({ code: 0, data: [{
      voice_key: 'aivmx:11111111-2222-4333-8444-555555555555:0',
      display_name: '灰原哀实时音色',
      health: 'ready',
      selectable: true,
      message: '中文试听验证通过',
    }] });
    const wrapper = mount(VoiceImportModal, { props: { visible: true, bridge: api } });

    await vi.waitFor(() => expect(wrapper.text()).toContain('刷新音色下拉框'));
    await wrapper.get('[data-test="refresh-aivmx-0"]').trigger('click');

    expect(wrapper.emitted('refresh-voices')).toHaveLength(1);
  });

  it('submits a v2Pro Japanese training-result build', async () => {
    const api = bridge();
    const wrapper = mount(VoiceImportModal, { props: { visible: true, bridge: api } });

    await wrapper.get('[data-test="voice-mode-gpu"]').trigger('click');

    await wrapper.get('[data-test="gpt-path"]').setValue('D:/voice/GPT.ckpt');
    await wrapper.get('[data-test="sovits-path"]').setValue('D:/voice/SoVITS.pth');
    await wrapper.get('[data-test="wizard-next"]').trigger('click');
    await wrapper.get('[data-test="reference-audio-path"]').setValue('D:/voice/reference.wav');
    await wrapper.get('[data-test="reference-text"]').setValue('今日何食べたい？');
    await wrapper.get('[data-test="wizard-next"]').trigger('click');
    await wrapper.get('[data-test="display-name"]').setValue('灰原哀（日语）');
    await wrapper.get('[data-test="voice-id"]').setValue('haibara-jp');
    await wrapper.get('[data-test="license-path"]').setValue('D:/voice/LICENSE.txt');
    await wrapper.get('[data-test="permissions-confirmed"]').setValue(true);
    await wrapper.get('[data-test="wizard-next"]').trigger('click');
    await wrapper.get('[data-test="voice-build-submit"]').trigger('click');

    expect(api.startVoicePackBuild).toHaveBeenCalledWith(expect.objectContaining({
      model_version: 'v2Pro',
      source_language: 'ja',
      supported_output_languages: ['ja'],
      voice_id: 'haibara-jp',
    }));
    await vi.waitFor(() => expect(wrapper.emitted('installed')).toHaveLength(1));
    expect(wrapper.text()).toContain('GPU 运行时');
  });

  it('installs a separate GPU runtime then prepares the imported voice', async () => {
    const api = bridge();
    api.chooseRuntimeSource.mockResolvedValue({ code: 0, data: { path: 'D:/packages/gpu-runtime.zip' } });
    const wrapper = mount(VoiceImportModal, { props: { visible: true, bridge: api } });
    await wrapper.get('[data-test="voice-mode-gpu"]').trigger('click');
    Object.assign(wrapper.vm.form, {
      gptPath: 'D:/v/a.ckpt', sovitsPath: 'D:/v/a.pth', referenceAudioPath: 'D:/v/r.wav',
      referenceText: '今日何食べたい？', displayName: '灰原哀', voiceId: 'haibara-jp',
      licensePath: 'D:/v/LICENSE.txt', permissionsConfirmed: true,
    });
    wrapper.vm.step = 4;
    await wrapper.vm.$nextTick();
    expect(wrapper.get('[data-test="runtime-path"]').element.parentElement.classList.contains('runtime-source-row')).toBe(true);
    await wrapper.get('[data-test="pick-runtime-zip"]').trigger('click');
    await vi.waitFor(() => expect(wrapper.get('[data-test="runtime-path"]').element.value).toContain('gpu-runtime.zip'));
    await wrapper.get('[data-test="runtime-install"]').trigger('click');
    await vi.waitFor(() => expect(wrapper.text()).toContain('GPU 运行时安装完成'));
    await wrapper.get('[data-test="prepare-voice"]').trigger('click');
    await vi.waitFor(() => expect(wrapper.text()).toContain('首包 680 ms'));
    expect(api.previewVoice).toHaveBeenCalledWith('pack:haibara-jp');
    expect(api.prepareVoice).not.toHaveBeenCalled();
  });

  it('uses the desktop native picker and keeps cancellation harmless', async () => {
    const api = bridge();
    api.chooseVoiceSource
      .mockResolvedValueOnce({ code: 0, data: { path: 'D:/voice/GPT.ckpt' } })
      .mockResolvedValueOnce({ code: 0, data: { path: '' } });
    const wrapper = mount(VoiceImportModal, { props: { visible: true, bridge: api } });

    await wrapper.get('[data-test="voice-mode-gpu"]').trigger('click');

    await wrapper.get('[data-test="pick-gpt"]').trigger('click');
    await vi.waitFor(() => {
      expect(wrapper.get('[data-test="gpt-path"]').element.value).toBe('D:/voice/GPT.ckpt');
    });
    await wrapper.get('[data-test="pick-sovits"]').trigger('click');
    expect(wrapper.get('[data-test="sovits-path"]').element.value).toBe('');
  });

  it('blocks the next step when required model files are missing', async () => {
    const wrapper = mount(VoiceImportModal, { props: { visible: true, bridge: bridge() } });
    await wrapper.get('[data-test="voice-mode-gpu"]').trigger('click');
    await wrapper.get('[data-test="wizard-next"]').trigger('click');
    expect(wrapper.text()).toContain('请选择 GPT .ckpt 文件');
    expect(wrapper.find('[data-test="reference-text"]').exists()).toBe(false);
  });
});
