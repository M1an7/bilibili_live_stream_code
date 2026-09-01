<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue';

const props = defineProps({
  bridge: { type: Object, required: true },
});
const emit = defineEmits(['installed', 'refresh-voices']);

const sourcePath = ref('');
const metadata = ref(null);
const permissionsConfirmed = ref(false);
const installedVoices = ref([]);
const error = ref('');
const activePreview = ref('');
const job = reactive({ id: '', status: 'idle', progress: 0, message: '' });
const runtime = reactive({
  state: 'checking',
  root: '',
  sourceType: 'zip',
  sourcePath: '',
  jobId: '',
  jobStatus: 'idle',
  progress: 0,
  message: '',
  metrics: null,
});
let voicePollTimer = null;
let runtimePollTimer = null;

const formatSize = bytes => `${(Number(bytes || 0) / 1024 / 1024).toFixed(1)} MB`;

const refreshVoices = async () => {
  if (typeof props.bridge.listAivmxVoices !== 'function') return;
  const response = await props.bridge.listAivmxVoices();
  installedVoices.value = response?.code === 0 && Array.isArray(response.data) ? response.data : [];
};

const refreshRuntime = async () => {
  if (typeof props.bridge.getCpuRuntimeStatus !== 'function') return;
  const response = await props.bridge.getCpuRuntimeStatus();
  if (response?.code !== 0) {
    runtime.state = 'missing';
    runtime.message = response?.msg || '无法读取 CPU 运行时状态';
    return;
  }
  runtime.state = response.data?.state || 'missing';
  runtime.root = response.data?.runtime_root || '';
  runtime.metrics = response.data?.process?.metrics || runtime.metrics;
};

onMounted(() => Promise.all([refreshVoices(), refreshRuntime()]));

const chooseAivmx = async () => {
  error.value = '';
  const selected = await props.bridge.chooseVoiceSource('aivmx');
  if (selected?.code !== 0) {
    error.value = selected?.msg || '无法选择 AIVMX 文件';
    return;
  }
  const path = selected?.data?.path || '';
  if (!path) return;
  sourcePath.value = path;
  metadata.value = null;
  permissionsConfirmed.value = false;
  const inspected = await props.bridge.inspectAivmx(path);
  if (inspected?.code !== 0) {
    error.value = inspected?.msg || 'AIVMX 音色校验失败';
    return;
  }
  metadata.value = inspected.data;
};

const pollVoiceJob = async () => {
  const response = await props.bridge.getAivmxJob(job.id);
  if (response?.code !== 0) {
    job.status = 'failed';
    error.value = response?.msg || '读取 AIVMX 导入进度失败';
    return;
  }
  const next = response.data || {};
  job.status = next.status || job.status;
  job.progress = Number(next.progress) || 0;
  job.message = next.message || '';
  if (job.status === 'completed') {
    await refreshVoices();
    emit('installed', next.result);
    return;
  }
  if (job.status === 'failed') {
    error.value = next.error?.message || job.message || 'AIVMX 导入失败';
    return;
  }
  voicePollTimer = window.setTimeout(pollVoiceJob, 250);
};

const installAivmx = async () => {
  error.value = '';
  if (!metadata.value || !permissionsConfirmed.value) {
    error.value = '请先选择有效的 AIVMX，并确认使用权限';
    return;
  }
  job.status = 'queued';
  job.message = '正在创建 AIVMX 导入任务';
  const response = await props.bridge.startAivmxInstall({
    path: sourcePath.value,
    permissions_confirmed: true,
  });
  if (response?.code !== 0) {
    job.status = 'failed';
    error.value = response?.msg || '无法启动 AIVMX 导入';
    return;
  }
  job.id = response.data?.job_id || '';
  await pollVoiceJob();
};

const chooseRuntime = async (sourceType) => {
  error.value = '';
  const response = await props.bridge.chooseCpuRuntimeSource(sourceType);
  if (response?.code !== 0) {
    error.value = response?.msg || '无法选择 CPU 运行时';
    return;
  }
  if (response?.data?.path) {
    runtime.sourceType = sourceType;
    runtime.sourcePath = response.data.path;
  }
};

const pollRuntimeJob = async () => {
  const response = await props.bridge.getCpuRuntimeJob(runtime.jobId);
  if (response?.code !== 0) {
    runtime.jobStatus = 'failed';
    error.value = response?.msg || '读取 CPU 运行时安装进度失败';
    return;
  }
  const next = response.data || {};
  runtime.jobStatus = next.status || runtime.jobStatus;
  runtime.progress = Number(next.progress) || 0;
  runtime.message = next.message || '';
  if (runtime.jobStatus === 'completed') {
    await refreshRuntime();
    return;
  }
  if (runtime.jobStatus === 'failed') {
    error.value = next.error?.message || runtime.message || 'CPU 运行时安装失败';
    return;
  }
  runtimePollTimer = window.setTimeout(pollRuntimeJob, 300);
};

const installRuntime = async () => {
  error.value = '';
  if (!runtime.sourcePath) {
    error.value = '请选择 CPU 运行时 ZIP 或目录';
    return;
  }
  runtime.jobStatus = 'queued';
  runtime.message = '正在创建 CPU 运行时安装任务';
  const response = await props.bridge.startCpuRuntimeInstall({
    source_type: runtime.sourceType,
    path: runtime.sourcePath,
  });
  if (response?.code !== 0) {
    runtime.jobStatus = 'failed';
    error.value = response?.msg || '无法启动 CPU 运行时安装';
    return;
  }
  runtime.jobId = response.data?.job_id || '';
  await pollRuntimeJob();
};

const preview = async (voice, index) => {
  error.value = '';
  activePreview.value = voice.voice_key;
  try {
    const response = await props.bridge.previewAivmxVoice(voice.voice_key, '准备完成，可以开始播报。');
    if (response?.code !== 0) {
      error.value = response?.msg || 'CPU 音色试听失败';
      return;
    }
    runtime.metrics = response.data?.runtime?.metrics || null;
    installedVoices.value[index] = {
      ...voice,
      health: 'ready',
      selectable: true,
      message: '中文试听验证通过',
      metrics: runtime.metrics,
    };
    emit('installed', installedVoices.value[index]);
  } finally {
    activePreview.value = '';
  }
};

onUnmounted(() => {
  if (voicePollTimer) window.clearTimeout(voicePollTimer);
  if (runtimePollTimer) window.clearTimeout(runtimePollTimer);
});
</script>

<template>
  <section class="aivmx-panel">
    <div class="mode-note">
      <strong>CPU 推理 · 显存 0 MB</strong>
      <span>导入单个 .aivmx；中文弹幕保持原文并按中文发音朗读。合成时短时使用 CPU，停用后释放运行时内存。</span>
    </div>

    <section class="panel-card">
      <div class="card-title"><strong>1. 选择实时音色</strong><span>无需参考 WAV、config 或额外权重</span></div>
      <div class="file-row">
        <input :value="sourcePath" data-test="aivmx-path" type="text" readonly placeholder="选择一个 .aivmx 文件">
        <button data-test="pick-aivmx" type="button" @click="chooseAivmx">浏览</button>
      </div>
      <div v-if="metadata" class="metadata-grid" data-test="aivmx-metadata">
        <span>名称</span><strong>{{ metadata.display_name }}</strong>
        <span>创建者</span><strong>{{ metadata.creators?.join('、') }}</strong>
        <span>架构</span><strong>{{ metadata.architecture }}</strong>
        <span>语言</span><strong>{{ metadata.languages?.join('、') }}</strong>
        <span>风格</span><strong>{{ metadata.styles?.map(style => style.name).join('、') }}</strong>
        <span>大小</span><strong>{{ formatSize(metadata.size_bytes) }}</strong>
        <span>SHA-256</span><code>{{ metadata.sha256 }}</code>
        <span>授权</span><strong>{{ metadata.license }}</strong>
      </div>
      <label v-if="metadata" class="permission-check">
        <input v-model="permissionsConfirmed" data-test="aivmx-permissions" type="checkbox">
        <span>我确认已获得训练、合成语音与公开直播使用权限</span>
      </label>
      <div class="actions">
        <span v-if="job.status !== 'idle'">{{ job.message }}<b v-if="job.progress"> · {{ job.progress }}%</b></span>
        <button data-test="install-aivmx" type="button" :disabled="!metadata || !permissionsConfirmed || ['queued', 'running'].includes(job.status)" @click="installAivmx">导入音色</button>
      </div>
    </section>

    <section class="panel-card runtime-card">
      <div class="card-title">
        <div><strong>2. 独立 CPU 运行时</strong><span>ONNX Runtime CPU · 默认 4 线程 · 单路合成</span></div>
        <b :class="`runtime-${runtime.state}`">{{ runtime.state === 'ready' ? '已安装' : '待安装' }}</b>
      </div>
      <p>目录：{{ runtime.root || '应用数据目录 / runtimes / .cpu' }}</p>
      <div class="file-row runtime-row">
        <input v-model.trim="runtime.sourcePath" data-test="cpu-runtime-path" type="text" placeholder="选择 CPU 运行时 ZIP 或目录">
        <button data-test="pick-cpu-runtime-zip" type="button" @click="chooseRuntime('zip')">ZIP</button>
        <button type="button" @click="chooseRuntime('directory')">目录</button>
      </div>
      <div class="actions">
        <span>{{ runtime.message }}</span>
        <button data-test="install-cpu-runtime" type="button" :disabled="!runtime.sourcePath || ['queued', 'running'].includes(runtime.jobStatus)" @click="installRuntime">安装 CPU 运行时</button>
      </div>
    </section>

    <section v-if="installedVoices.length" class="panel-card">
      <div class="card-title"><strong>3. 已导入实时音色</strong><span>首次使用需要完成一次中文试听</span></div>
      <article v-for="(voice, index) in installedVoices" :key="voice.voice_key" class="voice-row">
        <div><strong>{{ voice.display_name }}</strong><span>{{ voice.message || voice.health }}</span></div>
        <div class="voice-actions">
          <button :data-test="`preview-aivmx-${index}`" type="button" :disabled="activePreview === voice.voice_key" @click="preview(voice, index)">
            {{ voice.health === 'ready' ? '重新试听' : '中文试听验证' }}
          </button>
          <button
            v-if="voice.health === 'ready' && voice.selectable"
            :data-test="`refresh-aivmx-${index}`"
            class="refresh-voices"
            type="button"
            @click="emit('refresh-voices', voice)"
          >
            刷新音色下拉框
          </button>
        </div>
      </article>
    </section>

    <div v-if="runtime.metrics" class="metrics">
      <span>内存 {{ runtime.metrics.rss_mb || 0 }} MB</span>
      <span>首包 {{ runtime.metrics.first_pcm_ms || 0 }} ms</span>
      <span>显存 {{ runtime.metrics.vram_mb || 0 }} MB</span>
    </div>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.aivmx-panel { display: flex; flex-direction: column; gap: 12px; }
.mode-note, .panel-card, .metrics { padding: 13px; border: 1px solid #d9e8f1; border-radius: 12px; background: #fff; }
.mode-note { display: flex; flex-direction: column; gap: 4px; border-color: #bce4f3; background: #eefaff; }
.mode-note strong { color: #007da9; font-size: 13px; }.mode-note span, .card-title span, .panel-card p, .voice-row span { color: #64748b; font-size: 11px; line-height: 1.5; }
.panel-card { display: flex; flex-direction: column; gap: 9px; }.card-title, .card-title > div, .voice-row > div { display: flex; flex-direction: column; gap: 2px; }.card-title { flex-direction: row; align-items: center; justify-content: space-between; }.card-title strong, .voice-row strong { color: #1e293b; font-size: 12px; }
.file-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }.runtime-row { grid-template-columns: minmax(0, 1fr) auto auto; }
input[type="text"] { box-sizing: border-box; width: 100%; height: 37px; padding: 0 11px; border: 1px solid #d9e4ed; border-radius: 9px; background: #fff; color: #1e293b; font: inherit; font-size: 12px; }
button { min-height: 34px; padding: 0 14px; border: 0; border-radius: 9px; background: #e8f5fb; color: #008fc4; cursor: pointer; font: inherit; font-size: 12px; font-weight: 650; }button:disabled { cursor: not-allowed; opacity: .5; }
.metadata-grid { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 7px 10px; padding: 11px; border-radius: 9px; background: #f8fafc; font-size: 11px; }.metadata-grid span { color: #64748b; }.metadata-grid strong { overflow-wrap: anywhere; color: #334155; }.metadata-grid code { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #475569; }
.permission-check { display: flex; align-items: flex-start; gap: 8px; padding: 10px; border-radius: 9px; background: #eef8fc; color: #334155; font-size: 12px; line-height: 1.45; }.permission-check input { margin-top: 2px; accent-color: #00aeec; }
.actions { display: flex; align-items: center; justify-content: flex-end; gap: 10px; min-height: 34px; }.actions span { flex: 1; color: #64748b; font-size: 11px; }.actions button { background: #00aeec; color: #fff; }
.runtime-card p { margin: 0; }.runtime-ready { color: #047857; }.runtime-missing, .runtime-checking { color: #c2410c; }
.voice-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding-top: 9px; border-top: 1px solid #edf2f6; }
.voice-actions { display: flex; align-items: center; justify-content: flex-end; gap: 7px; }.voice-actions .refresh-voices { background: #00aeec; color: #fff; }
.metrics { display: flex; gap: 18px; background: #f0f9ff; color: #075985; font-size: 11px; }.form-error { margin: 0; padding: 9px 11px; border-radius: 8px; background: #fff1f2; color: #be123c; font-size: 12px; }
</style>
