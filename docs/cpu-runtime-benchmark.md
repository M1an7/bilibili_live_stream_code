# Style-Bert-VITS2 CPU 运行时真实验收记录

日期：2026-09-01
版本：BiliLiveTool v2.4.0
结果：通过

## 发布包

- 运行时：`BiliLiveTool-Style-Bert-VITS2-CPU-2026.09.01-66de777e.zip`
- ZIP 大小：760,181,781 bytes（约 0.71 GiB）
- ZIP SHA-256：`e51070ccc9fbb64742aad36317430379ea00d9b5f4fb4a39bb36694885025451`
- Style-Bert-VITS2 固定提交：`66de777e...`
- 文件清单：22,929 个文件，逐文件 SHA-256
- 签名：Ed25519 正式签名，使用桌面应用内置公钥验证通过
- Provider：仅 `CPUExecutionProvider`
- 不含：Torch、CUDA、cuDNN、TensorRT、DirectML 和用户音色

发布 ZIP 被重新解压到独立目录后运行完整校验，证明压缩包本身可搬移且没有依赖开发目录。

## 原生 Windows 实测

测试使用 4 个推理线程和用户已授权的私有 Style-Bert-VITS2 AIVMX 音色。音色文件、试听 WAV 和授权素材不进入 Git，也不随公开发布包分发。

| 指标 | 结果 |
| --- | ---: |
| sidecar 冷启动 | 12.517 秒 |
| 音色加载及预热 | 3.692 秒 |
| 冷态首个 PCM | 1.991 秒 |
| 冷态整句 | 1.994 秒 |
| 热态首个 PCM | 1.175 秒 |
| 热态整句 | 1.175 秒 |
| 当前工作集 RSS | 1,133 MiB |
| 峰值工作集 RSS | 2,014 MiB |
| 推理显存 | 0 MiB |
| sidecar 关闭释放 | 通过 |

冷态与热态首包都低于 3 秒验收上限；热态 1.175 秒接近实时目标，但没有隐藏其略高于理想 1 秒目标的事实。模型保持加载时会占用约 1.1 GiB 系统内存，合成与加载阶段峰值约 2.0 GiB；关闭语音播报后 sidecar 退出并释放该内存。

## 自动校验

- Python 全量测试：129 个通过；前端测试：51 个通过，Vite 正式构建通过。
- 两次输出均为有效的 44.1 kHz、16-bit、单声道非静音 PCM。
- 冷态输出 2.519 秒、222,208 bytes、峰值 32,767、RMS 5,727.74。
- 热态输出 3.100 秒、273,408 bytes、峰值 32,767、RMS 6,845.83。
- 模型 SHA-256：`43f3de83e48ee27c967956f7c07ffa90a58ab47e8db502bd9a5a899beeadd965`。
- 运行时报告 `CPUExecutionProvider` 且 `vram_mb` 为 0；系统中没有加载 Torch。
- 测试结束后 sidecar 进程退出。

复现入口为 `scripts/test_real_cpu_voice.py`。音色相似度和主观听感由用户试听判断，不由自动测试替代。
