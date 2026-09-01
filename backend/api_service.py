import logging
import asyncio
import threading
import sys
import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from backend.bilibili_api import BilibiliApi
from backend.config import Config
from backend.state import SessionState
from backend.services.window_service import WindowService
from backend.services.user_service import UserService
from backend.services.live_service import LiveService
from backend.services.auth_service import AuthService
from backend.services.danmu_service import DanmuService
from backend.services.system_speech_service import SystemSpeechService
from backend.services.personalized_speech_service import PersonalizedSpeechService
from backend.services.aivmx_speech_service import AivmxSpeechService
from backend.services.streaming_audio_player import AudioPlaybackError, StreamingAudioPlayer
from backend.aivmx import (
    AivmxContractError,
    AivmxHealthStore,
    AivmxInstallJobManager,
    AivmxMetadataReader,
    AivmxVoiceRegistry,
    sha256_file as sha256_aivmx,
)
from backend.cpu_runtime import (
    CpuRuntimeContractError,
    CpuRuntimeInstaller,
    CpuRuntimeInstallJobManager,
    CpuRuntimeManager,
    CpuRuntimeRegistry,
    CpuRuntimeVerifier,
)
from backend.runtime import (
    GpuRuntimeManager,
    RuntimeContractError,
    RuntimeInstaller,
    RuntimeInstallJobManager,
    RuntimeRegistry,
    RuntimeVerifier,
)
from backend.runtime.client import SidecarError
from backend.voice import (
    VoiceContractError,
    VoiceJobManager,
    VoicePackBuilder,
    VoicePackRegistry,
    VoicePackValidator,
    VoiceStoragePaths,
)
from backend.voice.health import VoiceHealthStore
from backend.voice.validator import is_link_or_reparse

logger = logging.getLogger("ApiService")

class FrontendLogHandler(logging.Handler):
    """自定义日志处理器，将日志发送到前端"""
    def __init__(self, window_service):
        super().__init__()
        self.window_service = window_service

    def emit(self, record):
        try:
            msg = self.format(record)
            # 避免在主线程阻塞或死循环，这里简单直接调用
            # 注意：如果日志量巨大，可能需要缓冲或限流
            self.window_service.send_to_frontend("onBackendLog", msg)
        except Exception:
            self.handleError(record)

class ApiService:
    def __init__(self):
        self.api_client = BilibiliApi()
        self.config_manager = Config()
        self.session_state = SessionState()
        
        # Initialize services
        self.window_service = WindowService()
        self.user_service = UserService(self.api_client, self.config_manager, self.session_state)
        self.live_service = LiveService(self.api_client, self.config_manager, self.session_state)
        self.auth_service = AuthService(self.api_client, self.user_service, self.live_service, self.session_state)
        self.danmu_service = DanmuService(self.api_client, self.session_state)
        self.speech_service = SystemSpeechService()
        storage_env = dict(os.environ)
        configured_runtime_root = str(self.config_manager.data.get("runtime_root", "")).strip()
        if configured_runtime_root:
            storage_env["BILILIVE_RUNTIME_HOME"] = configured_runtime_root
        self.voice_paths = VoiceStoragePaths.resolve(env=storage_env).ensure()
        self.voice_validator = VoicePackValidator()
        self.voice_builder = VoicePackBuilder(self.voice_paths, self.voice_validator)
        self.voice_registry = VoicePackRegistry(self.voice_paths, self.voice_validator)
        self.voice_jobs = VoiceJobManager(self.voice_builder, self.voice_registry)
        self._initialize_gpu_services()
        
        # 设置弹幕回调
        self.danmu_service.set_callback(self._on_danmu_message)
        # self.danmu_service.set_log_callback(self._on_backend_log) # 不再需要单独的回调，统一走 logging
        
        # 配置日志转发到前端
        self._setup_logging()

        # Initial setup
        self.user_service.init_current_user()
        
        # Asyncio loop for danmu
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self._start_loop, args=(self.loop,), daemon=True)
        self.loop_thread.start()

    def _setup_logging(self):
        """配置日志处理器，将 INFO 及以上级别的日志转发到前端"""
        root_logger = logging.getLogger()
        frontend_handler = FrontendLogHandler(self.window_service)
        frontend_handler.setLevel(logging.INFO) # 只转发 INFO 及以上
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        frontend_handler.setFormatter(formatter)
        root_logger.addHandler(frontend_handler)

    def _initialize_gpu_services(self):
        allow_unsigned = os.environ.get("BILILIVE_ALLOW_UNSIGNED_RUNTIME", "").strip() == "1"
        self.runtime_verifier = RuntimeVerifier(allow_unsigned=allow_unsigned)
        self.runtime_registry = RuntimeRegistry(self.voice_paths, self.runtime_verifier)
        self.voice_registry.set_runtime_registry(self.runtime_registry)
        self.runtime_installer = RuntimeInstaller(self.voice_paths, self.runtime_verifier)
        self.runtime_jobs = RuntimeInstallJobManager(self.runtime_installer, self.runtime_registry)
        self.gpu_runtime_manager = GpuRuntimeManager(
            self.voice_paths.logs,
            self.voice_paths.voices,
            runtime_verifier=self.runtime_verifier,
        )
        self.voice_health = VoiceHealthStore(self.voice_paths)
        self.personalized_speech = PersonalizedSpeechService(
            self.voice_paths,
            self.voice_registry,
            self.runtime_registry,
            self.gpu_runtime_manager,
            StreamingAudioPlayer(),
            self.voice_health,
        )
        self.aivmx_reader = AivmxMetadataReader()
        self.aivmx_registry = AivmxVoiceRegistry(self.voice_paths, self.aivmx_reader)
        self.aivmx_jobs = AivmxInstallJobManager(self.aivmx_registry)
        self.cpu_runtime_verifier = CpuRuntimeVerifier(allow_unsigned=allow_unsigned)
        self.cpu_runtime_registry = CpuRuntimeRegistry(self.voice_paths, self.cpu_runtime_verifier)
        self.cpu_runtime_installer = CpuRuntimeInstaller(self.voice_paths, self.cpu_runtime_verifier)
        self.cpu_runtime_jobs = CpuRuntimeInstallJobManager(self.cpu_runtime_installer, self.cpu_runtime_registry)
        self.cpu_runtime_manager = CpuRuntimeManager(
            self.voice_paths.logs,
            self.voice_paths.aivmx_voices,
            runtime_verifier=self.cpu_runtime_verifier,
        )
        self.aivmx_health = AivmxHealthStore(self.voice_paths)
        self.aivmx_speech = AivmxSpeechService(
            self.voice_paths,
            self.aivmx_registry,
            self.cpu_runtime_registry,
            self.cpu_runtime_manager,
            StreamingAudioPlayer(),
            self.aivmx_health,
        )

    def _shutdown_voice_services(self, include_voice_jobs=True):
        for name in ("personalized_speech", "aivmx_speech"):
            service = getattr(self, name, None)
            if service:
                try:
                    service.shutdown()
                except Exception:
                    logger.exception("Failed to shut down %s", name)
        job_names = ["runtime_jobs", "aivmx_jobs", "cpu_runtime_jobs"]
        if include_voice_jobs:
            job_names.insert(0, "voice_jobs")
        for name in job_names:
            jobs = getattr(self, name, None)
            if jobs:
                try:
                    jobs.shutdown()
                except Exception:
                    logger.exception("Failed to shut down %s", name)

    def _start_loop(self, loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def _on_danmu_message(self, data):
        """处理弹幕消息回调，推送到前端"""
        # 注意：这里可能在子线程中被调用，webview 的 evaluate_js 应该是线程安全的
        # 前端挂载的函数名为 onDanmuMessage
        self.window_service.send_to_frontend("onDanmuMessage", data)

    # def _on_backend_log(self, msg):
    #     """处理后端日志回调，推送到前端"""
    #     self.window_service.send_to_frontend("onBackendLog", msg)

    # --- Window Proxy Methods ---
    def window_min(self): return self.window_service.window_min()
    def window_max(self): return self.window_service.window_max()
    def window_close(self):
        if self.config_manager.data.get("min_to_tray", True):
            self.config_manager.save()
            self.window_service.send_to_frontend("onAppHidden", None)
            if sys.platform == 'win32':
                self.window_service.window_hide()
            else:
                self.window_service.window_min()
            return True

        # 只有在直播状态下才尝试停止直播
        if self.session_state.is_live:
            self.live_service.stop_live()

        asyncio.run_coroutine_threadsafe(self.danmu_service.stop(), self.loop)
        self._shutdown_voice_services()
        return self.window_service.window_close(lambda: self.config_manager.save())
    def get_window_position(self): return self.window_service.get_window_position()
    def window_drag(self, target_x, target_y): return self.window_service.window_drag(target_x, target_y)

    # --- User Proxy Methods ---
    def load_saved_config(self): return self.user_service.load_saved_config()
    def refresh_current_user(self): return self.user_service.refresh_current_user()
    def get_account_list(self): return self.user_service.get_account_list()
    def switch_account(self, uid):
        # 切换账户前先停止弹幕，防止新连接使用旧账户
        asyncio.run_coroutine_threadsafe(self.danmu_service.stop(), self.loop)
        return self.user_service.switch_account(uid)
    def logout(self, uid):
        asyncio.run_coroutine_threadsafe(self.danmu_service.stop(), self.loop)
        return self.user_service.logout(uid)

    # --- Auth Proxy Methods ---
    def get_login_qrcode(self): return self.auth_service.get_login_qrcode()
    def poll_login_status(self, key): return self.auth_service.poll_login_status(key)

    # --- Live Proxy Methods ---
    def get_partitions(self): return self.live_service.get_partitions()
    def sync_room_profile(self): return self.live_service.sync_room_profile()
    def update_title(self, title): return self.live_service.update_title(title)
    def update_announcement(self, announcement): return self.live_service.update_announcement(announcement)
    def update_area(self, p_name, s_name): return self.live_service.update_area(p_name, s_name)
    def start_live(self, p_name=None, s_name=None): 
        res = self.live_service.start_live(p_name, s_name)
        # if res['code'] == 0:
        #      # 开启直播成功后，连接弹幕
        #      room_id = self.session_state.room_id
        #      if room_id:
        #          asyncio.run_coroutine_threadsafe(self.danmu_service.connect(room_id), self.loop)
        return res
        
    def stop_live(self): 
        res = self.live_service.stop_live()
        return res

    # --- Danmu Methods ---
    def start_danmu_monitor(self):
        """开启弹幕监听，如果已在运行则跳过"""
        if self.danmu_service.running:
            return {"code": 0, "msg": "弹幕已在运行"}
        room_id = self.session_state.room_id
        if not room_id:
             return {"code": -1, "msg": "未获取到房间ID"}
        asyncio.run_coroutine_threadsafe(self.danmu_service.connect(room_id), self.loop)
        return {"code": 0}

    def stop_danmu_monitor(self):
        asyncio.run_coroutine_threadsafe(self.danmu_service.stop(), self.loop)
        return {"code": 0}

    def send_danmu(self, msg):
        """发送弹幕"""
        return self.danmu_service.send_danmu(msg)

    # --- System Speech Methods ---
    def get_speech_capabilities(self):
        return self.speech_service.get_capabilities()

    def speak_text(self, text, voice_uri="", rate=1.0, volume=1.0, voice_key=""):
        try:
            if isinstance(voice_key, str) and voice_key.startswith("pack:"):
                return self.personalized_speech.speak(text, voice_key, volume=volume, rate=rate)
            if isinstance(voice_key, str) and voice_key.startswith("aivmx:"):
                return self.aivmx_speech.speak(text, voice_key, volume=volume, rate=rate)
            if voice_key and not str(voice_key).startswith("system:"):
                raise SidecarError("invalid_voice_key", "语音音色标识无效")
            if isinstance(voice_key, str) and voice_key.startswith("system:") and not voice_uri:
                voice_uri = voice_key[7:]
            return self.speech_service.speak(text, voice_uri, rate, volume)
        except Exception as exc:
            return self._voice_error(exc)

    def stop_speech(self):
        system_result = self.speech_service.stop()
        self.personalized_speech.stop()
        self.aivmx_speech.stop()
        return system_result

    # --- Personalized Voice Pack Methods ---
    @staticmethod
    def _voice_error(exc):
        if isinstance(exc, (
            VoiceContractError,
            RuntimeContractError,
            AivmxContractError,
            CpuRuntimeContractError,
            SidecarError,
            AudioPlaybackError,
        )):
            return {
                "code": -1,
                "msg": exc.message,
                "error": {"code": exc.code, "message": exc.message, "field": getattr(exc, "field", "")},
            }
        logger.exception("Voice pack operation failed")
        return {
            "code": -1,
            "msg": "音色操作失败，请查看应用日志",
            "error": {"code": "internal_error", "message": "音色操作失败，请查看应用日志", "field": ""},
        }

    def choose_voice_source(self, kind):
        filters = {
            "gpt": ("GPT 权重 (*.ckpt)",),
            "sovits": ("SoVITS 权重 (*.pth)",),
            "reference": ("PCM WAV 音频 (*.wav)",),
            "license": ("授权说明 (*.txt;*.md;*.pdf)", "所有文件 (*.*)"),
            "aivmx": ("AIVMX 实时 CPU 音色 (*.aivmx)",),
        }
        if kind not in filters:
            return self._voice_error(VoiceContractError("invalid_source_kind", "不支持的文件选择类型"))
        try:
            import webview

            windows = getattr(webview, "windows", [])
            if not windows:
                raise VoiceContractError("window_unavailable", "桌面窗口尚未就绪")
            dialog_api = getattr(webview, "FileDialog", None)
            dialog_type = dialog_api.OPEN if dialog_api else getattr(webview, "OPEN_DIALOG")
            result = windows[0].create_file_dialog(
                dialog_type,
                allow_multiple=False,
                file_types=filters[kind],
            )
            if isinstance(result, (tuple, list)):
                selected = str(result[0]) if result else ""
            else:
                selected = str(result or "")
            return {"code": 0, "data": {"path": selected}}
        except VoiceContractError as exc:
            return self._voice_error(exc)
        except Exception as exc:
            return self._voice_error(exc)

    def start_voice_pack_build(self, request):
        try:
            return {"code": 0, "data": {"job_id": self.voice_jobs.start_build(request)}}
        except Exception as exc:
            return self._voice_error(exc)

    def get_voice_job(self, job_id):
        try:
            return {"code": 0, "data": self.voice_jobs.get(job_id)}
        except Exception as exc:
            return self._voice_error(exc)

    def cancel_voice_job(self, job_id):
        try:
            return {"code": 0, "data": {"cancelled": self.voice_jobs.cancel(job_id)}}
        except Exception as exc:
            return self._voice_error(exc)

    def list_voice_packs(self):
        try:
            self.voice_registry.refresh()
            return {"code": 0, "data": self.voice_registry.list_packs()}
        except Exception as exc:
            return self._voice_error(exc)

    # --- AIVMX Realtime CPU Voice Methods ---
    def inspect_aivmx(self, path):
        try:
            source = Path(str(path or ""))
            metadata = self.aivmx_reader.read(source)
            data = metadata.to_dict()
            data.update({"sha256": sha256_aivmx(source), "size_bytes": source.stat().st_size})
            return {"code": 0, "data": data}
        except Exception as exc:
            return self._voice_error(exc)

    def start_aivmx_install(self, request):
        try:
            return {"code": 0, "data": {"job_id": self.aivmx_jobs.start(request)}}
        except Exception as exc:
            return self._voice_error(exc)

    def get_aivmx_job(self, job_id):
        try:
            return {"code": 0, "data": self.aivmx_jobs.get(job_id)}
        except Exception as exc:
            return self._voice_error(exc)

    def list_aivmx_voices(self):
        try:
            self.aivmx_registry.refresh()
            voices = self.aivmx_registry.list_voices()
            for voice in voices:
                record = self.aivmx_registry.get(voice["model_uuid"])
                runtime = self.cpu_runtime_registry.find_compatible(record.metadata.architecture, "zh-CN") if record else None
                state = self.aivmx_health.get(record, voice["style_id"], runtime) if record else {
                    "health": "runtime_required",
                    "message": "AIVMX 音色不可用",
                }
                voice.update({
                    "health": state["health"],
                    "selectable": state["health"] == "ready",
                    "message": state["message"],
                    "runtime_id": getattr(runtime, "runtime_id", ""),
                    "metrics": dict(state.get("metrics", {})),
                })
            return {"code": 0, "data": voices}
        except Exception as exc:
            return self._voice_error(exc)

    # --- GPU Runtime and Personalized Speech Methods ---
    def choose_runtime_source(self, kind):
        if kind not in ("zip", "directory", "data_root"):
            return self._voice_error(RuntimeContractError("invalid_source_kind", "不支持的 GPU 运行时选择类型"))
        try:
            import webview

            windows = getattr(webview, "windows", [])
            if not windows:
                raise RuntimeContractError("window_unavailable", "桌面窗口尚未就绪")
            dialog_api = getattr(webview, "FileDialog", None)
            if kind == "zip":
                dialog_type = dialog_api.OPEN if dialog_api else getattr(webview, "OPEN_DIALOG")
                options = {"allow_multiple": False, "file_types": ("GPU 运行时 (*.zip)",)}
            else:
                dialog_type = dialog_api.FOLDER if dialog_api else getattr(webview, "FOLDER_DIALOG")
                options = {}
            result = windows[0].create_file_dialog(dialog_type, **options)
            selected = str(result[0]) if isinstance(result, (tuple, list)) and result else str(result or "")
            return {"code": 0, "data": {"path": selected}}
        except Exception as exc:
            return self._voice_error(exc)

    def choose_cpu_runtime_source(self, kind):
        if kind not in ("zip", "directory"):
            return self._voice_error(CpuRuntimeContractError("invalid_source_kind", "不支持的 CPU 运行时选择类型"))
        try:
            import webview

            windows = getattr(webview, "windows", [])
            if not windows:
                raise CpuRuntimeContractError("window_unavailable", "桌面窗口尚未就绪")
            dialog_api = getattr(webview, "FileDialog", None)
            if kind == "zip":
                dialog_type = dialog_api.OPEN if dialog_api else getattr(webview, "OPEN_DIALOG")
                options = {"allow_multiple": False, "file_types": ("CPU 运行时 (*.zip)",)}
            else:
                dialog_type = dialog_api.FOLDER if dialog_api else getattr(webview, "FOLDER_DIALOG")
                options = {}
            result = windows[0].create_file_dialog(dialog_type, **options)
            selected = str(result[0]) if isinstance(result, (tuple, list)) and result else str(result or "")
            return {"code": 0, "data": {"path": selected}}
        except Exception as exc:
            return self._voice_error(exc)

    def configure_runtime_root(self, path):
        try:
            target = self._preflight_runtime_root(path)
            self._shutdown_voice_services(include_voice_jobs=False)
            previous_root = self.config_manager.data.get("runtime_root", "")
            previous_paths = self.voice_paths
            self.config_manager.data["runtime_root"] = str(target)
            self.config_manager.save()
            try:
                self.voice_paths = replace(self.voice_paths, runtimes=target, cpu_runtimes=target / ".cpu").ensure()
                self._initialize_gpu_services()
            except Exception:
                self.config_manager.data["runtime_root"] = previous_root
                self.config_manager.save()
                self.voice_paths = previous_paths
                self._initialize_gpu_services()
                raise
            return {"code": 0, "data": self.runtime_registry.status()}
        except Exception as exc:
            return self._voice_error(exc)

    @staticmethod
    def _preflight_runtime_root(path, minimum_free_bytes=1024**3):
        raw = str(path or "").strip()
        if not raw:
            raise RuntimeContractError("invalid_runtime_root", "GPU 运行时数据目录无效")
        candidate = Path(raw).expanduser()
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            if not candidate.is_dir() or is_link_or_reparse(candidate):
                raise RuntimeContractError("invalid_runtime_root", "GPU 运行时数据目录无效或不安全")
            target = candidate.resolve(strict=True)
            probe = target / f".bililive-write-probe-{uuid.uuid4().hex}"
            try:
                with probe.open("xb") as stream:
                    stream.write(b"ok")
            finally:
                probe.unlink(missing_ok=True)
            if shutil.disk_usage(target).free < minimum_free_bytes:
                raise RuntimeContractError("insufficient_disk_space", "GPU 运行时数据目录至少需要 1 GiB 可用空间")
            return target
        except RuntimeContractError:
            raise
        except OSError as exc:
            raise RuntimeContractError("runtime_root_unwritable", "GPU 运行时数据目录不可写") from exc

    def start_runtime_install(self, request):
        try:
            return {"code": 0, "data": {"job_id": self.runtime_jobs.start(request)}}
        except Exception as exc:
            return self._voice_error(exc)

    def get_runtime_job(self, job_id):
        try:
            return {"code": 0, "data": self.runtime_jobs.get(job_id)}
        except Exception as exc:
            return self._voice_error(exc)

    def get_gpu_runtime_status(self):
        try:
            data = self.runtime_registry.status()
            data["process"] = self.gpu_runtime_manager.status() if hasattr(self, "gpu_runtime_manager") else {"state": "stopped"}
            return {"code": 0, "data": data}
        except Exception as exc:
            return self._voice_error(exc)

    def start_cpu_runtime_install(self, request):
        try:
            return {"code": 0, "data": {"job_id": self.cpu_runtime_jobs.start(request)}}
        except Exception as exc:
            return self._voice_error(exc)

    def get_cpu_runtime_job(self, job_id):
        try:
            return {"code": 0, "data": self.cpu_runtime_jobs.get(job_id)}
        except Exception as exc:
            return self._voice_error(exc)

    def get_cpu_runtime_status(self):
        try:
            data = self.cpu_runtime_registry.status()
            data["process"] = self.cpu_runtime_manager.status() if hasattr(self, "cpu_runtime_manager") else {"state": "stopped"}
            return {"code": 0, "data": data}
        except Exception as exc:
            return self._voice_error(exc)

    def prepare_aivmx_voice(self, voice_key):
        try:
            return {"code": 0, "data": self.aivmx_speech.prepare(voice_key)}
        except Exception as exc:
            return self._voice_error(exc)

    def preview_aivmx_voice(self, voice_key, text=""):
        try:
            return {"code": 0, "data": self.aivmx_speech.preview(voice_key, text)}
        except Exception as exc:
            return self._voice_error(exc)

    def release_aivmx_voice(self):
        try:
            self.aivmx_speech.shutdown()
            return {"code": 0}
        except Exception as exc:
            return self._voice_error(exc)

    def prepare_voice(self, voice_key):
        try:
            return {"code": 0, "data": self.personalized_speech.prepare(voice_key)}
        except Exception as exc:
            return self._voice_error(exc)

    def preview_voice(self, voice_key, text=""):
        try:
            return {"code": 0, "data": self.personalized_speech.preview(voice_key, text)}
        except Exception as exc:
            return self._voice_error(exc)

    def release_personalized_voice(self):
        try:
            self.personalized_speech.shutdown()
            return {"code": 0}
        except Exception as exc:
            return self._voice_error(exc)

    # --- App Config Methods ---
    def get_app_config(self):
        import sys
        # 使用实际托盘运行状态（由 main.py 设置）
        has_tray = getattr(self, 'tray_active', False)
        config = {
            "min_to_tray": self.config_manager.data.get("min_to_tray", True),
            "is_win32": sys.platform == 'win32',
            "has_tray": has_tray
        }
        return {"code": 0, "data": config}

    def set_app_config(self, key, value):
        if key == "min_to_tray":
            self.config_manager.data["min_to_tray"] = bool(value)
            self.config_manager.save()
            return {"code": 0}
        return {"code": -1, "msg": "Unknown config key"}

    def get_version(self):
        """获取应用版本号"""
        import os, sys
        try:
            if getattr(sys, 'frozen', False):
                base = sys._MEIPASS
            else:
                base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            version_file = os.path.join(base, 'VERSION')
            if os.path.exists(version_file):
                with open(version_file, 'r', encoding='utf-8') as f:
                    return {"code": 0, "version": f.read().strip()}
        except Exception:
            pass
        return {"code": 0, "version": "dev"}
