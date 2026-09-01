# BiliLiveTool v2.4.0

## 主要更新

- 新增直播弹幕语音播报、队列顺序播放、语速和音量控制。
- 新增 Windows 系统音色，以及可导入的 GPT-SoVITS GPU 音色接口。
- 新增 Style-Bert-VITS2 `.aivmx` 实时 CPU 音色：中文发音、零推理显存、独立运行时和音色导入。
- AIVMX 试听通过后，`刷新音色下拉框` 只刷新列表；不会自动选择音色或启动播报。播报始终由顶部开关统一启停。
- 修复音色操作按钮可能在顶部开关之外单独启动播报的问题，避免两套播报生命周期并存。
- CPU/GPU sidecar 使用本机随机回环端口和认证令牌，关闭语音或退出应用后释放进程资源。
- 修复 Windows 上启动个性化语音时额外显示 Python 终端窗口的问题；语音启用期间 sidecar 会在后台静默运行。
- 桌面程序改为单实例运行：重复启动时唤醒并显示已经运行的窗口，不再创建第二套直播和语音进程。
- 统一应用退出清理流程，正常退出时一并停止系统语音、GPU/CPU sidecar 和后台任务，避免遗留进程。

## 下载说明

- `BiliLiveTool-v2.4.0-windows-amd64.zip`：Windows 桌面主程序，约 125.6 MiB；使用系统音色的用户只需下载它。
- `BiliLiveTool-Style-Bert-VITS2-CPU-2026.09.01-66de777e.zip`：可选的 Style-Bert-VITS2 CPU 运行时，约 725 MiB；仅导入 AIVMX 个性化音色时需要。
- `SHA256SUMS.txt`：两个 ZIP 的 SHA-256 校验值。

如果已经安装过同一版 CPU 运行时，本次修复只需替换 Windows 桌面主程序；运行时和已导入的 `.aivmx` 音色会继续复用，无需重新安装。

CPU 运行时与主程序分开安装，不包含任何用户音色、训练素材、账号 Cookie 或本地配置。用户自己的 `.aivmx` 通过应用内导入界面安装。

Windows 包内已经带有顶层文件夹和 `README-Windows.txt`。请把整个文件夹解压到一个独立、可写的位置后再运行；程序会在 EXE 同目录生成 `logs/`、`config.json` 等文件。`config.json` 可能包含账号登录信息，请勿分享整个运行目录。

## 已验证

- Python：133 个测试通过。
- 前端：51 个测试通过，Vite 正式构建通过。
- Windows 单文件 EXE：隔离数据目录启动冒烟测试通过。
- Windows 单实例：重复启动退出并唤醒现有窗口的实机测试通过。
- Windows sidecar：无控制台窗口启动及应用退出清理测试通过。
- 已签名 CPU 运行时：22,929 个文件逐项校验通过，仅含 `CPUExecutionProvider`，不含 Torch/CUDA/DirectML。
- 本机真实 AIVMX：冷态首包 1.991 秒、热态首包 1.175 秒、推理显存 0 MiB，关闭后 sidecar 进程退出。

CPU 模式保持音色热加载时约占用 1.1 GiB 系统内存，加载和合成阶段实测峰值约 2.0 GiB；关闭语音播报后释放。完整数据见 `docs/cpu-runtime-benchmark.md`。
