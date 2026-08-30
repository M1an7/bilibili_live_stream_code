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

系统语音播报仅占用少量 CPU 和内存，不使用 GPU 或云服务器。选择个性化音色时，程序会按需启动独立的 GPT-SoVITS GPU 运行时；停用个性化音色或退出程序后，侧车进程结束并释放显存。

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

发布包必须使用 Ed25519 私钥签名，私钥不得提交到 Git。仅本机开发验证时可用 `-AllowUnsignedDevelopment`，同时以环境变量 `BILILIVE_ALLOW_UNSIGNED_RUNTIME=1` 启动源码版应用。桌面界面第 4 步可以选择运行时 ZIP、已解压目录以及运行时数据盘位置。

运行时安装后，个性化语音的工作顺序为：启动侧车 → 加载音色 → 生成日语试听 → 校验并保存试听 → 开始按弹幕队列播报。CUDA 或显存错误会直接显示，不会回退到其他音色。

### 环境要求

- **Python**: 3.9+
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
