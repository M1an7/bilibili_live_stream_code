<script setup>
import { computed, onMounted, onUnmounted, reactive } from 'vue';

const props = defineProps({
  service: {
    type: Object,
    required: true,
  },
  devMode: {
    type: Boolean,
    default: false,
  },
});

const emit = defineEmits(['simulate', 'import-voice']);
const state = reactive(props.service.getState());
let unsubscribe = null;

const statusText = computed(() => ({
  unsupported: '不可用',
  idle: '未开启',
  ready: '等待弹幕',
  speaking: '正在播报',
  loading_gpu: '启动 GPU',
  warming: '音色预热',
  gpu_error: 'GPU 异常',
}[state.status] || '未知状态'));
const systemVoices = computed(() => state.voices.filter(voice => voice.kind !== 'pack'));
const personalizedVoices = computed(() => state.voices.filter(voice => voice.kind === 'pack'));

const applyState = (next) => {
  Object.assign(state, next);
};

const handleEnabled = event => props.service.setEnabled(event.target.checked);
const handleVoice = event => props.service.setVoice(event.target.value);
const handleRate = event => props.service.setRate(Number(event.target.value));
const handleVolume = event => props.service.setVolume(Number(event.target.value));

onMounted(() => {
  unsubscribe = props.service.subscribe(applyState);
  props.service.initialize?.();
  props.service.refreshVoices();
});

onUnmounted(() => {
  unsubscribe?.();
});
</script>

<template>
  <section class="speech-toolbar" :class="{ unsupported: !state.supported }">
    <div class="speech-main-row">
      <div class="speech-heading">
        <span class="speech-icon" aria-hidden="true">◖</span>
        <span>语音播报</span>
      </div>

      <label class="speech-switch">
        <input
          data-test="speech-enabled"
          type="checkbox"
          :checked="state.enabled"
          :disabled="!state.supported || state.status === 'loading_gpu' || state.status === 'warming'"
          @change="handleEnabled"
        >
        <span class="switch-track"><span class="switch-thumb"></span></span>
      </label>

      <span
        data-test="speech-status"
        class="status-pill"
        :class="`status-${state.status}`"
      >
        <span class="status-dot"></span>{{ statusText }}
      </span>

      <select
        data-test="speech-voice"
        class="voice-select"
        :value="state.selectedVoiceKey || (state.selectedVoiceURI ? `system:${state.selectedVoiceURI}` : '')"
        :disabled="!state.supported || state.voices.length === 0"
        title="语音音色"
        @change="handleVoice"
      >
        <option v-if="state.voices.length === 0" value="">暂无系统音色</option>
        <optgroup v-if="systemVoices.length" label="系统音色">
          <option v-for="voice in systemVoices" :key="voice.voiceKey" :value="voice.voiceKey">
            {{ voice.name }}{{ voice.lang ? ` · ${voice.lang}` : '' }}
          </option>
        </optgroup>
        <optgroup v-if="personalizedVoices.length" label="个性化音色">
          <option v-for="voice in personalizedVoices" :key="voice.voiceKey" :value="voice.voiceKey">
            {{ voice.name }}{{ voice.lang ? ` · ${voice.lang}` : '' }}
          </option>
        </optgroup>
      </select>

      <button
        data-test="import-voice"
        class="toolbar-button import-button"
        type="button"
        title="导入 GPT-SoVITS 个性化音色"
        @click="emit('import-voice')"
      >
        导入
      </button>

      <span data-test="speech-queue" class="queue-count">
        队列 {{ state.queueLength }}
      </span>

      <button
        data-test="speech-skip"
        class="toolbar-button"
        type="button"
        :disabled="!state.supported"
        @click="service.skip()"
      >
        跳过
      </button>
    </div>

    <div class="speech-settings-row">
      <label class="range-control">
        <span>语速</span>
        <input
          data-test="speech-rate"
          type="range"
          min="0.5"
          max="2"
          step="0.1"
          :value="state.rate"
          :disabled="!state.supported"
          @input="handleRate"
        >
        <output>{{ Number(state.rate).toFixed(1) }}×</output>
      </label>

      <label class="range-control">
        <span>音量</span>
        <input
          data-test="speech-volume"
          type="range"
          min="0"
          max="1"
          step="0.05"
          :value="state.volume"
          :disabled="!state.supported"
          @input="handleVolume"
        >
        <output>{{ Math.round(state.volume * 100) }}%</output>
      </label>

      <button
        v-if="devMode"
        data-test="simulate-danmu"
        class="toolbar-button preview-button"
        type="button"
        @click="emit('simulate')"
      >
        模拟弹幕
      </button>

      <p v-if="!state.supported || state.status === 'gpu_error'" class="speech-error">
        {{ state.error || '当前系统不支持语音播报' }}
      </p>
    </div>
  </section>
</template>

<style scoped>
.speech-toolbar {
  padding: 11px 14px;
  border-bottom: 1px solid rgba(0, 174, 236, 0.16);
  background: linear-gradient(135deg, #f4fbff 0%, #f8faff 100%);
  color: #334155;
}

.speech-toolbar.unsupported {
  background: #f8fafc;
}

.speech-main-row,
.speech-settings-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.speech-settings-row {
  margin-top: 9px;
  padding-left: 30px;
  gap: 18px;
}

.speech-heading {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 650;
  color: #0f172a;
}

.speech-icon {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 7px;
  background: #00aeec;
  color: white;
  font-size: 12px;
  transform: rotate(180deg);
}

.speech-switch {
  display: inline-flex;
  cursor: pointer;
}

.speech-switch input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.switch-track {
  position: relative;
  width: 34px;
  height: 19px;
  border-radius: 999px;
  background: #cbd5e1;
  transition: background 0.18s ease;
}

.switch-thumb {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 13px;
  height: 13px;
  border-radius: 50%;
  background: white;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.25);
  transition: transform 0.18s ease;
}

.speech-switch input:checked + .switch-track {
  background: #00aeec;
}

.speech-switch input:checked + .switch-track .switch-thumb {
  transform: translateX(15px);
}

.speech-switch input:focus-visible + .switch-track {
  outline: 2px solid rgba(0, 174, 236, 0.35);
  outline-offset: 2px;
}

.speech-switch input:disabled + .switch-track {
  opacity: 0.55;
  cursor: not-allowed;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
  padding: 4px 8px;
  border-radius: 999px;
  background: rgba(100, 116, 139, 0.09);
  color: #64748b;
  font-size: 11px;
  font-weight: 600;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.status-ready {
  color: #0f9f6e;
  background: rgba(15, 159, 110, 0.1);
}

.status-speaking {
  color: #008fc4;
  background: rgba(0, 174, 236, 0.12);
}

.status-unsupported {
  color: #c2410c;
  background: rgba(234, 88, 12, 0.1);
}

.voice-select {
  min-width: 0;
  max-width: 210px;
  height: 30px;
  padding: 0 28px 0 10px;
  border: 1px solid #dbe7f0;
  border-radius: 8px;
  outline: none;
  background: white;
  color: #334155;
  font-size: 12px;
}

.voice-select:focus {
  border-color: #00aeec;
  box-shadow: 0 0 0 2px rgba(0, 174, 236, 0.1);
}

.queue-count {
  margin-left: auto;
  flex-shrink: 0;
  color: #64748b;
  font-size: 11px;
}

.toolbar-button {
  flex-shrink: 0;
  height: 29px;
  padding: 0 11px;
  border: 1px solid #dbe7f0;
  border-radius: 8px;
  background: white;
  color: #475569;
  cursor: pointer;
  font-size: 12px;
  transition: 0.18s ease;
}

.toolbar-button:hover:not(:disabled) {
  border-color: #00aeec;
  color: #008fc4;
}

.import-button {
  border-color: rgba(0, 174, 236, 0.32);
  color: #008fc4;
}

.toolbar-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.preview-button {
  margin-left: auto;
  border-color: rgba(0, 174, 236, 0.3);
  background: rgba(255, 255, 255, 0.8);
  color: #008fc4;
}

.range-control {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #64748b;
  font-size: 11px;
}

.range-control input {
  width: 86px;
  accent-color: #00aeec;
}

.range-control output {
  width: 34px;
  color: #334155;
  font-variant-numeric: tabular-nums;
}

.speech-error {
  margin: 0 0 0 auto;
  color: #c2410c;
  font-size: 11px;
}

@media (max-width: 760px) {
  .speech-main-row,
  .speech-settings-row {
    flex-wrap: wrap;
  }

  .speech-settings-row {
    padding-left: 0;
  }

  .queue-count {
    margin-left: 0;
  }
}
</style>
