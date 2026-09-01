# 哔哩哔哩直播工具

1. 用于在准备直播时获取第三方推流码，以便可以绕开哔哩哔哩直播姬，直接在如OBS等软件中进行直播；
2. 支持开播时定义标题和分区；
3. 支持弹幕监控（含进场消息和礼物消息）以及发送弹幕；
4. 支持低延迟弹幕语音播报、系统音色选择，以及 GPT-SoVITS 个性化音色训练结果的安全导入。

## 声明

**本程序仅用于学习和交流，禁止用于商业或其他目的，任何不当使用导致的问题自行负责。*

## 使用教程

1. 扫码登录B站账号；
2. 填写标题并选择分区（首次使用需要点击`同步`）；
3. 点击 `开始直播` 来开始直播；
4. 在 *推流码* 复制链接和推流码至第三方推流工具；
5. 在 *弹幕* 界面，可以查看并发送弹幕；
6. 点击 `停止直播` 或关闭软件来停止直播，**使用 OBS 的 `停止直播` 并不会停止直播**；

## 自行构建

### 语音播报界面预览

1. 在 `frontend` 目录运行 `npm run dev -- --host 0.0.0.0`；
2. 打开 `http://localhost:5173/?speech-preview=1`；
3. 开启“语音播报”，点击“模拟弹幕”测试系统默认音色；
4. 生产构建后通过 `python main.py` 登录账号，进入弹幕页，用真实弹幕完成最终验证。

`speech-preview` 只在 Vite 开发环境生效，不会在生产构建中创建模拟登录。

桌面端会优先使用页面提供的 Web Speech API；如果 QtWebEngine 不支持，则自动降级到本机系统语音：

- Windows（以及可调用 `powershell.exe` 的 WSL）：使用内置 SAPI；
- macOS：使用内置 `say`；
- Linux：使用 `espeak-ng` 或 `espeak`，未安装时界面会给出提示。

系统语音播报仅占用少量 CPU 和内存，不使用 GPU 或云服务器。个性化音色可选择 Style-Bert-VITS2 AIVMX CPU 实时模式或 GPT-SoVITS GPU 高质量模式；停用个性化音色或退出程序后，对应侧车进程结束并释放内存/显存。

### 导入 AIVMX 实时 CPU 音色

进入“弹幕”页面，点击音色选择框右侧的 `导入`，默认打开 `实时 CPU 音色`：

1. 选择一个包含 ONNX 模型、配置和风格向量的 `.aivmx`；不需要再选参考 WAV、JSON 或权重；
2. 确认训练、合成语音与公开直播使用权限，然后点击 `导入音色`；
3. 选择并安装独立 CPU 运行时 ZIP；
4. 点击 `中文试听验证`。试听固定使用中文原文和中文发音；
5. 验证通过后点击 `刷新音色下拉框`，回到弹幕页手动选择音色，再使用顶部的语音播报开关启动。刷新按钮不会选择音色，也不会自动启动播报。

实时 CPU 模式固定使用 ONNX Runtime 的 `CPUExecutionProvider`，默认最多 4 个推理线程、单路顺序合成，语音侧车以低于正常的进程优先级运行。它不加载 Torch/CUDA，语音推理显存为 0 MB；网页/Qt 界面自身仍可能因硬件加速占用少量显存。停用播报会退出 CPU 侧车并释放运行时内存。

在 Windows PowerShell 中把便携 CPU 运行时构建到 D 盘：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_cpu_runtime.ps1 `
  -OutputRoot "D:\BiliLiveCpuRuntimeBuild" `
  -CacheRoot "D:\BiliLiveCpuRuntimeBuild\.build-cache\cpu-runtime" `
  -Source China `
  -AllowUnsignedDevelopment
```

这不会创建 Conda 环境，也不会修改系统 Python、注册表或 PATH。路径分别是：

```text
构建缓存：D:\BiliLiveCpuRuntimeBuild\.build-cache\cpu-runtime\
构建临时目录：D:\BiliLiveCpuRuntimeBuild\.build-temp\cpu-runtime\
便携运行时目录：D:\BiliLiveCpuRuntimeBuild\.build-temp\cpu-runtime\stage\style-bert-vits2-cpu\
Release ZIP：D:\BiliLiveCpuRuntimeBuild\BiliLiveTool-Style-Bert-VITS2-CPU-<version>.zip
```

`-Source China` 默认使用清华 PyPI 镜像与 `hf-mirror.com`；`-Source Official` 可切回官方源。脚本固定 Style-Bert-VITS2 与 aivmlib 源码提交、Python 3.11 便携发行版、CPU-only 依赖哈希，以及中文 ONNX BERT/分词器哈希；镜像内容也必须通过相同 SHA-256 校验，不包含用户音色。开发 ZIP 可用 `-AllowUnsignedDevelopment`。正式发布必须改用 `-PrivateKeyPath`，并通过 `-SigningPython` 指定一个已安装 `cryptography` 的构建环境；精简运行时本身不携带签名依赖。验证命令：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_cpu_runtime.ps1 `
  -RuntimeRoot "D:\BiliLiveCpuRuntimeBuild\.build-temp\cpu-runtime\stage\style-bert-vits2-cpu" `
  -AllowUnsignedDevelopment
```

软件导入后，默认保存到 `%LOCALAPPDATA%\BiliLiveTool\runtimes\.cpu\style-bert-vits2-cpu\`；如果界面中选择了运行时数据盘，则保存在该目录的 `.cpu\style-bert-vits2-cpu\` 下。桌面 EXE 与 CPU 运行时分开发行，未使用个性化音色的用户无需下载运行时 ZIP。

### 导入 GPT-SoVITS 个性化音色

进入“弹幕”页面，点击音色选择框右侧的 `导入`，按四步向导选择：

- GPT `.ckpt` 权重；
- SoVITS `.pth` 权重；
- 3–10 秒 PCM WAV 参考音频及完全对应的日文台词；
- 授权说明文件，并确认拥有 AI 训练、合成语音和公开直播使用权限。

首版接受 `v2Pro` 与 `v2ProPlus`。导入过程会把权重当作不透明字节复制，不会在主程序中反序列化模型；同时校验路径、文件类型、PCM WAV、大小和 SHA-256。模型、参考音频和授权文件不会提交到仓库，也不会打包进 EXE。

Windows 中导入后的标准音色包保存在：

```text
%LOCALAPPDATA%\BiliLiveTool\voices\<voice-id>\
```

从源码开发时可用环境变量 `BILILIVE_DATA_HOME` 指定测试数据目录，也可以用 `BILILIVE_RUNTIME_HOME` 单独指定 GPU 运行时所在的数据盘。导入完成后先显示为 `等待 GPU 运行时`（`runtime_required`），不会出现在可播报音色列表，也不会静默改用错误音色。安装运行时后点击 `GPU 试听并启用`；只有通过真实 CUDA FP16 合成、非静音 PCM 校验与试听后，音色才会进入播报下拉框。

### GPT-SoVITS GPU 运行时

GPU 运行时和桌面 EXE 分开发行，支持 NVIDIA CUDA 12.6、GPT-SoVITS `v2Pro` / `v2ProPlus` 与日语合成。应用使用随机令牌访问仅绑定 `127.0.0.1` 的侧车服务；模型路径只能位于应用的音色数据目录内。

在 Windows PowerShell 中把运行时构建到空间充足的数据盘：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_gpu_runtime.ps1 `
  -BuildRoot "D:\BiliLiveRuntimeBuild" `
  -PrivateKeyPath "D:\keys\runtime-ed25519-private.pem"
```

脚本固定官方 GPT-SoVITS 提交，创建 Python 3.10 + PyTorch CU126 环境，下载基础预训练模型、Open JTalk 字典与 FFmpeg，并输出独立的：

```text
D:\BiliLiveRuntimeBuild\artifacts\BiliLiveTool-GPT-SoVITS-CU126-<version>.zip
```

脚本会校验并解压固定的 Python 3.10 独立发行版到 `BuildRoot`，不会修改系统 Python、注册表或 PATH。已有 `pretrained_models.zip` 时可通过 `-PretrainedModelsArchive` 复用数据盘上的归档，避免重复下载。

运行时 ZIP 超过 GitHub 单文件限制时，构建器会同时输出 `*.zip.parts` 目录（每卷不超过 1900 MiB）和 `parts-manifest.json`。下载全部分卷后，先重组并校验，再在应用中导入生成的完整 ZIP：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\join_gpu_runtime_parts.ps1 `
  -ManifestPath ".\BiliLiveTool-GPT-SoVITS-CU126-<version>.zip.parts\parts-manifest.json"
```

3060 Laptop 6GB 的原生 Windows 便携运行时测试中，侧车启动约 6.35 秒、音色加载约 25.97 秒，热态短弹幕首包约 1.56 秒、整句约 1.72 秒，运行时报告峰值显存约 1.7GB；关闭个性化语音后侧车进程和显存均释放。完整记录见 [`docs/gpu-runtime-benchmark.md`](docs/gpu-runtime-benchmark.md)。

发布包必须使用 Ed25519 私钥签名，私钥不得提交到 Git。仅本机开发验证时可用 `-AllowUnsignedDevelopment`，同时以环境变量 `BILILIVE_ALLOW_UNSIGNED_RUNTIME=1` 启动源码版应用。桌面界面第 4 步可以选择运行时 ZIP、已解压目录以及运行时数据盘位置。

运行时安装后，个性化语音的工作顺序为：启动侧车 → 加载音色 → 生成日语试听 → 校验并保存试听 → 开始按弹幕队列播报。CUDA 或显存错误会直接显示，不会回退到其他音色。

### 环境要求

- **Python**: 3.12+
- **Node.js**: 18+

### Windows 一键封装

在 Windows PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

脚本会创建独立的 `.venv-win` 构建环境、编译前端，并生成单文件无控制台窗口程序：

```text
dist\BiliLiveTool.exe
```

封装内容不包含账号 Cookie、本地配置、测试文件或个性化音色模型。系统语音可直接使用；个性化音色文件通过桌面向导导入到本地应用数据目录。

Windows 便携包请完整解压到单独文件夹后再运行，不要只把 EXE 拖到下载目录或桌面根目录。程序会在 EXE 所在目录生成 `logs/`、`config.json` 等本地文件，其中配置文件可能包含账号登录信息，请勿分享整个运行目录。

### 构建步骤

1. **克隆仓库**

   ```bash
   git clone https://github.com/ChaceQC/bilibili_live_stream_code.git
   cd bilibili_live_stream_code
   ```

2. **构建前端**

   ```bash
   cd frontend
   npm install
   npm run build
   cd ..
   ```

3. **安装后端依赖**

   ```bash
   pip install -r requirements.txt
   pip install pyinstaller Pillow
   ```

   **Linux**：无需额外系统依赖，程序使用内置的 Qt 库运行托盘。建议在 Ubuntu 20.04+ 或其他主流发行版上运行。
   
   > 若启动时提示 `Qt platform plugin "xcb" could not be found`，请安装：  
   > `sudo apt install libxcb-xinerama0 libxcb-cursor0 libnss3`

   从源码运行时还需 pip 安装：
   ```bash
   pip install PyGObject
   ```

   > 未安装时程序仍可正常运行，仅无托盘图标。打包后的二进制仅需系统包（无需 pip 安装）。

4. **准备图标 (可选)**

   - **macOS (ico -> icns)**:
     ```bash
     # 使用 sips 和 iconutil (macOS 自带)
     sips -s format png bilibili.ico --out temp_icon.png
     mkdir bilibili.iconset
     sips -z 1024 1024 temp_icon.png --out bilibili.iconset/icon_512x512@2x.png
     iconutil -c icns bilibili.iconset
     rm -rf bilibili.iconset temp_icon.png
     ```

   - **Linux (ico -> png)**:
     ```bash
     # 使用 Python Pillow 库
     python -c "from PIL import Image; Image.open('bilibili.ico').save('bilibili.png')"
     ```

5. **打包应用**

   - **Windows**:
     ```bash
     pyinstaller main.py --name BiliLiveTool --onefile --add-data "frontend/dist;frontend/dist" --icon "bilibili.ico" --noconsole
     ```

   - **macOS**:
     ```bash
     pyinstaller main.py --name BiliLiveTool --onefile --add-data "frontend/dist:frontend/dist" --icon "bilibili.icns" --hidden-import _cffi_backend --windowed
     ```

   - **Linux**:
     ```bash
     pyinstaller main.py --name BiliLiveTool --onefile \
      --add-data "frontend/dist:frontend/dist" \
      --add-data "bilibili.ico:." \
      --icon "bilibili.png" \
      --hidden-import _cffi_backend \
      --hidden-import cffi \
      --hidden-import qtpy \
      --hidden-import PyQt5 \
      --hidden-import webview.platforms.qt
     ```

6. **运行**

   构建完成后，可执行文件位于 `dist` 目录下。

## 其他

1. 支持推流码类型：RTMP和SRT；
2. 因为本人穷，用不起mac，mac用户可以自行进行测试，如果调试到可以正常运行，欢迎提交pr；
3. 社区已有基于本项目的 Tauri 重构版本，技术栈从 Python + PyInstaller 迁移至 **Tauri 2.x (Rust) + React 18 + TypeScript**，并补全了 macOS 端的适配（含托盘、窗口退出、深色模式等）。有需要的同学可以移步 [Zeppelinpp/bilibili-streamer](https://github.com/Zeppelinpp/bilibili-streamer) 查看。

### ⭐ Star 历史

   [![Stargazers over time](https://starchart.cc/ChaceQC/bilibili_live_stream_code.svg?variant=adaptive)](https://starchart.cc/ChaceQC/bilibili_live_stream_code)
