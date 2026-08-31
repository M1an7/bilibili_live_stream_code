# 中文个性化音色 CPU 实时播报设计

日期：2026-08-31  
状态：已完成对话设计，待用户复核书面规格

## 1. 背景与已确认目标

当前项目已经打通 Windows 系统语音和 GPT-SoVITS GPU 个性化音色。真实 Windows 便携运行时测试显示，GPT-SoVITS 的启动、模型加载、首包延迟和约 1.7 GB 峰值显存不适合作为持续读取弹幕的默认引擎。

本阶段将新增轻量个性化实时模式。输入是中文弹幕，输出保持中文内容并按普通话发音，同时尽量保留经授权日语角色素材中的音色和性格。直播期间完全离线，不依赖训练服务器。

硬性目标：

- 实时个性化模式不创建 CUDA 上下文，不占用 NVIDIA 显存。
- 音色模型通过一个文件导入，不要求用户再次选择权重、参考音频和配置文件。
- 本地 RTX 3060 Laptop 6 GB 不参与推理。
- 20 个中文字符以内的弹幕在预热后，开始播放延迟中位数不超过 1 秒，P95 不超过 2 秒。
- 低负载时保持 FIFO 顺序，高负载时沿用现有过期丢弃策略。
- GPT-SoVITS 保留为高质量模式和中文教师模型，不删除现有权重、运行时或音色包。

## 2. 范围与非目标

### 2.1 本阶段包含

- 多语言 Style-Bert-VITS2 音色的 CPU ONNX 推理可行性基准。
- 单文件 `.aivmx` 音色导入、校验、注册、试听、选择和删除前的安全边界。
- 独立、签名、CPU-only 的本地推理运行时。
- 中文文本的 G2P、BERT 特征、ONNX 合成和 WAV/PCM 播放。
- 系统音色、CPU 实时音色、GPT-SoVITS GPU 高质量音色的统一目录与显式路由。
- 使用授权日语真实数据和经人工筛选的中文教师数据训练轻量模型的工作流。
- Windows 原生性能、资源、音质和稳定性验收。

### 2.2 本阶段不包含

- 在仓库、EXE 或公开 Release 中分发用户的授权角色模型或训练语料。
- 未经人工筛选就把 GPT-SoVITS 生成音频加入学生模型训练。
- 云端推理、在线翻译或直播期间下载模型。
- 把中文弹幕翻译成日语后朗读。
- 自动判断音色是否像目标角色；最终音色由用户主观验收。

## 3. 技术路线比较与选择

### 3.1 选择：多语言 Style-Bert-VITS2 + ONNX CPU

使用普通多语言 Style-Bert-VITS2，而不是仅面向日语的 JP-Extra 推理链路。音色网络和中文、日文 BERT 均导出或使用 ONNX，在 CPU Execution Provider 上运行。音色文件使用开放的 AIVMX 元数据容器，推理由本项目自己的隔离运行时完成，不依赖只支持日语合成的 AivisSpeech Engine。

选择理由：

- 训练数据格式原生区分 `JP`、`ZH`，适合保留真实日语数据并增加中文教师数据。
- Style-Bert-VITS2 已提供音色模型 ONNX 转换路径。
- AIVMX 可以把 ONNX 模型、超参数、风格向量、名称、授权与试听元数据放在单个文件中。
- ONNX Runtime CPU 可以避免 PyTorch/CUDA 推理带来的显存和大运行时成本。

### 3.2 备选：MeloTTS

MeloTTS 支持中文和日文并能进行 CPU 实时推理，但定制音色训练、ONNX 导出和单文件元数据生态不如选定路线完整。只有 Style-Bert-VITS2 的中文质量或 CPU 性能无法通过阶段门时才评估。

### 3.3 备选：单说话人小型 VITS/Piper 类学生模型

该路线可能得到更小模型和更低延迟，但日语真实数据到普通话输出的音素迁移、训练工具和音色表现风险更高。它是第二轻量化阶段，不作为首个实现。

## 4. 总体架构

保留现有 Vue、pywebview 和 Python 主程序。新增 CPU 音色注册与运行时边界，避免继续扩大 GPT-SoVITS 专用类的职责。

```text
Windows SAPI voices --------------------------┐
                                               │
AIVMX file -> AivmxVoiceRegistry -> CPU sidecar├-> SpeechCatalog -> existing speech queue
                                               │                    -> AudioPlayer
GPT pack -> VoicePackRegistry -> GPU sidecar --┘
```

### 4.1 SpeechCatalog

统一输出三类音色，不改变现有前端队列语义：

- `system:<voice-uri>`：Windows 系统音色。
- `aivmx:<model-uuid>:<style-id>`：CPU 实时个性化音色。
- `pack:<voice-id>`：现有 GPT-SoVITS GPU 高质量音色。

每条记录返回 `engine_kind`、显示名称、语言、资源模式、健康状态、是否可选和可用风格。前端根据前缀显式路由，禁止失败时静默切换成其他声音。

### 4.2 AivmxVoiceRegistry

职责：

- 扫描应用数据目录中的 `.aivmx` 文件。
- 在不启动模型推理的情况下读取有限的元数据。
- 校验文件大小、扩展名、SHA-256、模型 UUID、架构、语言、风格与授权字段。
- 生成稳定的音色键和健康状态。
- 使用临时目录、完整校验和原子替换安装文件。

主进程不得执行 ONNX 图，也不得信任模型声明的外部路径。AIVMX 必须是单文件模型，不接受 ONNX external data、符号链接、重解析点或路径引用。

### 4.3 CpuVoiceRuntimeRegistry

CPU 运行时与桌面 EXE、用户音色分开存储并单独签名。注册表验证：

- Windows x86-64 平台与引擎 API 版本。
- Ed25519 签名和完整文件集合。
- ONNX Runtime CPU provider，运行时中不得包含 CUDA provider、cuDNN 或 PyTorch CUDA 包。
- 固定的 Style-Bert-VITS2 推理代码提交、中文/日文 BERT ONNX、分词器、OpenJTalk 与中文 G2P 资源。

运行时放在可配置的数据盘。Release 构建不得在首次直播时从 Hugging Face 或其他网站下载公共模型。

### 4.4 CpuVoiceRuntimeManager

职责：

- 在随机回环端口启动 CPU sidecar。
- 通过标准输入传递随机会话令牌，不把令牌放进命令行或日志。
- 强制设置 `CUDA_VISIBLE_DEVICES=-1`、离线环境变量和有限 CPU 线程数。
- 加载一个选定 AIVMX 音色并预热。
- 合成、取消、健康检查、一次崩溃重启和确定性关闭。
- 上报启动、加载、首音、整句、RSS、CPU 使用率和 provider 列表。

sidecar 只允许读取经注册的音色根目录。关闭播报、切换账号或退出应用后必须停止进程并释放内存。

### 4.5 MultilingualOnnxSpeechService

服务解析 `aivmx:` 音色键，检查音色和 CPU 运行时兼容性，调用 sidecar 并把 WAV/PCM 交给现有播放器。语速和音量使用统一的前端范围，转换成引擎参数时必须做上下限约束。

预热发生在用户启用 CPU 个性化播报或切换到 CPU 音色时。预热完成前界面显示“正在加载 CPU 音色”，不得以其他音色代读。

## 5. AIVMX 音色合同

用户最终只导入一个文件：

```text
haibara-zh-realtime.aivmx
```

必须满足：

- AIVM manifest 版本受支持。
- 架构为多语言 `Style-Bert-VITS2`，不能标记为仅日语引擎。
- 至少声明中文输出能力与一个默认风格。
- ONNX 模型不使用 external data。
- 包含模型名称、UUID、说话人、风格向量、超参数、授权文本或授权标识、版本和创建者信息。
- 用户在安装时再次确认拥有训练、合成语音和公开直播权限。

应用额外保存安装 SHA-256、安装时间与本地健康状态，但不改写 AIVMX 文件本身。

## 6. 训练数据与教师蒸馏

### 6.1 当前数据状态

当前 `voice/haibara_jp/sliced` 有 54 条、总计约 148.3 秒的单声道 32 kHz 16-bit PCM WAV；27 条短于 2 秒。格式可用于跑通流程，但不足以训练最终音色。

### 6.2 真实日语数据

最终建议选择 1–3 小时干净、单人、录音条件相对一致的授权日语素材。最低可先用 30–60 分钟做第一轮。以 2–10 秒完整句子为主体，台词逐字匹配实际发音。

数据记录：

```text
raw/ja/000001.wav|haibara_jp|JP|そんな顔をしないで。
```

保留 10%–15% 独立测试集，并按节目、录音批次或场景分割，防止相邻片段泄漏。

### 6.3 中文教师数据

先用真实日语数据微调多语言学生模型并测试中文，不预先假设一定需要合成数据。若中文发音、韵律或音色不通过，则使用现有 GPT-SoVITS 音色生成覆盖直播场景的中文教师候选。

教师数据必须：

- 使用固定中文测试与训练文本，覆盖短弹幕、数字、人名、英文缩写、网络词和标点。
- 由用户剔除错音、口音异常、重复、吞字、噪声和明显 AI 感样本。
- 与真实日语数据分开存储、标记来源和生成参数。
- 比较 0%、25%、50% 中文教师数据配比，不默认让合成数据占主导。

记录格式：

```text
raw/zh/000001.wav|haibara_jp|ZH|你不要露出那种表情。
```

## 7. 导入与前端体验

导入弹窗改为两个明确入口：

- “实时 CPU 音色”：选择一个 `.aivmx`，显示元数据、授权、语言和预估资源，校验后安装并 CPU 试听。
- “高质量 GPU 音色”：保留现有 GPT/SoVITS 四步向导和 GPU 运行时管理。

音色下拉项显示标签：

- `系统 · 零模型`
- `实时 CPU · 零显存`
- `高质量 GPU`

CPU 音色不显示 GPU 运行时错误。导入成功但尚未安装 CPU 运行时时，显示“音色已导入，等待 CPU 运行时”；安装并试听通过后才进入可播报列表。

## 8. 数据流与生命周期

### 8.1 开启实时 CPU 音色

```text
选择 aivmx 音色
  -> 重新校验文件哈希与 CPU runtime
  -> 启动 sidecar
  -> 加载公共 BERT/G2P 与音色 ONNX
  -> 合成但不播放预热短句
  -> 状态 ready
  -> 接收中文弹幕
  -> 文本规范化/G2P/BERT/ONNX
  -> WAV/PCM 播放
```

### 8.2 停止与故障

- 关闭播报：取消当前请求、清队列、停止播放、退出 sidecar。
- 单条文本错误：跳过该条并继续队列。
- 模型或 provider 错误：停止 CPU 音色，不回退成系统或 GPU 音色。
- sidecar 崩溃：自动重启一次；第二次失败后关闭播报并显示结构化错误。
- 音色文件变化：立即失效，需要重新校验和试听。
- 运行时文件变化：签名验证失败，拒绝启动。

## 9. 性能与资源阶段门

先使用公开、许可兼容的多语言 Style-Bert-VITS2 AIVMX 测试模型做原生 Windows 基准，再接入用户模型。进入正式实现必须证明 CPU 推理链路可工作；最终完成需要满足：

- `nvidia-smi` 中没有 CPU sidecar 的计算进程，显存增量为 0 MB。
- ONNX provider 列表只有 CPU provider。
- 20 个中文字符以内，热态开始播放延迟中位数不超过 1 秒，P95 不超过 2 秒。
- 冷启动、模型加载和预热耗时有单独显示，不伪装成合成延迟。
- CPU 线程默认 2–4 个且可配置，不能占满 i7-12700H。
- 记录工作集与峰值 RSS；官方 AivisSpeech Engine 的 1.5 GB 空闲 RAM 要求只作为资源参考，不作为本实现的达标证明。
- 与 OBS 同时运行两小时无崩溃、持续内存增长或队列失控。

若多语言 Style-Bert-VITS2 ONNX 在本机无法满足热态延迟门槛，则停止扩大该运行时，转入第 3.3 节的小型 VITS 学生模型评估。

## 10. 安全、隐私与许可

- 音色、语料、教师输出、授权原件和训练检查点保持在 Git 忽略目录或用户指定数据盘。
- 主程序不反序列化 PyTorch pickle，不执行音色包内代码。
- 模型只通过隔离 sidecar 的 ONNX Runtime 加载。
- 所有安装均使用暂存目录、哈希校验和原子替换。
- localhost API 需要随机令牌并限制包内相对路径。
- 日志不记录完整弹幕历史、完整授权台词、模型内容或会话令牌。
- Style-Bert-VITS2 推理代码的 AGPL-3.0 义务通过独立运行时源码、许可证与对应源码链接履行；aivmlib、ONNX Runtime 和其他依赖逐项保留许可证。
- 公开 Release 不包含用户音色，用户对音色和生成内容的授权负责，应用保留本地确认记录。

## 11. 测试策略

### 11.1 自动化测试

- AIVMX：元数据、架构、UUID、语言、授权、external data、超大文件、损坏文件、链接和原子安装。
- CPU runtime：签名、额外文件、哈希变化、错误平台、错误 provider 和离线启动。
- manager/client：令牌、随机端口、加载、预热、WAV、取消、崩溃重启、关闭和并发拒绝。
- 路由：三类音色只能进入对应引擎，失败时不静默降级。
- 前端：单文件导入、资源标签、状态、试听、选择、错误和设置持久化。
- 队列：低压 FIFO、高压过期、切换音色清理和停止。

### 11.2 原生 Windows 验收

- 公开测试 AIVMX 的中文合成与播放。
- 用户 AIVMX 的固定中文测试集 A/B。
- 冷启动、热态延迟、CPU、RSS、显存和进程释放。
- OBS 共存与两小时压力测试。
- EXE、CPU runtime 和音色文件均在无 Python、无网络条件下启动。

## 12. 迁移与交付

- 现有两个 GPT-SoVITS 音色目录和健康记录保持不变。
- 现有 GPU runtime 保留但界面标为“高质量 GPU”，不再推荐实时弹幕使用。
- 新 AIVMX 和 CPU runtime 使用独立目录、注册表和健康状态，不复用 GPU ready 标记。
- 桌面 EXE 不内置用户音色；CPU runtime 作为单独签名构件，避免重新发布 EXE 才能更新推理依赖。
- 最终交付包括源码、测试、CPU runtime 构建脚本、运行时校验脚本、训练/导出说明和性能报告。

## 13. 完成定义

1. 用户可通过一个 `.aivmx` 文件导入中文实时个性化音色。
2. 中文弹幕保持中文并按普通话合成，不经过在线翻译。
3. 实时 CPU 模式不占用 GPU 显存并通过第 9 节延迟门槛。
4. GPT-SoVITS 仍可作为高质量模式和教师模型使用。
5. 用户确认最终音色、自然度和中文发音符合预期。
6. 自动化、原生 Windows、OBS 共存和离线验收全部通过。
7. 授权角色模型和语料不进入公开仓库或默认发行包。

## 14. 参考依据

- Style-Bert-VITS2 数据准备、`JP`/`ZH` 标记和训练流程：<https://github.com/litagin02/Style-Bert-VITS2/blob/master/docs/CLI.md>
- Style-Bert-VITS2 ONNX 转换：<https://github.com/litagin02/Style-Bert-VITS2/blob/master/convert_onnx.py>
- AIVMX 单文件格式与元数据规范：<https://github.com/Aivis-Project/aivmlib>
- AivisSpeech Engine 对 AIVMX、CPU ONNX 与日语限制的参考实现说明：<https://github.com/Aivis-Project/AivisSpeech-Engine>
- ONNX Runtime CPU Execution Provider：<https://onnxruntime.ai/docs/execution-providers/CPU-ExecutionProvider.html>
