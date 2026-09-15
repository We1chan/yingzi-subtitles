# 映字 · 本地双语字幕工作台

Windows 桌面应用：选择视频，在本机识别中文／日语／英语、对齐时间轴，将日语或英语翻译为简体中文，并预览、编辑、导出字幕。支持手动选择本地识别、对齐和翻译模型。

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
2. 选择语言：自动识别，或手动指定“日语”“中文”“英语”。英语默认每条 68 字符，也可以自行调整；英文字幕保留空格与完整单词。有配音的多音轨视频，请选择正确音轨。
3. 保留“原文 + 简体中文译文（日 / 英）”，点击“生成双语字幕”。日语生成日中双语、英语生成英中双语，中文片段直接保留中文字幕；也可选择仅识别原文。
4. 完成后单击字幕定位画面；双击修改文字或起止时间。修改原文会清空该条已有译文，点击“继续翻译”补全。
5. 点击“导出 SRT / VTT”选择双语、原文或中文。播放器加载同名 SRT 即可显示字幕。

导出的是外挂字幕文件，原视频保持原样。应用内支持字幕叠加预览；当前版本不提供字幕烧录或视频重新编码导出。

## 手动选择模型

在“生成设置”中点击 **选择模型…**：

- **语音识别**：选择 Qwen3-ASR-1.7B（默认）、Qwen3-ASR-0.6B、Cohere Transcribe 2B 或 Parakeet TDT 0.6B v2。也可浏览这些架构的兼容本地模型目录。
- **时间轴对齐**：默认 Qwen3-ForcedAligner-0.6B；可浏览兼容的本地对齐模型目录。
- **字幕翻译**：选择 `models/` 中的 GGUF，或浏览其他路径的聊天型 GGUF 文件。模型需要支持中日英翻译、JSON 输出，并能由当前 llama.cpp 引擎加载；任意 GGUF 并不保证兼容。

| 识别模型 | 语言和用途 | 说明 |
| --- | --- | --- |
| Qwen3-ASR 1.7B / 0.6B | 中日英通用 | 支持自动检测语言和术语提示 |
| Cohere Transcribe 2B | 日语候选，也支持中英 | 必须手动指定语言；不支持术语提示 |
| Parakeet TDT 0.6B v2 ONNX | 英语快速识别 | 必须选择英语；不支持术语提示 |

三个引擎都通过所选 Qwen3-ForcedAligner 生成字幕起止时间，再使用所选 GGUF 翻译。Parakeet 原生 token 时间戳也保存在 `recognition.json`，目前字幕分句采用统一的强制对齐结果。长视频先用 VAD 切成不超过 24 秒的语音片段，识别引擎释放后再加载对齐和翻译模型。

![本地模型选择](docs/models.png)

窗口会显示路径与文件检查结果。内置模型缺失时，点击“下载缺失的内置模型”，完成后点击“保存选择”。首次安装默认下载 1.7B；0.6B 按需下载，权重约 1.9 GB。取消下载会保留可续传文件。本地外部模型无需映字专用的 `.complete` 标记。

Cohere 权重约 4.1 GB，Parakeet ONNX 文件约 2.5 GB。扩展环境另需 PyTorch CUDA 和其他依赖，安装全部模型建议再预留约 20 GB 空间。Cohere 官方仓库需要先在 [模型页](https://huggingface.co/CohereLabs/cohere-transcribe-03-2026) 接受访问条件，并在终端执行 `.\.venv\Scripts\hf.exe auth login`；令牌由 Hugging Face CLI 保存，不应写入工程、Git 或聊天。模型下载器使用该本机登录，并在跳转到其他下载域名时移除授权头。

选择保存在本机 `settings.json`，重启后保留。每个字幕工程也记录模型路径及生成选项，打开时恢复；如工程来自其他电脑，重新选择有效的本地路径即可。处理过程中不能切换模型；更换模型后用于下一次生成或“继续翻译”，已完成的译文保留。

## 文件与模型

每次处理创建独立 `output/视频名_日期时间_编号/` 文件夹，包含：

- `bilingual.srt`：日文或英文在上、中文在下，共用同一时间轴。
- `original.srt`、`translated.srt`：原文和中文版本。
- `bilingual.vtt`：WebVTT 双语字幕。
- `subtitles.yingzi.json`：可再次打开编辑的字幕工程。
- `recognition.json`：识别原始结果；`run.log`：运行日志。

编辑会自动保存字幕工程；已经导出的 SRT 不会自动更新，需要再次导出。翻译失败或取消时，已经保存的原文与字幕工程仍然保留。“继续翻译”只处理还没有译文的日语或英语字幕。

翻译逐条生成，使用前后原文作为语境参考，以减少跨字幕行移动内容；有文字的原文若只得到标点译文，会触发重试。模型输出仍需校对。

内置模型保存在本目录的 `models/`，推理只加载本地文件：

| 用途 | 模型 | 来源 |
| --- | --- | --- |
| 中日英语音识别 | Qwen3-ASR-1.7B / 0.6B | https://github.com/QwenLM/Qwen3-ASR |
| 中日英语音识别 | Cohere Transcribe 2B，Apache 2.0 | https://huggingface.co/CohereLabs/cohere-transcribe-03-2026 |
| 英语语音识别 | NVIDIA Parakeet TDT 0.6B v2，CC-BY-4.0；使用 istupakov 的 ONNX 转换 | https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx |
| 时间轴 | Qwen3-ForcedAligner-0.6B | https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B |
| 日中 / 英中翻译 | Qwen3-4B-Instruct-2507，Q4_K_M | https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF |

语音检测使用 Silero VAD；音轨提取使用 imageio-ffmpeg 自带的 FFmpeg；翻译由 llama.cpp 的 Windows CUDA 引擎运行。第三方项目遵循各自许可证。模型下载脚本会验证大文件 SHA-256，并记录仓库修订版本。

识别、对齐、翻译分阶段加载。自动模式优先使用 NVIDIA GPU，Qwen 支持显存不足时部分权重放入内存；Cohere 和 Parakeet 需容纳所选精度的权重及运行开销，也可以选择“CPU 兼容”。速度会受显存占用和视频时长影响。

## 安装与修复

应用使用本目录 `.venv` 中的 Python 3.12 环境。双击 `setup.cmd` 可以重新安装依赖并续传模型。安装脚本使用 `requirements-lock.txt` 固定已验证的依赖版本。需要 Python 3.12 或已安装的 uv；运行环境与模型初次安装需要联网，安装后视频识别和翻译不访问外部服务。

Cohere 和 Parakeet 使用独立的 `runtime/asr-env`，由 `requirements-extra.txt` 声明依赖，安装时使用 `requirements-extra-lock.txt` 固定已验证版本，避免升级主环境的 Transformers 4。新引擎在子进程运行，取消任务会终止整个任务进程树；子进程报错时不会把部分识别结果标记为完成。

```powershell
.\.venv\Scripts\python.exe tools\download_assets.py extra-runtime parakeet cohere
.\.venv\Scripts\python.exe tools\run_validation.py --languages english --asr-model models/parakeet-tdt-0.6b-v2-onnx
.\.venv\Scripts\python.exe tools\run_validation.py --languages japanese english --asr-model models/cohere-transcribe-03-2026
```

```powershell
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe -m pytest -q
```

需要运行真实模型流程检查时，执行下列命令；脚本会先下载下文列出的公开测试音频，再在 `output/` 生成验证字幕：

```powershell
.\.venv\Scripts\python.exe tools\run_validation.py
.\.venv\Scripts\python.exe tools\smoke_ui_flow.py
```

单独检查英语或 0.6B 模型：

```powershell
.\.venv\Scripts\python.exe tools\download_assets.py asr-small
.\.venv\Scripts\python.exe tools\run_validation.py --languages english --asr-model models/Qwen3-ASR-0.6B
.\.venv\Scripts\python.exe tools\smoke_ui_flow.py output/验证_英中双语_Qwen3-ASR-0.6B/subtitles.yingzi.json
```

翻译引擎仅监听随机端口的 `127.0.0.1`，使用每次启动随机生成的 API 密钥；任务结束后关闭。原始媒体不会被上传。

## 使用范围

当前按普通对白设计。背景音乐很重、多人同时说话、含糊发音、人名和专有名词仍可能识别错误；选择 Qwen 时，在“人名、地名、专业词汇”中填写提示可帮助识别。自动字幕与译文建议在导出前校对。

## 验证素材来源

- 日语：Kotoba-Whisper v2.2 模型仓库的 `sample_audio/sample_diarization_japanese.mp3`，默认取前 16 秒合成测试视频；Cohere 本机验证使用完整 26.63 秒样本。
- 中文：Qwen3-ASR 官方演示音频 `https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-ASR-Repo/asr_zh.wav`。
- 英语：Qwen3-ASR 官方演示音频 `https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-ASR-Repo/asr_en.wav`，取前 16 秒合成测试视频。

这些短片仅用于功能验证，不代表识别精度或速度基准。
