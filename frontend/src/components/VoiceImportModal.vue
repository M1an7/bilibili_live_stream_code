<script setup>
import { nextTick, onUnmounted, reactive, ref, watch } from 'vue';

const props = defineProps({
  visible: { type: Boolean, default: false },
  bridge: { type: Object, required: true },
});

const emit = defineEmits(['close', 'installed']);
const step = ref(1);
const error = ref('');
const previousFocus = ref(null);
const closeButton = ref(null);
const form = reactive({
  gptPath: '',
  sovitsPath: '',
  modelVersion: 'v2Pro',
  referenceAudioPath: '',
  referenceText: '',
  displayName: '',
  voiceId: '',
  licensePath: '',
  permissionsConfirmed: false,
});
const job = reactive({
  id: '',
  status: 'idle',
  stage: '',
  progress: 0,
  message: '',
  result: null,
});
const runtime = reactive({
  sourceType: 'zip',
  sourcePath: '',
  root: '',
  state: 'checking',
  id: '',
  status: 'idle',
  phase: '',
  progress: 0,
  message: '',
  metrics: null,
});
let pollTimer = null;
let runtimePollTimer = null;

const reset = () => {
  step.value = 1;
  error.value = '';
  Object.assign(form, {
    gptPath: '',
    sovitsPath: '',
    modelVersion: 'v2Pro',
    referenceAudioPath: '',
    referenceText: '',
    displayName: '',
    voiceId: '',
    licensePath: '',
    permissionsConfirmed: false,
  });
  Object.assign(job, { id: '', status: 'idle', stage: '', progress: 0, message: '', result: null });
  Object.assign(runtime, {
    sourceType: 'zip', sourcePath: '', state: 'checking', id: '', status: 'idle',
    phase: '', progress: 0, message: '', metrics: null,
  });
};

const refreshRuntimeStatus = async () => {
  if (typeof props.bridge.getGpuRuntimeStatus !== 'function') return;
  const response = await props.bridge.getGpuRuntimeStatus();
  if (response?.code !== 0) {
    runtime.state = 'missing';
    runtime.message = response?.msg || '无法读取 GPU 运行时状态';
    return;
  }
  runtime.state = response.data?.state || 'missing';
  runtime.root = response.data?.runtime_root || runtime.root;
};

watch(() => props.visible, async (visible) => {
  if (visible) {
    previousFocus.value = document.activeElement;
    reset();
    await refreshRuntimeStatus();
    await nextTick();
    closeButton.value?.focus();
  }
});

const hasExtension = (path, extension) => String(path).toLowerCase().endsWith(extension);

const validateStep = () => {
  error.value = '';
  if (step.value === 1) {
    if (!hasExtension(form.gptPath, '.ckpt')) error.value = '请选择 GPT .ckpt 文件';
    else if (!hasExtension(form.sovitsPath, '.pth')) error.value = '请选择 SoVITS .pth 文件';
    else if (!['v2Pro', 'v2ProPlus'].includes(form.modelVersion)) error.value = '仅支持 v2Pro 或 v2ProPlus';
  } else if (step.value === 2) {
    if (!hasExtension(form.referenceAudioPath, '.wav')) error.value = '请选择 PCM WAV 参考音频';
    else if (!form.referenceText.trim()) error.value = '请填写与参考音频完全对应的日文台词';
  } else if (step.value === 3) {
    if (!form.displayName.trim()) error.value = '请填写音色显示名称';
    else if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(form.voiceId)) error.value = '音色 ID 只能使用小写字母、数字和连字符';
    else if (!form.licensePath.trim()) error.value = '请选择授权说明文件';
    else if (!form.permissionsConfirmed) error.value = '请确认已获得训练、合成语音和公开直播权限';
  }
  return !error.value;
};

const nextStep = () => {
  if (validateStep()) step.value = Math.min(4, step.value + 1);
};

const previousStep = () => {
  error.value = '';
  step.value = Math.max(1, step.value - 1);
};

const choose = async (kind, field) => {
  error.value = '';
  const result = await props.bridge.chooseVoiceSource(kind);
  if (result?.code !== 0) {
    error.value = result?.msg || '无法打开文件选择器';
    return;
  }
  const selected = result?.data?.path || '';
  if (selected) form[field] = selected;
};

const updateJob = (next) => {
  job.status = next.status || job.status;
  job.stage = next.stage || '';
  job.progress = Number(next.progress) || 0;
  job.message = next.message || '';
  job.result = next.result || null;
};

const pollJob = async () => {
  if (!job.id) return;
  const response = await props.bridge.getVoiceJob(job.id);
  if (response?.code !== 0) {
    job.status = 'failed';
    error.value = response?.msg || '读取导入进度失败';
    return;
  }
  updateJob(response.data || {});
  if (job.status === 'completed') {
    emit('installed', job.result);
    await refreshRuntimeStatus();
    return;
  }
  if (job.status === 'failed' || job.status === 'cancelled') {
    error.value = response.data?.error?.message || job.message || '音色导入失败';
    return;
  }
  pollTimer = window.setTimeout(pollJob, 250);
};

const chooseRuntime = async (sourceType) => {
  error.value = '';
  const response = await props.bridge.chooseRuntimeSource(sourceType);
  if (response?.code !== 0) {
    error.value = response?.msg || '无法选择 GPU 运行时';
    return;
  }
  const selected = response?.data?.path || '';
  if (selected) {
    runtime.sourceType = sourceType;
    runtime.sourcePath = selected;
  }
};

const chooseRuntimeRoot = async () => {
  const response = await props.bridge.chooseRuntimeSource('data_root');
  if (response?.code !== 0 || !response?.data?.path) return;
  const configured = await props.bridge.configureRuntimeRoot(response.data.path);
  if (configured?.code !== 0) {
    error.value = configured?.msg || '无法设置 GPU 运行时数据目录';
    return;
  }
  runtime.root = configured.data?.runtime_root || response.data.path;
  runtime.state = configured.data?.state || 'missing';
};

const pollRuntimeJob = async () => {
  if (!runtime.id) return;
  const response = await props.bridge.getRuntimeJob(runtime.id);
  if (response?.code !== 0) {
    runtime.status = 'failed';
    error.value = response?.msg || '读取 GPU 运行时安装进度失败';
    return;
  }
  const next = response.data || {};
  runtime.status = next.status || runtime.status;
  runtime.phase = next.phase || '';
  runtime.progress = Number(next.progress) || 0;
  runtime.message = next.message || '';
  if (runtime.status === 'completed') {
    runtime.state = 'ready';
    await refreshRuntimeStatus();
    return;
  }
  if (runtime.status === 'failed') {
    error.value = next.error?.message || runtime.message || 'GPU 运行时安装失败';
    return;
  }
  runtimePollTimer = window.setTimeout(pollRuntimeJob, 300);
};

const installRuntime = async () => {
  error.value = '';
  if (!runtime.sourcePath) {
    error.value = '请选择 GPU 运行时 ZIP 或目录';
    return;
  }
  runtime.status = 'queued';
  runtime.message = '正在创建 GPU 运行时安装任务';
  const response = await props.bridge.startRuntimeInstall({
    source_type: runtime.sourceType,
    path: runtime.sourcePath,
  });
  if (response?.code !== 0) {
    runtime.status = 'failed';
    error.value = response?.msg || '无法启动 GPU 运行时安装';
    return;
  }
  runtime.id = response.data?.job_id || '';
  await pollRuntimeJob();
};

const prepareImportedVoice = async () => {
  error.value = '';
  runtime.status = 'preparing';
  runtime.message = '正在启动 GPU、加载音色并生成试听';
  const response = await props.bridge.prepareVoice(`pack:${form.voiceId}`);
  if (response?.code !== 0) {
    runtime.status = 'failed';
    error.value = response?.msg || 'GPU 音色准备失败';
    return;
  }
  runtime.status = 'ready';
  runtime.state = 'ready';
  runtime.message = '音色已通过真实 GPU 合成验证，可以用于弹幕播报';
  runtime.metrics = response.data?.runtime?.metrics || null;
  emit('installed', { voice_id: form.voiceId, health: 'ready' });
};

const submit = async () => {
  error.value = '';
  job.status = 'queued';
  job.message = '正在创建导入任务';
  const response = await props.bridge.startVoicePackBuild({
    voice_id: form.voiceId.trim(),
    display_name: form.displayName.trim(),
    model_version: form.modelVersion,
    gpt_path: form.gptPath,
    sovits_path: form.sovitsPath,
    reference_audio_path: form.referenceAudioPath,
    reference_text: form.referenceText.trim(),
    license_path: form.licensePath,
    source_language: 'ja',
    supported_output_languages: ['ja'],
  });
  if (response?.code !== 0) {
    job.status = 'failed';
    error.value = response?.msg || '无法启动音色导入';
    return;
  }
  job.id = response.data?.job_id || '';
  await pollJob();
};

const cancelJob = async () => {
  if (!job.id) return;
  await props.bridge.cancelVoiceJob(job.id);
  job.message = '正在取消导入';
};

const close = async () => {
  if (pollTimer) window.clearTimeout(pollTimer);
  emit('close');
  await nextTick();
  previousFocus.value?.focus?.();
};

onUnmounted(() => {
  if (pollTimer) window.clearTimeout(pollTimer);
  if (runtimePollTimer) window.clearTimeout(runtimePollTimer);
});
</script>

<template>
  <div v-if="visible" class="voice-modal-overlay" @click.self="close">
    <section class="voice-modal" role="dialog" aria-modal="true" aria-labelledby="voice-import-title">
      <header class="modal-header">
        <div>
          <span class="eyebrow">个性化音色</span>
          <h2 id="voice-import-title">从 GPT-SoVITS 训练结果创建</h2>
        </div>
        <button ref="closeButton" class="icon-button" type="button" aria-label="关闭" @click="close">×</button>
      </header>

      <ol class="stepper" aria-label="导入进度">
        <li v-for="item in 4" :key="item" :class="{ active: step === item, done: step > item }">
          <span>{{ step > item ? '✓' : item }}</span>
          {{ ['模型文件', '参考音频', '音色信息', '校验安装'][item - 1] }}
        </li>
      </ol>

      <div class="modal-body">
        <div v-if="step === 1" class="form-step">
          <p class="step-intro">选择训练完成后的两个权重文件。权重只会作为不透明文件复制和校验，不会由主程序直接加载。</p>
          <label>GPT 权重（.ckpt）</label>
          <div class="file-row">
            <input v-model.trim="form.gptPath" data-test="gpt-path" type="text" placeholder="选择 GPT_*.ckpt">
            <button data-test="pick-gpt" type="button" @click="choose('gpt', 'gptPath')">浏览</button>
          </div>
          <label>SoVITS 权重（.pth）</label>
          <div class="file-row">
            <input v-model.trim="form.sovitsPath" data-test="sovits-path" type="text" placeholder="选择 SoVITS_*.pth">
            <button data-test="pick-sovits" type="button" @click="choose('sovits', 'sovitsPath')">浏览</button>
          </div>
          <label>模型版本</label>
          <select v-model="form.modelVersion" data-test="model-version">
            <option value="v2Pro">v2Pro</option>
            <option value="v2ProPlus">v2ProPlus</option>
          </select>
        </div>

        <div v-else-if="step === 2" class="form-step">
          <p class="step-intro">建议使用 3–10 秒、单人干声、无背景音乐的 PCM WAV，并填写完全对应的日文台词。</p>
          <label>参考音频（.wav）</label>
          <div class="file-row">
            <input v-model.trim="form.referenceAudioPath" data-test="reference-audio-path" type="text" placeholder="reference.wav">
            <button data-test="pick-reference" type="button" @click="choose('reference', 'referenceAudioPath')">浏览</button>
          </div>
          <label>参考日文台词</label>
          <textarea v-model="form.referenceText" data-test="reference-text" rows="4" placeholder="输入音频中逐字对应的日文"></textarea>
          <span class="language-chip">输出语言：日本語（ja）</span>
        </div>

        <div v-else-if="step === 3" class="form-step">
          <p class="step-intro">授权文件不会被程序判定法律效力，但会随音色包保存，便于后续核对。</p>
          <div class="two-columns">
            <div>
              <label>显示名称</label>
              <input v-model.trim="form.displayName" data-test="display-name" type="text" placeholder="例如：日语角色音色">
            </div>
            <div>
              <label>音色 ID</label>
              <input v-model.trim="form.voiceId" data-test="voice-id" type="text" placeholder="haibara-jp">
            </div>
          </div>
          <label>授权说明文件</label>
          <div class="file-row">
            <input v-model.trim="form.licensePath" data-test="license-path" type="text" placeholder="LICENSE.txt / 授权说明.pdf">
            <button data-test="pick-license" type="button" @click="choose('license', 'licensePath')">浏览</button>
          </div>
          <label class="permission-check">
            <input v-model="form.permissionsConfirmed" data-test="permissions-confirmed" type="checkbox">
            <span>我确认已获得 AI 训练、合成语音与公开直播使用权限</span>
          </label>
        </div>

        <div v-else class="form-step summary-step">
          <div class="summary-grid">
            <span>音色</span><strong>{{ form.displayName }}</strong>
            <span>标识</span><code>{{ form.voiceId }}</code>
            <span>版本</span><strong>{{ form.modelVersion }}</strong>
            <span>语言</span><strong>日本語（ja）</strong>
            <span>参考台词</span><strong class="summary-text">{{ form.referenceText }}</strong>
          </div>
          <div v-if="job.status !== 'idle'" class="job-panel" :class="`job-${job.status}`">
            <div class="job-head"><strong>{{ job.message }}</strong><span>{{ job.progress }}%</span></div>
            <div class="progress-track"><span :style="{ width: `${job.progress}%` }"></span></div>
            <p v-if="job.status === 'completed'">文件已安全导入。安装独立 GPU 运行时并完成真实试听后即可启用。</p>
          </div>
          <div v-else class="safety-note">音色包与 GPU 运行时分开存放；主程序关闭或停用个性化语音后会释放显存。</div>

          <section class="runtime-panel" data-test="runtime-panel">
            <div class="runtime-title">
              <div><strong>独立 GPU 运行时</strong><span>CUDA 12.6 · FP16 · RTX 3060</span></div>
              <span class="runtime-state" :class="`runtime-${runtime.state}`">{{ runtime.state === 'ready' ? '已安装' : '待安装' }}</span>
            </div>
            <p>运行时单独放在数据盘，不打进桌面 EXE；只有使用个性化音色时才占用显存。</p>
            <div class="runtime-root-row">
              <span>数据目录：{{ runtime.root || '应用数据目录 / runtimes' }}</span>
              <button type="button" @click="chooseRuntimeRoot">更换数据盘</button>
            </div>
            <div class="file-row">
              <input v-model.trim="runtime.sourcePath" data-test="runtime-path" type="text" placeholder="选择 GPU 运行时 .zip 或解压目录">
              <button data-test="pick-runtime-zip" type="button" @click="chooseRuntime('zip')">ZIP</button>
              <button data-test="pick-runtime-directory" type="button" @click="chooseRuntime('directory')">目录</button>
            </div>
            <div v-if="runtime.status !== 'idle'" class="runtime-progress">
              <div><strong>{{ runtime.message }}</strong><span v-if="runtime.progress">{{ runtime.progress }}%</span></div>
              <div v-if="runtime.progress" class="progress-track"><span :style="{ width: `${runtime.progress}%` }"></span></div>
            </div>
            <div v-if="runtime.metrics" class="metric-row">
              <span>峰值显存 {{ runtime.metrics.peak_vram_mb || 0 }} MB</span>
              <span>首包 {{ runtime.metrics.first_pcm_ms || 0 }} ms</span>
            </div>
            <div class="runtime-actions">
              <button data-test="runtime-install" class="secondary" type="button" :disabled="runtime.status === 'queued' || runtime.status === 'running'" @click="installRuntime">安装运行时</button>
              <button data-test="prepare-voice" class="primary" type="button" :disabled="!form.voiceId || runtime.status === 'preparing'" @click="prepareImportedVoice">GPU 试听并启用</button>
            </div>
          </section>
        </div>

        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      </div>

      <footer class="modal-actions">
        <button v-if="step > 1 && job.status !== 'running' && job.status !== 'queued'" class="secondary" type="button" @click="previousStep">上一步</button>
        <span class="action-spacer"></span>
        <button v-if="step < 4" data-test="wizard-next" class="primary" type="button" @click="nextStep">下一步</button>
        <button v-else-if="job.status === 'running' || job.status === 'queued'" class="secondary danger" type="button" @click="cancelJob">取消导入</button>
        <button v-else-if="job.status === 'completed'" class="primary" type="button" @click="close">完成</button>
        <button v-else data-test="voice-build-submit" class="primary" type="button" @click="submit">校验并安装</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.voice-modal-overlay { position: fixed; inset: 0; z-index: 2400; display: grid; place-items: center; padding: 22px; background: rgba(15, 23, 42, .48); backdrop-filter: blur(4px); }
.voice-modal { width: min(720px, calc(100vw - 44px)); max-height: calc(100vh - 44px); overflow: hidden; display: flex; flex-direction: column; border-radius: 18px; background: #fff; color: #243247; box-shadow: 0 24px 70px rgba(15, 23, 42, .28); }
.modal-header { display: flex; align-items: flex-start; justify-content: space-between; padding: 20px 24px 14px; }
.eyebrow { color: #008fc4; font-size: 11px; font-weight: 700; letter-spacing: .08em; }
h2 { margin: 4px 0 0; color: #0f172a; font-size: 19px; }
.icon-button { width: 31px; height: 31px; border: 0; border-radius: 9px; background: #f1f5f9; color: #64748b; font-size: 22px; cursor: pointer; }
.stepper { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 0; padding: 0 24px 15px; list-style: none; }
.stepper li { display: flex; align-items: center; gap: 6px; color: #94a3b8; font-size: 11px; white-space: nowrap; }
.stepper li span { display: grid; place-items: center; width: 21px; height: 21px; border-radius: 50%; background: #e9eff5; color: #64748b; font-weight: 700; }
.stepper li.active, .stepper li.done { color: #008fc4; }
.stepper li.active span, .stepper li.done span { background: #00aeec; color: #fff; }
.modal-body { min-height: 300px; overflow: auto; padding: 20px 24px; border-block: 1px solid #e8eef4; background: #f8fbfd; }
.form-step { display: flex; flex-direction: column; gap: 9px; }
.step-intro { margin: 0 0 4px; color: #64748b; font-size: 12px; line-height: 1.55; }
label { color: #334155; font-size: 12px; font-weight: 650; }
input[type="text"], textarea, select { box-sizing: border-box; width: 100%; border: 1px solid #d9e4ed; border-radius: 9px; outline: 0; background: #fff; color: #1e293b; font: inherit; font-size: 13px; }
input[type="text"], select { height: 37px; padding: 0 11px; }
textarea { resize: vertical; padding: 10px 11px; line-height: 1.5; }
input:focus, textarea:focus, select:focus { border-color: #00aeec; box-shadow: 0 0 0 3px rgba(0, 174, 236, .1); }
.file-row { display: grid; grid-template-columns: 1fr auto; gap: 8px; }
.file-row button, .primary, .secondary { border: 0; border-radius: 9px; padding: 0 15px; cursor: pointer; font: inherit; font-size: 12px; font-weight: 650; }
.file-row button, .secondary { background: #e8f5fb; color: #008fc4; }
.two-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.two-columns > div { display: flex; flex-direction: column; gap: 8px; }
.permission-check { display: flex; align-items: flex-start; gap: 9px; margin-top: 4px; padding: 11px; border-radius: 9px; background: #eef8fc; font-weight: 500; line-height: 1.45; }
.permission-check input { margin-top: 2px; accent-color: #00aeec; }
.language-chip { align-self: flex-start; padding: 5px 9px; border-radius: 999px; background: #e8f5fb; color: #008fc4; font-size: 11px; }
.summary-grid { display: grid; grid-template-columns: 90px 1fr; gap: 10px 14px; padding: 15px; border: 1px solid #e1eaf1; border-radius: 12px; background: #fff; font-size: 12px; }
.summary-grid span { color: #64748b; }.summary-grid strong { color: #1e293b; }.summary-text { font-weight: 500; }
.safety-note, .job-panel { padding: 13px; border-radius: 10px; background: #edf8fc; color: #336579; font-size: 12px; line-height: 1.5; }
.runtime-panel { display: flex; flex-direction: column; gap: 9px; margin-top: 4px; padding: 14px; border: 1px solid #d9e8f1; border-radius: 12px; background: #fff; }
.runtime-title, .runtime-title > div, .runtime-root-row, .runtime-actions, .metric-row, .runtime-progress > div { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.runtime-title > div { align-items: flex-start; flex-direction: column; gap: 2px; }.runtime-title span, .runtime-panel p, .runtime-root-row { color: #64748b; font-size: 11px; }.runtime-panel p { margin: 0; line-height: 1.45; }
.runtime-state { padding: 4px 8px; border-radius: 99px; background: #fff7ed; color: #c2410c !important; font-weight: 700; }.runtime-state.runtime-ready { background: #ecfdf5; color: #047857 !important; }
.runtime-root-row { padding: 7px 9px; border-radius: 8px; background: #f8fafc; }.runtime-root-row span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.runtime-root-row button { flex-shrink: 0; border: 0; background: transparent; color: #008fc4; cursor: pointer; }
.runtime-actions { justify-content: flex-end; }.runtime-progress, .metric-row { padding: 9px; border-radius: 8px; background: #f0f9ff; color: #075985; font-size: 11px; }.metric-row { justify-content: flex-start; gap: 18px; }
.job-head { display: flex; justify-content: space-between; gap: 12px; }.job-head strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.progress-track { height: 7px; margin-top: 9px; overflow: hidden; border-radius: 99px; background: #d8e7ed; }.progress-track span { display: block; height: 100%; border-radius: inherit; background: #00aeec; transition: width .2s ease; }
.job-panel p { margin: 9px 0 0; }.job-completed { background: #ecfdf5; color: #047857; }
.form-error { margin: 13px 0 0; padding: 9px 11px; border-radius: 8px; background: #fff1f2; color: #be123c; font-size: 12px; }
.modal-actions { display: flex; align-items: center; min-height: 42px; padding: 14px 24px; }.action-spacer { flex: 1; }
.primary, .secondary { min-height: 34px; padding-inline: 19px; }.primary { background: #00aeec; color: #fff; }.danger { background: #fff1f2; color: #be123c; }
@media (max-width: 620px) { .stepper li { font-size: 0; }.two-columns { grid-template-columns: 1fr; }.voice-modal-overlay { padding: 10px; }.voice-modal { width: calc(100vw - 20px); max-height: calc(100vh - 20px); } }
</style>
