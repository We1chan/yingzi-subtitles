# 映字 · 本地双语字幕工作台

Windows 桌面应用：选择视频，在本机识别中文／日语、对齐时间轴，将日语翻译为简体中文，并预览、编辑、导出字幕。

![映字字幕工作台](docs/screenshot.png)

## 首次安装

1. 在 Windows 上下载本仓库的 ZIP 并解压，或使用 Git 克隆仓库。
2. 安装 Python 3.12 或 uv；推荐 NVIDIA 显卡。识别也支持 CPU 模式。
3. 双击 `setup.cmd`，等待依赖、模型与翻译引擎下载完成。建议预留约 30 GB 磁盘空间。
4. 通过新建的桌面快捷方式启动应用。

仓库只包含源码、安装与测试脚本。模型权重、虚拟环境、运行时二进制、测试音视频和生成字幕均由脚本下载或运行时生成，不存入 Git。

## 开始使用

双击桌面的 **映字 - 双语字幕**，或此目录的 **启动映字.vbs**。

1. 选择或拖入视频。支持常见 MP4、MKV、MOV、AVI、WebM 等格式，也可以选择音频。
2. 选择语言。日语视频可以明确选择“日语”；中文选择“中文”。有配音的多音轨视频，请选择正确音轨。
3. 保留“日文原文 + 简体中文译文”，点击“生成双语字幕”。中文片段直接保留中文字幕。
4. 完成后单击字幕定位画面；双击修改文字或起止时间。修改原文会清空该条已有译文，点击“继续翻译”补全。
5. 点击“导出 SRT / VTT”选择双语、原文或中文。播放器加载同名 SRT 即可显示字幕。

导出的是外挂字幕文件，原视频保持原样。应用内支持字幕叠加预览；当前版本不提供字幕烧录或视频重新编码导出。

## 文件与模型

每次处理创建独立 `output/视频名_日期时间_编号/` 文件夹，包含：

- `bilingual.srt`：日文在上、中文在下，共用同一时间轴。
- `original.srt`、`translated.srt`：原文和中文版本。
- `bilingual.vtt`：WebVTT 双语字幕。
- `subtitles.yingzi.json`：可再次打开编辑的字幕工程。
- `recognition.json`：识别原始结果；`run.log`：运行日志。

编辑会自动保存字幕工程；已经导出的 SRT 不会自动更新，需要再次导出。翻译失败或取消时，已经保存的原文与字幕工程仍然保留。“继续翻译”只处理还没有译文的日语字幕。

三个模型均保存在本目录的 `models/`，推理只加载本地文件：

| 用途 | 模型 | 来源 |
| --- | --- | --- |
| 中日语音识别 | Qwen3-ASR-1.7B | https://huggingface.co/Qwen/Qwen3-ASR-1.7B |
| 时间轴 | Qwen3-ForcedAligner-0.6B | https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B |
| 日中翻译 | Qwen3-4B-Instruct-2507，Q4_K_M | https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF |

语音检测使用 Silero VAD；音轨提取使用 imageio-ffmpeg 自带的 FFmpeg；翻译由 llama.cpp 的 Windows CUDA 引擎运行。第三方项目遵循各自许可证。模型下载脚本会验证大文件 SHA-256，并记录仓库修订版本。

识别、对齐、翻译分阶段加载。自动模式优先使用 NVIDIA GPU，显存不足时部分识别模型放入内存；也可以选择“CPU 兼容”。速度会受显存占用和视频时长影响。

## 安装与修复

应用使用本目录 `.venv` 中的 Python 3.12 环境。双击 `setup.cmd` 可以重新安装依赖并续传模型。安装脚本使用 `requirements-lock.txt` 固定已验证的依赖版本。需要 Python 3.12 或已安装的 uv；运行环境与模型初次安装需要联网，安装后视频识别和翻译不访问外部服务。

```powershell
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe -m pytest -q
```

需要运行真实模型流程检查时，执行下列命令；脚本会先下载下文列出的公开测试音频，再在 `output/` 生成验证字幕：

```powershell
.\.venv\Scripts\python.exe tools\run_validation.py
.\.venv\Scripts\python.exe tools\smoke_ui_flow.py
```

翻译引擎仅监听随机端口的 `127.0.0.1`，使用每次启动随机生成的 API 密钥；任务结束后关闭。原始媒体不会被上传。

## 使用范围

当前按普通对白设计。背景音乐很重、多人同时说话、含糊发音、人名和专有名词仍可能识别错误；在“人名、地名、专业词汇”中填写提示可帮助识别。自动字幕与译文建议在导出前校对。

## 验证素材来源

- 日语：Kotoba-Whisper v2.2 模型仓库的 `sample_audio/sample_diarization_japanese.mp3`，取前 16 秒合成测试视频。
- 中文：Qwen3-ASR 官方演示音频 `https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-ASR-Repo/asr_zh.wav`。

这些短片仅用于功能验证，不代表识别精度或速度基准。
