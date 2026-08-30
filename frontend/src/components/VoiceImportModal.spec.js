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
});


describe('VoiceImportModal', () => {
  it('submits a v2Pro Japanese training-result build', async () => {
    const api = bridge();
    const wrapper = mount(VoiceImportModal, { props: { visible: true, bridge: api } });

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
    await vi.waitFor(() => expect(wrapper.text()).toContain('等待安装 CPU 运行时'));
    expect(wrapper.emitted('installed')).toHaveLength(1);
  });

  it('uses the desktop native picker and keeps cancellation harmless', async () => {
    const api = bridge();
    api.chooseVoiceSource
      .mockResolvedValueOnce({ code: 0, data: { path: 'D:/voice/GPT.ckpt' } })
      .mockResolvedValueOnce({ code: 0, data: { path: '' } });
    const wrapper = mount(VoiceImportModal, { props: { visible: true, bridge: api } });

    await wrapper.get('[data-test="pick-gpt"]').trigger('click');
    await vi.waitFor(() => {
      expect(wrapper.get('[data-test="gpt-path"]').element.value).toBe('D:/voice/GPT.ckpt');
    });
    await wrapper.get('[data-test="pick-sovits"]').trigger('click');
    expect(wrapper.get('[data-test="sovits-path"]').element.value).toBe('');
  });

  it('blocks the next step when required model files are missing', async () => {
    const wrapper = mount(VoiceImportModal, { props: { visible: true, bridge: bridge() } });
    await wrapper.get('[data-test="wizard-next"]').trigger('click');
    expect(wrapper.text()).toContain('请选择 GPT .ckpt 文件');
    expect(wrapper.find('[data-test="reference-text"]').exists()).toBe(false);
  });
});
