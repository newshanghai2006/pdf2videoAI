# PDF → AI 影片生成系统

将扫描版连环画 PDF 作为"剧本"，通过 AI 读懂文字与台词、理解剧情、重新生成彩色画面，最终合成带运镜和配音的影片。

---

## 工作原理

```
PDF 连环画
  │
  ├─ 1. 页面提取（PyMuPDF, 200 DPI）
  ├─ 2. OCR 文字识别（RapidOCR, 识别中文台词和旁白）
  ├─ 3. AI 剧情理解（GPT-4o, 拆分场景 + 生成旁白 + 画面提示词）
  ├─ 4. AI 画面生成（DALL·E 3, 全新彩色插画，非原图滤镜）
  ├─ 5. TTS 配音（edge-tts, 中文旁白）
  └─ 6. 影片合成（ffmpeg, Ken Burns 运镜 + 音频混合）
         │
         ▼
      MP4 影片
```

> **核心区别：** 不是把扫描页加滤镜做幻灯片，而是 AI 重新理解故事、重新创作画面。

---

## 环境要求

| 组件 | 要求 |
|------|------|
| 操作系统 | Windows / macOS / Linux 均可 |
| Python | 3.10+（建议用项目内隔离 venv） |
| ffmpeg | 需在系统 PATH 中可用（`ffmpeg -version` 能跑通即可） |
| 中文字体 | 可选，当前管线未做文字叠加；如需可自行安装系统中文字体 |
| 网络 | 需访问 OpenAI（或兼容代理）API；TTS 需访问微软服务 |

> 依赖里的 **PyMuPDF** 会被装进 venv，PDF 渲染默认在进程内完成，**无需任何额外的系统 Python**。
> （仅在极少数机器上原生 DLL 被安全软件极慢扫描时，才需要用到下方“疑难解答”里的可选后备方案。）

---

## 快速开始

核心原则：**用项目内的 venv 运行**，不要直接用系统全局 `python`，否则容易 `ModuleNotFoundError`。
项目会在自己的目录下创建 `.venv`，路径在任何机器上都一致。

### 方式一：双击/运行脚本（推荐）

**Windows：**
1. **首次使用** — 双击 `install.bat`（自动创建 `.venv` 并安装依赖）
2. **启动服务** — 双击 `run.bat`
3. 浏览器访问 **http://127.0.0.1:5000**
4. **停止服务** — 双击 `stop.bat`（或在终端窗口按 `Ctrl + C`）

**macOS / Linux：**
```bash
./install.sh   # 首次：创建 .venv 并安装依赖
./run.sh       # 启动服务
./stop.sh      # 停止服务
```

### 方式二：命令行（跨平台通用）

```bash
# 1) 进入项目目录
cd path/to/pdf2video

# 2) 创建并激活 venv
python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# Windows (CMD):         .venv\Scripts\activate.bat
# macOS / Linux:         source .venv/bin/activate

# 3) 安装依赖
pip install -r requirements.txt

# 4) 启动服务
python app.py
```

> PowerShell 提示：若不想激活 venv，直接用解释器路径启动时，命令要以调用运算符 `&` 开头，例如
> `& .venv\Scripts\python.exe app.py`（PowerShell 中以引号/路径开头的命令必须加 `&`）。

看到以下输出表示启动成功：

```
* Running on http://127.0.0.1:5000
```

浏览器访问 **http://127.0.0.1:5000**。

### 停止服务

推荐用停止脚本（只结束占用 5000 端口的服务，不会误伤其它程序）：
- **Windows**：双击 `stop.bat`
- **macOS / Linux**：`./stop.sh`

或在运行 `app.py` 的终端窗口按 `Ctrl + C`。

如果以上都不方便：
- **Windows**：`taskkill /F /IM python.exe`（会结束所有 python 进程，慎用）
- **macOS / Linux**：`pkill -f app.py`


---

## 使用指南

### 界面操作（四步向导）

#### 第一步：上传 PDF

- 拖拽 PDF 文件到上传区，或点击选择文件
- 支持 200MB 以内的 PDF 文件
- 上传后系统自动检测总页数

#### 第二步：配置参数

| 参数 | 说明 |
|------|------|
| **LLM API Key / Base URL** | 必填。用于剧情分析，支持 OpenAI、Kimi 等 OpenAI Chat Completions 兼容服务 |
| **图像 API Key / Base URL** | 可选。用于画面生成，可与 LLM 使用不同厂商；留空时沿用 LLM 配置 |
| **启用 AI 画面生成** | 可选。关闭时跳过图像接口，直接使用 PDF 原页面制作视频 |
| **美化为彩色图片** | 可选。将每页黑白连环画提交给支持图片编辑的图像模型设色，尽量保留原线稿和构图 |
| **LLM 模型** | 剧情理解用的模型。**默认 `gpt-5.5`**，可从常用模型下拉选择，也可编辑输入框填写任意模型名 |
| **图像模型** | 画面生成用的模型。**默认 `gpt-image-1`**，可从常用模型下拉选择，也可编辑输入框填写任意模型名 |
| **连接测试** | 点此按钮可快速验证 Key 与两个模型是否可用，**无需跑完整流程**，是排查"模型名不对"报错的最快方式 |
| **页码范围** | 选择要处理的 PDF 页范围（建议先选 3-5 页测试） |
| **AI 剧情分析** | 可选。关闭后按手动分段参数和 OCR 文本制作视频，不需要 LLM Key |
| **每段视频包含页数** | 手动模式下将 N 页 PDF 合并为一个视频段，N 可自定义 |
| **多页排列方式** | 自动、横排或竖排；自动模式下横屏使用横排，竖屏使用竖排 |
| **OCR 文字语言** | 简体/中文、繁体中文或英文 |
| **画面方向** | 横屏（1920×1080 / 1280×720）或 竖屏（1080×1920 / 720×1280） |
| **画质** | 1080p 或 720p |
| **视频引擎** | 可选“静态全画面”（完整显示、不运动、不裁剪）、默认 `Ken Burns` 缓动运镜，或 Seedance 占位引擎 |
| **艺术风格** | 见下方风格列表 |
| **生成AI视频提示词** | 可选开关。勾选后额外产出一份**中英双语视频提示词**（含镜头运动/时长/负向词），可复制粘贴到火山 Seedance / 即梦 / 可灵 / Runway 等**网页版**工具手动生成视频——不走 API、零额外费用。完成后在第四步「下载AI视频提示词」 |
| **TTS 配音** | 开关 + **旁白语音** + **对白语音**（两者用不同声音）+ 语速 |
| **按 TTS 时长自动延长片段** | 勾选后片段至少与有效 TTS 音频一样长；例如设置 6 秒、配音 20 秒时自动使用 20 秒 |
| **背景音乐** | 可选上传 BGM 文件 + 调整音量 |
| **片头封面** | 可选 AI 生成无文字封面或上传图片；作为第一段视频插入，默认时长 3 秒，可自定义 |

> **重要**：LLM 和图像模型都可手动输入任意模型名。如果你的代理（API Base URL）不是 OpenAI 官方，请用代理支持的模型名。例如图像模型若代理不支持 `dall-e-3`，可改用 `gpt-image-1` 或代理文档里的名称。点「连接测试」即可确认。

### OpenAI 兼容网关配置

界面的“服务商预设”选择 NVIDIA 后，会自动填入 NVIDIA LLM 地址，并切换到 NVIDIA
模型下拉菜单：

```text
LLM Base URL: https://integrate.api.nvidia.com/v1
LLM API Key:  nvapi-...
```

NVIDIA 文本模型通过 OpenAI 兼容 Chat Completions 使用。NVIDIA 图像模型使用独立的
NIM 图像端点适配；当前预设包含 FLUX.2 klein 4B 和 Stable Diffusion XL。免费额度、模型可用性和请求参数
以 NVIDIA API Catalog 当前页面为准。

NVIDIA 免费额度按 40 RPM 限制处理。程序在进程内对 NVIDIA LLM、连接测试、Flux/SDXL
生图和参数重试使用同一个限速器，每次 NVIDIA 请求至少间隔约 1.55 秒，以避免超过
40 RPM。多个同时运行的生成任务也共享此限制；服务重启后限速状态重新计时。

NVIDIA Flux 当前按官方示例使用 `1024x1024`，因为部分 NIM 端点只接受该固定尺寸；
最终视频仍会按所选输出分辨率编码，并由视频引擎适配横屏或竖屏画面。

如果 NVIDIA 返回 `finishReason: CONTENT_FILTERED`，表示场景提示词触发内容安全过滤，并非
Key 或连接失败。程序会先把提示词改写为非血腥、无明确伤亡的全年龄历史叙事画面并重试
一次；安全提示词仍被过滤时，才使用该场景对应的 PDF 原页面继续生成视频。
AI 片头封面触发过滤时会自动跳过封面并继续正文视频，不会导致整个任务失败。
AI 封面默认明确禁止生成文字、字母、Logo 和字幕，以避免图像模型产生乱码；如果需要准确
标题，建议上传已经排版好的封面图片。后续可再增加程序使用真实字体后置叠字功能。

选择 NVIDIA 预设后，页面会自动启用“AI 画面生成”并选择 FLUX.2 klein 4B。图像 Key
和图像 Base URL 可以留空，后端会沿用 LLM 的 `nvapi-...` Key，并自动请求 NVIDIA 的
专用图像端点。完整链路为：OCR → NVIDIA/其他 LLM 生成场景提示词 → NVIDIA Flux 生图
→ TTS → 静态或 Ken Burns 视频合成。

程序支持服务商提供的自定义 API 根路径，例如：

```text
API Key:  sk_sample
Base URL: https://sample.com/openapi
LLM 模型: gpt-5.5
```

OpenAI、Kimi 等提供 OpenAI 兼容 `/chat/completions` 的服务可以直接使用。Claude
原生 Anthropic Messages API、Claude Code 本身不是该协议；需要使用其 OpenAI 兼容网关，
或后续单独实现 Anthropic 客户端。

Anthropic Key 能否用于图像生成取决于网关是否额外提供图像服务。Claude 原生模型并不
提供 `/images/generations` 图片生成接口，因此不能仅把 Claude 模型名加入图像模型列表
就生成图片。项目中的模型名称可以手动输入，名称必须是网关实际支持的模型名。

程序会请求 `https://sample.com/openapi/chat/completions`。Base URL 不会自动补上
`/v1`，请严格填写服务商文档给出的 API 根地址，不要填写完整的
`/chat/completions` 地址。

LLM 请求的兼容策略：

- `gpt-5.x` 和 `o1/o3/o4` 系列使用 `max_completion_tokens`，且不强制发送 `temperature`。
- `gpt-4.x`、`gpt-4o` 等模型使用 `max_tokens`。
- 使用流式响应，减少长篇剧情分析被代理网关判定为超时的概率。
- 连接超时为 15 秒，响应读取超时为 300 秒；SDK 最多自动重试一次。
- 只有网关明确返回“参数不支持”时才切换兼容参数，连接失败和网关 5xx 不会被误判后重复提交完整请求。

LLM 接口与图像接口分别测试。LLM 测试成功只表示 `/chat/completions` 可用；
完整影片生成还需要该服务支持 `/images/generations`，并提供可用的图像模型。

LLM 和图像服务可以分别填写。例如 LLM 使用 Kimi 兼容网关、图像使用另一家服务：

```text
LLM Base URL:   https://kimi-provider.example/v1
LLM 模型:       kimi-k2
图像 Base URL:  https://image-provider.example/v1
图像模型:      gpt-image-1
```

图像服务必须实现 `/images/generations`；只有聊天接口的服务不能用于 AI 画面生成。
没有图像服务 Key 时关闭“启用 AI 画面生成”即可继续流程：LLM 负责剧情分析，视频使用
PDF 原页面，并可继续进行 TTS 配音和 Ken Burns 合成。连接测试在关闭该选项时也会跳过图像接口。

勾选“美化为彩色图片”后，程序调用图像模型的 `images.edit` 接口逐页设色；该功能不是
普通文本 LLM 能完成的，必须使用支持图片编辑的图像模型和网关。它与“启用 AI 画面生成”
互斥，二者同时勾选时界面会自动关闭另一个选项。

### Ollama 和本地模型

Ollama 可以作为 LLM 使用，前提是启用其 OpenAI 兼容接口并填写类似：

```text
LLM API Key:  ollama
LLM Base URL: http://127.0.0.1:11434/v1
LLM 模型:     qwen2.5:14b / llama3.2 / 其他本地模型
```

本地 LLM 需要能根据提示返回结构化 JSON 场景结果；小模型可能无法稳定遵守格式。
Ollama 本身通常不提供本项目所需的 `/images/generations` 或文生视频接口，因此
图像生成应关闭，或另行配置支持该接口的本地图像服务。视频引擎 `Ken Burns` 是本地
可用的；Seedance 当前仍是接入占位，尚不能直接调用本地或云端视频模型。

> **配音说明**：旁白与角色对白使用**不同声音**，更像广播剧。角色台词里的「角色名：」前缀不会被读出来（只朗读台词本身），角色名仅用于区分声音。

#### 第三步：生成影片

点击"开始生成"后，界面实时显示 6 个阶段的进度：

| 阶段 | 进度区间 | 说明 |
|------|---------|------|
| 提取PDF | 2% - 10% | PDF 每页转为图片 |
| OCR识别 | 12% - 25% | 识别中文台词和旁白 |
| AI分析 | 28% - 35% | LLM（默认 gpt-5.5）理解剧情，拆分场景 |
| AI生成 | 38% - 73% | 图像模型（默认 gpt-image-1）生成每个场景的彩色画面 |
| TTS配音 | 75% - 85% | 旁白/对白双声音生成音频 |
| 影片合成 | 87% - 100% | 视频引擎出片（默认 Ken Burns 运镜）+ 逐场景音画对齐 |

每个场景的 AI 生成画面会实时显示在预览区。

AI 理解或 AI 生图失败时，任务会暂停并显示“无 AI 继续”和“退出任务”：继续会复用已完成
的 PDF/OCR 结果；理解失败时按手动规则构造场景，生图失败时未完成场景使用 PDF 原页面。
等待选择最长为 1 小时，超过后任务自动失败。

如果 NVIDIA 图像提示词触发内容过滤，页面会显示原提示词文本框。用户可以修改后点击
“修改后重新提交”，或提交空内容取消本次 AI 生图并使用对应 PDF 原页面。
提示词框在轮询任务状态时不会覆盖用户正在编辑的内容。

处理超过 8 页时，AI 剧情分析会自动按每批 8 页执行，再合并标题、角色和场景并重新编号。
这可以避免 100 页等大文档一次返回过长 JSON，导致 `JSONDecodeError: Unterminated string`。

#### 第四步：预览与下载

- 生成完成后，页面内嵌播放器可在线预览影片
- 点击"下载影片"按钮保存 MP4 文件到本地
- 点击“下载字幕 SRT”保存与场景音画时间轴对应的字幕文件；包含旁白和对白

---

## 艺术风格

| 风格 ID | 中文名 | 描述 |
|---------|--------|------|
| `chinese_ink` | 水墨彩绘 | 中国传统水墨彩绘风格，色彩鲜明，东方美学 |
| `cinematic` | 电影写实 | 电影级写实风格，光影戏剧化，好莱坞历史大片感 |
| `anime` | 动漫 | 精致动漫风格，色彩饱和度高，人物造型优美 |
| `oil_painting` | 油画 | 西方油画风格，厚重笔触，光影丰富，古典艺术感 |
| `illustration` | 插画 | 精美插画风，色彩温暖，细节丰富，适合故事叙述 |
| `comic` | 美漫 | 美漫风格，线条硬朗，色彩浓烈，具有冲击力 |
| `gongbi` | 工笔重彩 | 中国工笔重彩画风格，精细工整，色彩艳丽 |

---

## TTS 语音选项

| 语音 ID | 名称 | 特点 |
|---------|------|------|
| `zh-CN-YunxiNeural` | 云希 | 男·沉稳（默认） |
| `zh-CN-YunjianNeural` | 云健 | 男·浑厚 |
| `zh-CN-YunyangNeural` | 云扬 | 男·专业 |
| `zh-CN-XiaoxiaoNeural` | 晓晓 | 女·温柔 |
| `zh-CN-XiaoyiNeural` | 晓伊 | 女·活泼 |
| `zh-CN-XiaohanNeural` | 晓涵 | 女·大气 |

> TTS 依赖微软在线服务，网络不通时自动降级为无声影片。

---

## 环境变量

可通过环境变量预配置 API Key，避免每次在界面输入：

```bash
# Windows (PowerShell)
$env:OPENAI_API_KEY = "sk-..."
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"  # 可选，默认值

# Windows (CMD)
set OPENAI_API_KEY=sk-...
set OPENAI_BASE_URL=https://api.openai.com/v1
```

设置后重启服务生效。界面中填写的 Key 优先级高于环境变量。

---

## 项目结构

```
pdf2video/
├── app.py                      # Flask 后端（路由 + 管线编排）
├── config.py                   # 全局配置（分辨率/风格/语音/API）
├── pdf_helper.py               # PDF 渲染的可选子进程后备（外部 Python + PyMuPDF）
├── requirements.txt            # Python 依赖
├── install.bat / install.sh    # 一键创建 .venv 并装依赖（Windows / *nix）
├── run.bat / run.sh            # 一键启动服务（Windows / *nix）
├── stop.bat / stop.sh          # 一键停止服务（按 5000 端口精确结束）
├── README.md                   # 本文件
│
├── core/                       # 核心处理模块
│   ├── __init__.py
│   ├── pdf_processor.py        # PDF → 页面图片（默认进程内 PyMuPDF，必要时降级到 pdf_helper.py）
│   ├── ocr_processor.py        # OCR 文字识别（RapidOCR）
│   ├── story_analyzer.py       # AI 剧情理解（OpenAI Chat Completions 兼容接口）
│   ├── image_generator.py      # AI 画面生成（OpenAI Images 兼容接口）
│   ├── tts_engine.py           # TTS 配音（旁白/对白双声音，去角色名前缀）
│   ├── subtitle_builder.py     # 按场景实际时长生成 SRT 字幕
│   ├── video_prompt.py         # 生成可粘贴到网页版AI视频工具的中英双语提示词（本地拼装）
│   ├── video_builder.py        # 影片合成：逐场景音画对齐 + 拼接 + BGM
│   └── video_engines/          # 视频引擎（可扩展）
│       ├── __init__.py         #   引擎工厂 get_engine()
│       ├── base.py             #   引擎接口 VideoEngine.generate_clip
│       ├── static.py           #   静态全画面（完整显示、不运动）
│       ├── kenburns.py         #   Ken Burns 运镜（默认）
│       └── seedance.py         #   火山 Seedance 占位（接入扩展点）
│
├── templates/
│   └── index.html              # Web 界面
│
├── static/
│   ├── css/style.css           # 样式
│   └── js/app.js               # 前端交互逻辑
│
├── uploads/                    # 上传的 PDF（运行时自动创建）
└── outputs/                    # 生成的影片和中间文件（运行时自动创建）
    └── <task_id>/
        ├── pages/              # 提取的 PDF 页面图片
        ├── scenes/             # AI 生成的场景画面
        ├── audio/              # TTS 音频
        └── final_film.mp4      # 最终影片
```

### 代码库文件分类

以下文件和目录是项目运行所需的源程序、前端资源或依赖定义，应该提交到代码库：

```text
app.py
config.py
pdf_helper.py
requirements.txt
core/
templates/
static/
```

以下文件不是核心源程序，但用于安装、启动、停止和说明项目，建议提交到代码库：

```text
.env.example
.gitignore
README.md
install.bat / install.sh
run.bat / run.sh
stop.bat / stop.sh
```

以下目录和文件由安装或运行过程生成，不应提交到代码库：

| 路径 | 内容 | 处理方式 |
|------|------|----------|
| `.venv/` | 项目 Python 虚拟环境和已安装的第三方库 | 不入库；执行安装脚本重建 |
| `.env` | 本机 API Key、Base URL 等私密配置 | 不入库；从 `.env.example` 创建 |
| `user_settings.json` | 网页保存的本机 API Key、Base URL 和模型设置 | 不入库；仅适合个人本机使用 |
| `__pycache__/`、`*.pyc` | Python 字节码缓存 | 不入库；Python 自动生成 |
| `uploads/` | 用户上传的 PDF 和 BGM | 不入库；应用启动时自动创建 |
| `outputs/` | 页面图片、AI 场景图、音频和最终影片 | 不入库；任务运行时生成 |
| `test_pages/` | PDF/OCR 测试过程生成的页面 | 不入库 |
| `server.log`、`*.log` | 服务运行日志 | 不入库 |
| `.vscode/`、`.idea/` | 本机编辑器配置 | 默认不入库 |

根目录的 `10辕门射戟.pdf` 是约 30 MB 的人工测试样例，不参与程序启动，也不是依赖库。
正式代码库通常不提交该文件；如需保留演示数据，建议使用独立的样例下载地址、Git LFS，
或放入专门的 `samples/` 目录并明确维护策略。

第三方 Python 库安装在 `.venv/` 中，不需要复制进代码库；代码库只保留
`requirements.txt`，其他机器通过 `install.bat` 或 `install.sh` 重建运行环境。

### 上传 GitHub 前检查

`.gitignore` 已默认排除本机密钥、虚拟环境、上传文件、生成结果、日志、缓存和测试素材。
推送前建议执行：

```bash
git status --short --ignored
```

正常情况下，GitHub 提交中只应包含前面“应该提交到代码库”和“建议提交”的文件。以下内容
绝对不要上传：

```text
.env
user_settings.json
任何真实 API Key、Cookie 或访问令牌
.venv/
uploads/、outputs/、test_pages/
*.log、server.log、__pycache__/、*.pyc
本地 PDF、BGM、生成的 MP4/PNG/MP3/SRT
```

仓库根目录的 `.gitignore` 已包含上述规则，并额外忽略 Python 测试缓存、覆盖率文件、
临时文件和操作系统元数据；`.env.example` 会被明确保留用于说明配置格式。

如果密钥曾经被提交过，仅删除本地文件和新增 `.gitignore` 还不够，密钥仍存在于 Git 历史中；
应立即在服务商后台撤销并重新生成，再根据需要清理 Git 历史。

### 页面选择

配置页面范围时可以输入单页、逗号分隔页面和连续范围的组合，例如：

```text
1,3,5-10,12-20
```

页面会自动去重并按页码顺序处理，且必须在 PDF 实际页数范围内。

关闭“AI 剧情分析”后，可设置每段包含的 PDF 页数、统一时长、逐段时长以及每段伴读文字。
选择 1-40 页时，AI 模式下 LLM 仍会按剧情自行拆分场景，场景数量不保证等于 40；如需严格
一页一个片段，请关闭“启用 AI 剧情分析”并将“每段视频包含页数”设为 1。
伴读文字留空时直接使用 OCR 文本；也可以在页面导入 SRT，程序会提取字幕文本作为逐段伴读内容。
OCR 不在配置页单独预览。开始生成后只执行一次 OCR，随后在 TTS 前的场景确认界面中显示
每个场景的图片和 OCR 伴读文字，用户可逐场景修改；修改后的文字会用于 TTS 和 SRT。

### 本地保存配置

页面中的 LLM、图像和视频服务 Key、Base URL、模型名会在点击“连接测试”或“开始生成”
时保存到项目根目录的 `user_settings.json`，下次打开页面自动填充。该文件是明文本地配置，
已加入 `.gitignore`，不应提交到代码库；共享电脑或公网部署时请关闭此功能或删除该文件。

---

## API 接口

任务状态保存在当前服务进程内存中。服务重启后，正在运行或已完成任务的内存状态会丢失；
浏览器检测到任务 404 时会自动停止轮询，并提示返回配置页面重新生成。`outputs/` 中已生成的
文件不会因重启而删除，但旧任务不能再通过原 task_id 下载。

影片合成阶段若 FFmpeg 失败，错误信息会提取时间戳、输入流、编码和封装相关的关键行，
而不是只显示最后的编码统计；多段拼接会自动重新生成时间戳并统一为 CFR，降低音频拼接
时的封装失败概率。

如果 TTS 网络请求中断，可能留下存在但损坏的 AAC 文件。合成前程序会使用 `ffprobe`
验证音频流，并让 FFmpeg 完整解码验证；无法解码的音频会自动替换为等长静音轨，不会阻止视频继续生成。
TTS 现在按场景独立重试 3 次；单个场景失败不会让后续场景全部跳过，最终会显示成功和失败的场景数量。
启用 TTS 时，生成配音前会暂停显示场景卡片；每个场景图片下方都有独立的伴读文本框，
可逐场景编辑后提交。如果 60 秒内没有操作，系统会自动使用当前文本继续生成配音；后端
保留 75 秒等待余量，避免自动提交和服务端超时竞态。
用户点击或开始编辑任意场景文本后，倒计时会停止，需要手动点击确认。此时页面显示的是
“请确认伴读文字”，不是错误状态；任务处于等待确认状态。
手动模式会按实际提取页数校验分段数量，例如 1-40 页、每段 2 页必须生成 20 个视频段。
单个场景视频时长上限为 60 秒；如果 OCR 文本过多或 TTS 异常导致音频超过该长度，合成时会截取到 60 秒，避免生成数百秒片段导致 FFmpeg 超时。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web 界面 |
| GET | `/api/config` | 获取可用配置（风格/语音/分辨率） |
| POST | `/api/upload` | 上传 PDF 文件 |
| POST | `/api/process` | 启动影片生成管线 |
| GET | `/api/progress/<task_id>` | 轮询任务进度 |
| GET | `/api/scene_image/<task_id>/<scene_idx>` | 获取场景 AI 图片 |
| GET | `/api/download/<task_id>` | 下载影片 |
| GET | `/api/download_prompts/<task_id>` | 下载 AI 视频提示词（.txt，需开启该功能） |
| GET | `/api/download_subtitles/<task_id>` | 下载影片字幕（SRT） |
| GET | `/api/preview/<task_id>` | 在线预览影片 |
| POST | `/api/upload_bgm` | 上传背景音乐 |

### 启动生成示例

先上传拿到文件路径（`/api/upload` 返回的 `path` 字段），再用该路径启动生成：

```bash
# 1) 上传 PDF，返回 { "path": "...uploads/xxxx_10辕门射戟.pdf", "page_count": ... }
curl -X POST http://127.0.0.1:5000/api/upload \
  -F "file=@10辕门射戟.pdf"

# 2) 用上一步返回的 path 启动生成
curl -X POST http://127.0.0.1:5000/api/process \
  -H "Content-Type: application/json" \
  -d '{
    "pdf_path": "D:/.../pdf2video/uploads/xxxx_10辕门射戟.pdf",
    "api_key": "sk-...",
    "art_style": "cinematic",
    "resolution": "1080p_land",
    "start_page": 1,
    "end_page": 5,
    "use_tts": true,
    "tts_voice": "zh-CN-YunxiNeural"
  }'
```

返回 `task_id` 后轮询进度：

```bash
curl http://127.0.0.1:5000/api/progress/<task_id>
```

---

## 常见问题

### Q: 没有 OpenAI API Key 能用吗？

不能完成完整管线。API Key 是必须的——AI 剧情分析和画面生成都依赖它。没有 Key 只能跑到 OCR 阶段。

### Q: TTS 配音失败怎么办？

TTS 依赖微软在线服务。如果网络不通，系统会自动降级为无声影片，不影响影片生成。可关闭 TTS 开关跳过此步骤。

### Q: OCR 识别准确率不高？

- 确保使用 200 DPI 或更高分辨率提取页面
- 扫描质量差的页面识别率会下降
- 系统已针对中文优化（RapidOCR + 中文模型）

### Q: 出现 `ONNXRuntime inferece failed`？

这是本地 RapidOCR/ONNXRuntime 推理时的内存或运行时错误。程序已限制 OpenBLAS/ONNX
线程数，将 OCR 输入缩放到最大 1200px，并在失败时自动使用 800px 图片重试。修改后需要
完全停止并重新启动服务，确保线程环境变量在 ONNXRuntime 导入前生效。

### Q: 生成速度慢？

AI 画面生成是主要耗时环节（每个场景约 10-30 秒）。建议：
- 先用 3-5 页测试流程
- 确认效果满意后再处理更多页面
- 720p 比 1080p 生成更快

### Q: 如何更换 API 代理地址？

在界面的"API Base URL"字段填写代理地址，或设置环境变量 `OPENAI_BASE_URL`。

### Q: 报错 "images endpoint requires an image model" 或图像模型调用失败？

这是因为你的代理/API 不认识默认的 `dall-e-3` 或 `gpt-image-1` 名称。解决步骤：
1. 在界面填好 Key、Base URL 后，点 **「连接测试」** 按钮，它会分别验证 LLM 和图像模型是否可用，并给出明确✅/❌。
2. 若图像模型❌，请在「图像模型」框改成你的代理实际支持的模型名（常见：`gpt-image-1`、`dall-e-3`、`dall-e-2`、`flux-1`）。可直接手动输入任意名称。
3. 也可设置环境变量 `IMAGE_MODEL` 和 `LLM_MODEL` 固化为默认值。

### Q: 报错 "max_tokens is not supported" 或类似 token 参数错误？

这是 GPT-5 系列与旧模型参数差异导致的。系统会为 GPT-5 系列直接使用
`max_completion_tokens`，为 GPT-4/4o 使用 `max_tokens`；只有兼容网关明确拒绝参数时
才切换参数名。若仍报错，请确认模型名和 Base URL 正确，或点「连接测试」排查。

### Q: 影片没有声音？

检查以下几点：
1. TTS 开关是否打开
2. 网络是否能访问微软 TTS 服务
3. 是否上传了背景音乐
4. 系统音量/播放器音量

### Q: 上传 PDF 后卡住不动 / PDF 渲染极慢？（可选后备方案）

极少数机器上，原生 PDF 库的 DLL 会被安全软件以极慢速度扫描，导致首次渲染卡死。
本项目默认在进程内用 venv 里的 PyMuPDF 渲染，通常不受影响。若你确实遇到此问题，
可指定另一个「加载正常」的 Python 解释器（需已安装 PyMuPDF）作为渲染后备：

```bash
# Windows (PowerShell)
$env:PDF_HELPER_PYTHON = "C:/path/to/other/python.exe"

# macOS / Linux
export PDF_HELPER_PYTHON=/path/to/other/python3
```

设置后重启服务，渲染会自动改为通过 `pdf_helper.py` 子进程调用该解释器完成。
不设置则一律走进程内渲染（推荐）。

---

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Flask 3.x |
| PDF 处理 | PyMuPDF (fitz) |
| OCR | rapidocr-onnxruntime |
| AI 剧情理解 | OpenAI GPT-4o |
| AI 画面生成 | OpenAI DALL·E 3 |
| TTS 配音 | edge-tts（旁白/对白双声音） |
| 图片处理 | Pillow + OpenCV |
| 视频合成 | ffmpeg（可插拔引擎：Ken Burns 默认 / 火山 Seedance 预留）+ 逐场景音画对齐 |
| 前端 | HTML + CSS + JavaScript（原生，无框架） |

---

## 开发说明

### 修改艺术风格

编辑 `config.py` 中的 `ART_STYLES` 字典，添加或修改风格描述。

### 修改 TTS 语音

编辑 `config.py` 中的 `TTS_VOICES` 字典。完整语音列表见 [edge-tts 文档](https://github.com/rany2/edge-tts)。

### 修改视频参数

编辑 `config.py`：
- `FPS`：帧率（默认 25）
- `DEFAULT_DURATION`：每场景默认时长（秒）
- `RESOLUTIONS`：添加自定义分辨率

### 调整 AI 提示词

编辑 `core/story_analyzer.py` 中的系统提示词，调整 AI 对剧情理解和画面提示词生成的行为。

### 新增视频引擎（如接入火山 Seedance / 即梦等真实视频模型）

视频「画面动态」通过可插拔引擎实现，位于 `core/video_engines/`：

- 每个引擎实现 `VideoEngine.generate_clip(scene, out_path, width, height, duration, ...)`，
  职责是把一个场景渲染成一段**时长恰为 `duration` 秒的无声视频**；配音对齐、拼接、
  BGM 混音由 `video_builder` 统一处理，引擎不必关心。
- 内置 `kenburns`（默认）与 `seedance`（占位）。

接入火山 Seedance 的步骤：
1. 在 `core/video_engines/seedance.py` 的 `_submit_task` / `_poll_task` 里实现火山的
   「提交任务 → 轮询 → 取视频 URL」，再下载并用 ffmpeg 规整到目标尺寸/时长。
2. 前端「视频引擎」选 Seedance 后会出现 **API Key** 输入框（也可用环境变量
   `SEEDANCE_API_KEY` / `SEEDANCE_BASE_URL` / `SEEDANCE_MODEL`）。
3. 在 `config.py` 的 `VIDEO_ENGINES` 里登记新引擎名即可出现在下拉中。

> 未接入真实调用前，选择 Seedance 会**自动降级为 Ken Burns**，保证流程不中断。

### 关于音画同步

`video_builder` 采用「逐场景对齐」：每个场景先生成一段画面长度**等于该场景配音长度**的
片段，再贴上配音，最后整体拼接。因此单场景音画严丝合缝，拼接后也不会累积漂移。
