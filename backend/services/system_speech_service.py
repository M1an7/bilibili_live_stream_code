import base64
import json
import logging
import re
import shutil
import subprocess
import sys
import threading


logger = logging.getLogger("SystemSpeech")


class SystemSpeechService:
    """Low-resource system TTS backed by platform-provided commands."""

    def __init__(
        self,
        platform_name=None,
        which=None,
        runner=None,
        popen_factory=None,
    ):
        self.platform_name = platform_name or sys.platform
        self._which = which or shutil.which
        self._runner = runner or subprocess.run
        self._popen = popen_factory or subprocess.Popen
        self._process_lock = threading.Lock()
        self._current_process = None
        self._engine, self._command = self._detect_engine()

    def _detect_engine(self):
        if self.platform_name.startswith("win"):
            command = self._which("powershell.exe") or self._which("powershell")
            return ("sapi", command) if command else (None, None)
        if self.platform_name == "darwin":
            command = self._which("say")
            return ("say", command) if command else (None, None)

        command = self._which("espeak-ng")
        if command:
            return "espeak-ng", command
        command = self._which("espeak")
        if command:
            return "espeak", command
        # WSL can use the host Windows SAPI directly without a Linux TTS package.
        command = self._which("powershell.exe")
        if command:
            return "sapi", command
        return None, None

    def _unsupported_error(self):
        if self.platform_name.startswith("win"):
            return "Windows 系统语音不可用（未找到 PowerShell/SAPI）"
        if self.platform_name == "darwin":
            return "macOS 系统语音不可用（未找到 say）"
        return "Linux 桌面语音需要安装 espeak-ng 或 espeak"

    def get_capabilities(self):
        if not self._command:
            return {
                "code": 0,
                "data": {
                    "supported": False,
                    "engine": "",
                    "voices": [],
                    "error": self._unsupported_error(),
                },
            }

        try:
            voices = self._list_voices()
            return {
                "code": 0,
                "data": {
                    "supported": True,
                    "engine": self._engine,
                    "voices": voices,
                    "error": "",
                },
            }
        except Exception as exc:
            logger.warning("Unable to list system voices: %s", exc)
            return {
                "code": 0,
                "data": {
                    "supported": True,
                    "engine": self._engine,
                    "voices": [],
                    "error": "可使用系统默认音色，但音色列表读取失败",
                },
            }

    def _run_for_output(self, args):
        completed = self._runner(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "系统语音命令执行失败")
        return completed.stdout

    def _list_voices(self):
        if self._engine in {"espeak", "espeak-ng"}:
            output = self._run_for_output([self._command, "--voices"])
            voices = []
            for line in output.splitlines()[1:]:
                parts = line.split()
                if len(parts) < 5:
                    continue
                voices.append({
                    "name": parts[3],
                    "voiceURI": parts[4],
                    "lang": parts[1],
                    "default": len(voices) == 0,
                })
            return voices

        if self._engine == "say":
            output = self._run_for_output([self._command, "-v", "?"])
            voices = []
            for line in output.splitlines():
                match = re.match(r"^(.+?)\s+([a-z]{2}_[A-Z]{2})\s+#", line)
                if not match:
                    continue
                name, language = match.groups()
                voices.append({
                    "name": name.strip(),
                    "voiceURI": name.strip(),
                    "lang": language.replace("_", "-"),
                    "default": len(voices) == 0,
                })
            return voices

        script = """
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
Add-Type -AssemblyName System.Speech
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$defaultVoice = $synth.Voice.Name
$items = @($synth.GetInstalledVoices() | ForEach-Object {
  [PSCustomObject]@{
    name = $_.VoiceInfo.Name
    voiceURI = $_.VoiceInfo.Name
    lang = $_.VoiceInfo.Culture.Name
    default = ($_.VoiceInfo.Name -eq $defaultVoice)
  }
})
ConvertTo-Json -InputObject $items -Compress
""".strip()
        output = self._run_for_output([
            self._command,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ])
        parsed = json.loads(output or "[]")
        return parsed if isinstance(parsed, list) else [parsed]

    @staticmethod
    def _clamp(value, minimum, maximum, fallback):
        try:
            return min(maximum, max(minimum, float(value)))
        except (TypeError, ValueError):
            return fallback

    def _speech_command(self, text, voice_uri, rate, volume):
        if self._engine in {"espeak", "espeak-ng"}:
            args = [
                self._command,
                "-s",
                str(round(175 * rate)),
                "-a",
                str(round(200 * volume)),
            ]
            if voice_uri:
                args.extend(["-v", voice_uri])
            args.extend(["--", text])
            return args, None

        if self._engine == "say":
            args = [self._command, "-r", str(round(175 * rate))]
            if voice_uri:
                args.extend(["-v", voice_uri])
            args.append(text)
            return args, None

        encoded_text = base64.b64encode(text.encode("utf-8")).decode("ascii")
        encoded_voice = base64.b64encode(voice_uri.encode("utf-8")).decode("ascii")
        sapi_rate = round((rate - 1.0) * 5.0)
        sapi_volume = round(volume * 100.0)
        script = f"""
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
Add-Type -AssemblyName System.Speech
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$text = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{encoded_text}'))
$voice = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{encoded_voice}'))
if ($voice) {{ $synth.SelectVoice($voice) }}
$synth.Rate = {sapi_rate}
$synth.Volume = {sapi_volume}
$synth.Speak($text)
""".strip()
        encoded_command = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return [
            self._command,
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded_command,
        ], None

    def speak(self, text, voice_uri="", rate=1.0, volume=1.0):
        normalized = str(text or "").strip()
        if not normalized:
            return {"code": -1, "msg": "播报文本为空"}
        if not self._command:
            return {"code": -1, "msg": self._unsupported_error()}

        rate = self._clamp(rate, 0.5, 2.0, 1.0)
        volume = self._clamp(volume, 0.0, 1.0, 1.0)
        voice_uri = str(voice_uri or "")
        args, environment = self._speech_command(normalized, voice_uri, rate, volume)

        try:
            process = self._popen(
                args,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            with self._process_lock:
                self._current_process = process
            _, stderr = process.communicate()
            if process.returncode != 0:
                return {"code": -1, "msg": stderr.strip() or "系统语音播放失败"}
            return {"code": 0}
        except Exception as exc:
            logger.exception("System speech failed")
            return {"code": -1, "msg": str(exc)}
        finally:
            with self._process_lock:
                if self._current_process is locals().get("process"):
                    self._current_process = None

    def stop(self):
        with self._process_lock:
            process = self._current_process
        if process and process.poll() is None:
            try:
                process.terminate()
                if hasattr(process, "wait"):
                    process.wait(timeout=2)
            except Exception as exc:
                logger.debug("Unable to stop system speech process: %s", exc)
        return {"code": 0}
