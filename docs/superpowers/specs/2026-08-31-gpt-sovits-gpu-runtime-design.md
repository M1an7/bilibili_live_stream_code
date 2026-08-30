# GPT-SoVITS GPU Runtime 设计

日期：2026-08-31  
状态：对话设计已确认，等待书面规格复核  
依赖设计：`2026-08-31-voice-pack-import-runtime-design.md`  
依赖里程碑：`2026-08-31-voice-pack-import-foundation.md`

## 1. 目标

为现有直播工具加入可实际播报的 GPT-SoVITS `v2Pro`/`v2ProPlus` GPU 推理闭环。首个真实验收音色是项目 `voice/haibara_jp` 中已经验证的 `v2Pro` 日语音色。

本版本必须做到：

1. 使用本机 RTX 3060 Laptop 6 GB，通过 CUDA FP16 合成日语。
2. 语音未开启时不启动运行时、不占用显存。
3. 选择个性化音色并开启播报时启动独立旁车、加载音色并预热。
4. 关闭播报、切回系统音色或退出应用时停止旁车并释放显存。
5. 预热后的短弹幕以首个可播放 PCM 为延迟指标，目标 1 秒内，验收上限 3 秒。
6. GPU 推理失败时不静默改用系统音色或 CPU；界面保留明确错误并允许用户手动切换。
7. 主程序继续以小型单文件 EXE 发布；CUDA、PyTorch、GPT-SoVITS 源码和基础模型作为独立运行时安装一次。

## 2. 非目标

- 本里程碑不训练或继续微调音色。
- 不把 CUDA、PyTorch、基础模型或授权角色音色打进主 EXE。
- 不支持云端推理、局域网推理或对外监听端口。
- 不保证热加载状态低于 1 GB 显存；低延迟优先于热加载时的显存占用。
- 不在单条弹幕之间反复把完整模型从 CPU 移到 GPU，因为这会放大首条延迟并增加设备状态错误风险。
- 不自动回退到 CPU。CPU 将来作为用户明确选择的另一运行模式。
- 不立即迁移现有前端弹幕队列到 Python；本里程碑复用现有 FIFO、过载清理和跳过控制，只把个性化合成与播放放在后端。

## 3. 已确认约束

- 本地 GPU：NVIDIA GeForce RTX 3060 Laptop GPU，6144 MiB。
- 设计时检测到可用显存约 4791 MiB；运行时仍须每次重新检测，不能依赖该数值。
- 模型：`GPT_haibara_jp.ckpt`、`SoVITS_haibara_jp.pth`，版本 `v2Pro`。
- 参考音频：32 kHz、16-bit、单声道 PCM WAV，时长 3.42 秒。
- 参考语言和输出语言均为日语 `ja`。
- 直播工具目标：首包优先；低压力按 FIFO 顺序播报；过载时继续沿用已有等待时间和过期消息策略。
- 独立运行时可以占用数 GB 磁盘。安装向导必须允许选择数据目录，默认仍为本地应用数据目录；不能假定系统盘空间充足。

## 4. 方案选择

### 4.1 采用：固定版本的独立 GPU 旁车

主应用管理一个固定版本、带清单的 Windows GPU Runtime。运行时包含 Python、CUDA 版 PyTorch、固定 GPT-SoVITS 源码、日语文本前端、FFmpeg 和推理所需基础模型。授权音色包只包含用户权重、参考音频、文本和授权材料。

旁车不直接暴露官方 `api_v2.py`。项目提供一个薄包装入口，固定可调用能力、限制文件根目录、加入随机会话令牌，并把官方 TTS Pipeline 的输出转换为内部流式 PCM 协议。

优点：

- 主程序与大型运行时解耦。
- 运行时升级不需要重新分发每个音色。
- 主应用可以可靠控制进程、显存释放、取消和错误映射。
- 不暴露官方 API 中与本工具无关的控制和任意路径入口。

### 4.2 不采用：把运行时塞进主 EXE

这会使 EXE 变成数 GB，启动时需要解包大量文件，发布平台单文件限制、杀毒误报和重复下载都会恶化。

### 4.3 不采用：每条弹幕临时加载 GPU

模型权重文件虽约 290 MB，但完整推理还包含文本模型、HuBERT、说话人模型、CUDA 上下文和中间张量。每条消息重新加载会牺牲用户要求的 1–3 秒延迟目标。

## 5. 运行时目录与合同

运行时安装目录：

```text
<data-root>\runtimes\gpt-sovits-v2pro-cu126-1\
├── runtime-manifest.json
├── runtime-manifest.sig
├── bin\
│   ├── python.exe
│   ├── ffmpeg.exe
│   └── ffprobe.exe
├── engine\
│   ├── sidecar.py
│   ├── GPT_SoVITS\
│   └── pinned-source.json
├── python\
└── pretrained_models\
    ├── chinese-hubert-base\
    ├── chinese-roberta-wwm-ext-large\
    ├── v2Pro\
    ├── sv\
    └── open_jtalk\
```

首版运行时固定：

- Windows x86-64。
- CUDA 12.6 兼容构建。
- FP16 GPU 推理。
- GPT-SoVITS `v2Pro` 与 `v2ProPlus`。
- 引擎接口版本 `1`。
- 日语文本与提示语言 `ja`。

`runtime-manifest.json` 至少记录：

- `runtime_id`、清单版本、平台、架构、构建版本。
- GPT-SoVITS 固定提交号。
- Python、PyTorch 和 CUDA 构建标识。
- 支持的模型版本、语言、引擎接口版本。
- 入口点相对路径。
- 每个运行时文件的 SHA-256 与大小。
- `gpu=true`、`precision=fp16`、最低计算能力和最低总显存声明。

生产版只安装项目发布密钥签名的运行时。开发模式可以通过显式环境变量注册未签名目录，但该能力不得成为 Release 默认行为。

## 6. 主应用组件

### 6.1 RuntimeRegistry

- 扫描并验证已安装运行时清单、签名、平台、接口版本和文件哈希。
- 根据音色的 `model_version` 选择兼容 GPU Runtime。
- 对缺失、损坏、不兼容和签名错误返回结构化状态。
- 不导入运行时中的 Python 模块；主进程只把运行时当作外部程序。

### 6.2 RuntimeInstaller

- 支持从 ZIP 和已解压目录安装运行时。
- 原生文件/目录选择器返回路径后，后台任务执行大小限制、路径穿越检查、签名与 SHA-256 校验。
- 在随机 staging 目录解包，并原子移动到最终运行时目录。
- 允许用户选择数据根目录；保存设置前先验证目标盘可写和剩余空间。
- 更新失败保留旧运行时。

### 6.3 GpuRuntimeManager

状态机：

```text
missing → stopped → starting → ready → busy
                     ↓          ↓       ↓
                   failed ← stopping ← cancelling
```

职责：

- 在需要时选择随机回环端口并生成随机会话令牌。
- 启动旁车，设置 `CUDA_VISIBLE_DEVICES=0`、线程数和离线环境变量。
- 等待带令牌的握手与健康检查；启动超时后终止已确认属于本应用的进程。
- 一次只加载一个音色。切换音色时取消当前合成并清空旧音频。
- 旁车异常退出时最多自动重启一次；连续失败后保持 `failed`，不形成重启循环。
- 关闭播报、切回系统音色或应用退出时关闭旁车；超时后终止进程树。
- 记录启动耗时、模型加载耗时、首包耗时、整句耗时、峰值显存和释放后显存，不记录完整弹幕或参考文本。

### 6.4 PersonalizedSpeechService

- 对主应用提供 `prepare_voice`、`speak`、`stop` 和 `shutdown`。
- `prepare_voice` 完整校验音色包和运行时，启动旁车、加载用户权重与参考素材，并执行短句预热。
- `speak` 只接受已准备音色和日文文本，转发旁车 PCM 流。
- `stop` 同时取消旁车生成和本地播放。
- 不复用 `SystemSpeechService` 的 SAPI 参数语义；ApiService 根据 `voice_key` 明确路由。

### 6.5 StreamingAudioPlayer

- 使用 Windows 可打包的 PortAudio 输出（Python `sounddevice.RawOutputStream`）。
- 接受旁车返回的 16-bit、单声道 PCM 块及采样率。
- 第一个完整块到达后立即播放，不等待完整 WAV。
- 支持停止、丢弃旧 token 的迟到音频、音量缩放和输出错误映射。
- 播放器只消费内存 PCM，不允许旁车指定任意播放路径。

## 7. 旁车协议

旁车监听 `127.0.0.1` 的随机端口。所有请求必须携带主进程生成的高熵会话令牌。旁车只接受来自回环地址的连接。

接口：

- `GET /health`
- `POST /load_voice`
- `POST /synthesize`
- `POST /cancel`
- `POST /unload_voice`
- `POST /shutdown`

`load_voice` 只接受主进程解析后的 `voice_id`。旁车根据启动时下发的允许根目录自行构造固定合同路径，拒绝客户端传入任意模型路径。

`synthesize` 请求：

```json
{
  "request_id": "random-id",
  "voice_id": "haibara-jp",
  "text": "こんばんは",
  "text_lang": "ja",
  "prompt_lang": "ja",
  "speed_factor": 1.0
}
```

流响应由一条 JSON 头和多个二进制 PCM 帧组成。JSON 头包含采样率、声道、采样宽度、请求 ID 和引擎版本。旁车不得返回任意磁盘路径。

取消按 `request_id` 生效。旧请求的迟到块由主进程播放 token 再过滤一次。

## 8. GPU 与显存生命周期

### 8.1 启动前检查

旁车启动后、加载模型前通过 PyTorch 查询：

- CUDA 是否可用。
- GPU 名称、计算能力、总显存和当前可用显存。
- 当前 PyTorch 是否支持该设备。

运行时清单声明的最低要求只是兼容边界；是否有足够空闲显存由一次真实预热决定。不得用估算值把音色直接标为可用。

### 8.2 常驻规则

- 系统音色或语音关闭：旁车不运行，目标显存占用为 0。
- 个性化音色已启用：旁车和当前音色保持热加载，优先保证低延迟。
- 用户关闭语音、切回系统音色或退出：先取消合成，再关闭旁车。
- 首版不在直播等待间隙自动回收；自动空闲回收会在测得真实加载耗时后作为可选“节省显存”模式加入。

### 8.3 OOM 和资源竞争

- CUDA OOM 映射为 `gpu_out_of_memory`，界面展示加载前后可用显存。
- 失败后停止旁车，确保释放已分配显存。
- 不自动降低到 CPU，不自动更换音色。
- 批大小固定为 1；短弹幕不并行合成多个请求。

## 9. 音色健康状态与试听晋级

当前 `runtime_required` 音色只有在以下步骤全部成功后晋级 `ready`：

1. 音色包结构和 SHA-256 再验证。
2. 兼容的签名 GPU Runtime 验证通过。
3. 旁车在 CUDA FP16 模式启动并报告正确 GPU。
4. 用户 GPT/SoVITS 权重在受限旁车中加载成功，确认实际版本一致。
5. 使用音色参考素材合成固定日语试听句。
6. PCM 参数有效、非静音、时长处于合理范围。
7. 主应用保存 `preview.wav`、更新清单哈希并原子写入音色健康记录。

健康记录保存在应用数据根目录下的独立 `voice-state` 目录，包含音色清单摘要、运行时 ID、运行时清单摘要、验证时间和性能指标。任一摘要变化都会让音色回到 `runtime_required`，避免使用过期验证结果。

## 10. 前端与 API 变化

### 10.1 统一 voice key

前端调用后端播报时必须传递完整 `voice_key`：

```text
system:<voice-uri>
pack:<voice-id>
```

不能再把个性化音色退化为空的 `voiceURI`。系统音色继续走现有系统服务；`pack:` 音色走 GPU 服务。

### 10.2 新增 API

- `choose_runtime_source(kind)`
- `start_runtime_install(request)`
- `get_runtime_job(job_id)`
- `get_gpu_runtime_status()`
- `prepare_voice(voice_key)`
- `preview_voice(voice_key, text)`
- `release_personalized_voice()`

`speak_text` 增加 `voice_key` 参数并保留旧调用兼容。所有 API 返回统一结构化错误。

### 10.3 界面

- 音色导入第 4 步在缺少运行时时显示“安装 GPU 运行时”。
- 运行时安装支持 ZIP、目录和数据位置选择。
- 个性化音色显示 GPU 名称、运行时版本、健康状态、预热结果、峰值显存和最近首包延迟。
- 选择未就绪音色时先执行准备流程；成功后才允许开启语音。
- 状态新增“加载 GPU”“正在预热”“等待弹幕”“显存不足”“运行时异常”。
- 保留系统音色作为用户可见、可手动选择的独立选项。

## 11. 数据流

```text
B 站弹幕
  → 现有前端队列与过载策略
  → speak_text(text, voice_key, rate, volume)
  → ApiService 显式路由
  → PersonalizedSpeechService
  → 带令牌的回环旁车
  → GPT-SoVITS CUDA FP16 流式推理
  → PCM 分块
  → StreamingAudioPlayer
  → Windows 输出设备
```

跳过、关闭或切换音色：

```text
前端 token 失效
  → 后端停止播放器
  → 旁车取消 request_id
  → 丢弃迟到 PCM
```

## 12. 安全边界

- 主进程永不反序列化 `.ckpt`/`.pth`。
- 只有固定版本旁车在受控目录内加载模型。
- 旁车无管理员权限、只监听回环、要求随机令牌。
- 模型和参考素材路径由固定根目录与清单相对路径生成，不接受网络请求中的任意绝对路径。
- 生产运行时必须验证 Ed25519 签名和 SHA-256。
- 运行时启动环境启用离线模式；不在直播期间下载模型。
- 日志脱敏，不记录完整弹幕、参考台词、模型内容或会话令牌。

## 13. 测试策略

### 13.1 自动测试

- Runtime 清单、签名、平台、哈希、路径穿越和原子安装测试。
- 使用假旁车验证启动、握手、令牌、超时、异常退出、一次重启和清理。
- 使用假 PCM 流验证首块播放、停止、跳过、音量和迟到块过滤。
- ApiService 路由测试，确保 `system:` 与 `pack:` 不混用。
- 前端 voice key、准备状态、运行时安装、错误提示和不静默回退测试。
- Windows 打包测试，确保主 EXE 包含客户端与播放器，但不包含 CUDA、PyTorch、运行时或音色。

### 13.2 真实 GPU 验收

真实验收通过显式环境变量开启，不进入普通单元测试：

- GPU：RTX 3060 Laptop 6 GB。
- 音色：`haibara-jp` v2Pro。
- 输入：固定短日语句和一组典型短弹幕。
- 验证：非静音、可播放、日语内容正确、音色人工听感通过。
- 记录：运行时启动、模型加载、预热、首 PCM、整句、峰值显存、旁车退出后显存。
- 预热后首 PCM 目标不超过 1 秒，允许上限 3 秒。
- 关闭个性化播报后 5 秒内旁车退出；随后确认不再存在本应用 CUDA 进程。

## 14. 发布物

```text
BiliLiveTool.exe
BiliLiveTool-GPTSoVITS-GPU-CU126-Runtime.zip
```

主 Release 不包含授权音色。运行时 ZIP 不包含用户音色，只包含共用引擎和基础模型。开发构建脚本生成运行时清单、哈希和签名；主程序构建继续断言不存在 `.ckpt`、`.pth`、CUDA DLL 和 `voice/`。

## 15. 实施顺序

1. Runtime 清单、验证、安装与状态 API。
2. 假旁车驱动的 GpuRuntimeManager 状态机。
3. PCM 播放器与个性化 SpeechService。
4. voice key 端到端路由及前端加载状态。
5. 固定 GPT-SoVITS 提交的 GPU 旁车包装器。
6. Windows CU126 Runtime 构建脚本和签名流程。
7. 使用 `haibara-jp` 做真实 GPU 预热、试听、延迟与显存验收。
8. 完成 EXE/Runtime 双发布物验证。

## 16. 验收定义

本 GPU 版本只有同时满足以下条件才算完成：

- 主应用在无运行时时仍可正常使用系统语音。
- 签名 GPU Runtime 可以独立安装、验证和移除。
- `haibara-jp` 可以在 RTX 3060 Laptop 上以 CUDA FP16 加载并合成日语试听。
- 试听成功后音色从 `runtime_required` 晋级 `ready` 并出现在个性化音色选项中。
- 真实弹幕可按现有队列策略使用该音色播报。
- 跳过、关闭、切换和退出能取消合成与播放。
- 关闭个性化播报后旁车退出并释放显存。
- 主 EXE 不包含运行时、基础模型或授权音色。
- 自动测试、前端生产构建、Windows 打包断言和真实 GPU 验收全部通过。
