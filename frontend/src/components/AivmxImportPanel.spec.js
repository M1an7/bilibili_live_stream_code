// @vitest-environment jsdom
import { mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import AivmxImportPanel from './AivmxImportPanel.vue';


const voiceKey = 'aivmx:11111111-2222-4333-8444-555555555555:0';

const makeBridge = () => ({
  chooseVoiceSource: vi.fn().mockResolvedValue({ code: 0, data: { path: 'D:/voice/haibara.aivmx' } }),
  inspectAivmx: vi.fn().mockResolvedValue({
    code: 0,
    data: {
      model_uuid: '11111111-2222-4333-8444-555555555555',
      display_name: '灰原哀实时音色',
      creators: ['M1an7'],
      license: '已授权训练、合成与公开直播',
      architecture: 'Style-Bert-VITS2',
      languages: ['ja', 'zh-CN'],
      styles: [{ name: 'Neutral', style_id: 0, voice_key: voiceKey }],
      sha256: `sha256:${'a'.repeat(64)}`,
      size_bytes: 196733341,
    },
  }),
  startAivmxInstall: vi.fn().mockResolvedValue({ code: 0, data: { job_id: 'aivmx-job' } }),
  getAivmxJob: vi.fn().mockResolvedValue({
    code: 0,
    data: { status: 'completed', progress: 100, message: '导入完成', result: { voice_key: voiceKey } },
  }),
  listAivmxVoices: vi.fn().mockResolvedValue({ code: 0, data: [] }),
  getCpuRuntimeStatus: vi.fn().mockResolvedValue({
    code: 0,
    data: { state: 'ready', runtime_root: 'D:/BiliLiveRuntime/.cpu', process: { state: 'stopped', metrics: { vram_mb: 0 } } },
  }),
  chooseCpuRuntimeSource: vi.fn().mockResolvedValue({ code: 0, data: { path: 'D:/packages/cpu-runtime.zip' } }),
  startCpuRuntimeInstall: vi.fn().mockResolvedValue({ code: 0, data: { job_id: 'cpu-job' } }),
  getCpuRuntimeJob: vi.fn().mockResolvedValue({
    code: 0,
    data: { status: 'completed', progress: 100, message: 'CPU 运行时安装完成' },
  }),
  previewAivmxVoice: vi.fn().mockResolvedValue({
    code: 0,
    data: { health: 'ready', runtime: { metrics: { vram_mb: 0, rss_mb: 1820, first_pcm_ms: 830 } } },
  }),
});


describe('AivmxImportPanel', () => {
  it('inspects one aivmx and displays its model, rights, languages, and zero-vram mode', async () => {
    const bridge = makeBridge();
    const wrapper = mount(AivmxImportPanel, { props: { bridge } });

    await wrapper.get('[data-test="pick-aivmx"]').trigger('click');
    await vi.waitFor(() => expect(wrapper.text()).toContain('灰原哀实时音色'));
    expect(bridge.inspectAivmx).toHaveBeenCalledWith('D:/voice/haibara.aivmx');
    expect(wrapper.text()).toContain('M1an7');
    expect(wrapper.text()).toContain('zh-CN');
    expect(wrapper.text()).toContain('已授权训练、合成与公开直播');
    expect(wrapper.text()).toContain('CPU 推理 · 显存 0 MB');
    expect(wrapper.get('[data-test="install-aivmx"]').attributes('disabled')).toBeDefined();
  });

  it('requires rights confirmation then imports the unchanged aivmx', async () => {
    const bridge = makeBridge();
    const wrapper = mount(AivmxImportPanel, { props: { bridge } });
    await wrapper.get('[data-test="pick-aivmx"]').trigger('click');
    await vi.waitFor(() => expect(wrapper.text()).toContain('灰原哀实时音色'));
    await wrapper.get('[data-test="aivmx-permissions"]').setValue(true);
    await wrapper.get('[data-test="install-aivmx"]').trigger('click');

    expect(bridge.startAivmxInstall).toHaveBeenCalledWith({
      path: 'D:/voice/haibara.aivmx',
      permissions_confirmed: true,
    });
    await vi.waitFor(() => expect(wrapper.text()).toContain('导入完成'));
    expect(wrapper.emitted('installed')?.[0]?.[0]).toMatchObject({ voice_key: voiceKey });
  });

  it('installs the separate cpu runtime and previews an installed voice in Chinese', async () => {
    const bridge = makeBridge();
    bridge.listAivmxVoices.mockResolvedValue({ code: 0, data: [{
      voice_key: voiceKey,
      display_name: '灰原哀实时音色',
      health: 'runtime_required',
      selectable: false,
      message: '等待 CPU 试听验证',
    }] });
    const wrapper = mount(AivmxImportPanel, { props: { bridge } });
    await vi.waitFor(() => expect(wrapper.text()).toContain('灰原哀实时音色'));

    await wrapper.get('[data-test="pick-cpu-runtime-zip"]').trigger('click');
    await vi.waitFor(() => expect(wrapper.get('[data-test="cpu-runtime-path"]').element.value).toContain('cpu-runtime.zip'));
    await wrapper.get('[data-test="install-cpu-runtime"]').trigger('click');
    await vi.waitFor(() => expect(wrapper.text()).toContain('CPU 运行时安装完成'));

    await wrapper.get('[data-test="preview-aivmx-0"]').trigger('click');
    await vi.waitFor(() => expect(bridge.previewAivmxVoice).toHaveBeenCalledWith(voiceKey, '准备完成，可以开始播报。'));
    expect(wrapper.text()).toContain('内存 1820 MB');
    expect(wrapper.text()).toContain('首包 830 ms');
    expect(wrapper.text()).toContain('显存 0 MB');
  });

  it('offers a ready voice for refreshing the live speech dropdown', async () => {
    const bridge = makeBridge();
    const readyVoice = {
      voice_key: voiceKey,
      display_name: '灰原哀实时音色',
      health: 'ready',
      selectable: true,
      message: '中文试听验证通过',
    };
    bridge.listAivmxVoices.mockResolvedValue({ code: 0, data: [readyVoice] });
    const wrapper = mount(AivmxImportPanel, { props: { bridge } });

    await vi.waitFor(() => expect(wrapper.text()).toContain('刷新音色下拉框'));
    await wrapper.get('[data-test="refresh-aivmx-0"]').trigger('click');

    expect(wrapper.emitted('refresh-voices')?.[0]?.[0]).toEqual(readyVoice);
  });
});
