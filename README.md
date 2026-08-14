# PDF → AI 影片生成系统

[中文](README.md) | [English](README_EN.md)

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
| Python | 3.10+（OCR 引擎按已安装库选择；Python 3.14 可使用 EasyOCR，RapidOCR 需匹配可用的 ONNXRuntime 版本） |
| ffmpeg | 需在系统 PATH 中可用（`ffmpeg -version` 能跑通即可） |
| 中文字体 | 可选，当前管线未做文字叠加；如需可自行安装系统中文字体 |
| 网络 | 需访问 OpenAI（或兼容代理）API；TTS 需访问微软服务；EasyOCR 首次运行需下载模型 |

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

### 邮箱验证码登录

网页现在默认要求使用图形验证码、邮箱和 6 位邮箱验证码登录。图形验证码与邮箱验证码有效期
默认都是 10 分钟，同一邮箱发送冷却 60 秒；登录会话默认有效 12 小时。发送邮件时图形验证码
只校验、不消耗，完成登录时才消耗，因此发送邮件后继续填写同一张图即可。验证码刷新接口按
IP 限流，答案和邮箱验证码都只以 SHA-256 哈希保存在 SQLite。会话 Cookie 使用 `HttpOnly`、
`SameSite=Strict`，所有业务写请求还必须携带登录后取得的 CSRF Token。
图形验证码渲染器直接移植 `Security_center/src/captcha.js` 的 5×7 点阵字库、交叉干扰线、
噪点、交替倾斜和易混淆字符排除规则，输出 180×56 BMP，不依赖系统字体。

本地首次调试可以保留 `.env` 中的：

```text
AUTH_PREVIEW_CODES=true
```

SMTP 未配置时，登录页会直接显示本地预览验证码。公网部署必须改为
`AUTH_PREVIEW_CODES=false`，并配置真实邮件服务器：

```text
PUBLIC_BASE_URL=https://film.example.com
PORT=5000
TRUST_PROXY_HOPS=1
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_SECURE=true
SMTP_STARTTLS=true
SMTP_USER=noreply@example.com
SMTP_PASS=your-smtp-password
SMTP_FROM=AI Film Studio <noreply@example.com>
```

若邮件服务器使用 587 端口，通常设置 `SMTP_SECURE=false`、`SMTP_STARTTLS=true`。公网的
`PUBLIC_BASE_URL` 必须使用 `https://`，这样会话 Cookie 才会自动带上 `Secure` 属性。
只有应用端口被防火墙限制为仅供可信反向代理访问时，才可把 `TRUST_PROXY_HOPS` 设为实际
代理层数（单层 Nginx 通常为 1）；应用端口直接暴露公网时必须保持 0，防止伪造客户端 IP。

关闭预览并启用真实邮箱发送的完整步骤：

1. 在项目根目录创建或编辑 `.env`，设置 `AUTH_PREVIEW_CODES=false`。
2. 填写 `SMTP_HOST`、`SMTP_PORT`、`SMTP_SECURE`、`SMTP_STARTTLS`、`SMTP_USER`、
   `SMTP_PASS` 和 `SMTP_FROM`。
3. 执行 `stop.bat` 后再执行 `run.bat`，环境变量只在服务启动时读取。
4. 打开无痕窗口，用实际收件邮箱测试。正常响应不会再在网页或接口中返回预览验证码。

邮箱服务商通常要求在 `SMTP_PASS` 中填写“SMTP 授权码/应用专用密码”，不是网页登录密码。
不要把真实授权码提交到 Git，也不要发到聊天记录；只写入已被 `.gitignore` 排除的 `.env`。
可以提供邮箱服务商名称、SMTP 主机、端口、SSL/STARTTLS 方式、发件地址和显示名称，由此确定
配置格式；授权码应由你在本机自行填入。


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
| **Agnes 图片尺寸** | Agnes 生图/美化专用的 1K、2K、3K、4K 档位；默认 1K，档位越高免费 RPM 越低 |
| **连接测试** | 点此按钮可快速验证 Key 与两个模型是否可用，**无需跑完整流程**，是排查"模型名不对"报错的最快方式 |
| **页码范围** | 选择要处理的 PDF 页范围（建议先选 3-5 页测试） |
| **AI 剧情分析** | 可选。关闭后按手动分段参数和 OCR 文本制作视频，不需要 LLM Key |
| **每段视频包含页数** | 手动模式下将 N 页 PDF 合并为一个视频段，N 可自定义 |
| **多页排列方式** | 自动、横排或竖排；自动模式下横屏使用横排，竖屏使用竖排 |
| **PDF 第一页作为无旁白封面** | 默认开启；第一页保留原图但不交给 LLM 分析、不生成 TTS，正文从第二页开始绑定 |
| **OCR 文字语言** | 简体/中文、繁体中文或英文 |
| **画面方向** | 横屏（1920×1080 / 1280×720）或 竖屏（1080×1920 / 720×1280） |
| **画质** | 1080p 或 720p |
| **视频引擎** | 可选“静态全画面”、默认 `Ken Burns`、Seedance 占位引擎，或已接入的 `Agnes Video V2.0` 异步视频引擎 |
| **艺术风格** | 见下方风格列表 |
| **生成AI视频提示词** | 可选开关。勾选后额外产出一份**中英双语视频提示词**（含镜头运动/时长/负向词），可复制粘贴到火山 Seedance / 即梦 / 可灵 / Runway 等**网页版**工具手动生成视频——不走 API、零额外费用。完成后在第四步「下载AI视频提示词」 |
| **TTS 配音** | 开关 + **旁白语音** + **对白语音**（两者用不同声音）+ 语速 |
| **按 TTS 时长自动延长片段** | 勾选后片段至少使用手动时长；若有效 TTS 更长则自动延长，例如设置 6 秒、配音 20 秒时使用 20 秒 |
| **背景音乐** | 可选上传 BGM 文件 + 调整音量 |
| **片头封面** | 可选 AI 生成无文字封面或上传图片；作为第一段视频插入，默认严格 3 秒，可自定义，不受 TTS 自动延长影响 |

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
Key 或连接失败。程序会先把提示词改写为非血腥、无明确伤亡且保持原作年代的全年龄叙事画面并重试
一次；安全提示词仍被过滤时，才使用该场景对应的 PDF 原页面继续生成视频。
AI 片头封面触发过滤时会自动跳过封面并继续正文视频，不会导致整个任务失败。
AI 封面默认明确禁止生成文字、字母、Logo 和字幕，以避免图像模型产生乱码；如果需要准确
标题，建议上传已经排版好的封面图片。后续可再增加程序使用真实字体后置叠字功能。

选择 NVIDIA 预设后，页面会自动启用“AI 画面生成”并选择 FLUX.2 klein 4B。图像 Key
和图像 Base URL 可以留空，后端会沿用 LLM 的 `nvapi-...` Key，并自动请求 NVIDIA 的
专用图像端点。完整链路为：OCR → NVIDIA/其他 LLM 生成场景提示词 → NVIDIA Flux 生图
→ TTS → 静态或 Ken Burns 视频合成。

### Agnes AI 免费模型配置

界面的“服务商预设”选择 **Agnes AI** 后，会自动填入三个模型和统一 API 根地址：

```text
LLM Base URL:   https://apihub.agnes-ai.com/v1
LLM 模型:       agnes-2.0-flash
图像 Base URL:  https://apihub.agnes-ai.com/v1
图像模型:       agnes-image-2.0-flash
视频 Base URL:  https://apihub.agnes-ai.com/v1
视频模型:       agnes-video-v2.0
```

同一个 Agnes Key 可用于文本、图片和视频。图像 Key、视频 Key 留空时会复用 LLM Key；
是否使用 AI 图片和 Agnes 视频仍由“启用 AI 画面生成”和“视频引擎”两个选项独立控制。

程序按 Agnes 官方 2026-07-27 公开文档中的免费/default Key **实际 RPM** 在进程内限速：

| 能力 | 免费实际限制 | 程序处理 |
|------|-------------|----------|
| `agnes-2.0-flash` 文本 | 20 RPM | 剧情分析、连接测试和重试共享限速器；429 等待一分钟后只重试一次 |
| 图片 1K | 20 RPM | 每次生图、图生图和测试均限速 |
| 图片 2K | 10 RPM | 与 1K 使用独立档位限速器 |
| 图片 3K / 4K | 1 RPM | 每次请求至少间隔约一分钟，批量处理会明显变慢 |
| `agnes-video-v2.0` 视频 | 1 RPM | 任务提交和每次状态轮询都经过同一限速器 |

相同类型的多个免费 Key 在 Agnes 账户侧共享限制池，创建更多 Key 不会增加 RPM。程序的
限速器只能协调当前 Flask 进程；若同一 Key 还被其他程序使用，服务端仍可能返回 429。

Agnes Image 使用官方 `POST /v1/images/generations`。文生图发送 `size` 档位和 `ratio`，
图生图/“美化为彩色图片”把本地页面编码为 Data URI，放入
`extra_body.image`；`response_format` 按官方要求放在 `extra_body` 内，不放在顶层。
横屏使用 16:9，竖屏使用 9:16。常用实际输出如下：

| 档位 | 16:9 | 9:16 |
|------|------|------|
| 1K | 1312×736 | 736×1312 |
| 2K | 2624×1472 | 1472×2624 |
| 3K | 3936×2208 | 2208×3936 |
| 4K | 5248×2944 | 2944×5248 |

服务端可能把不受支持的精确像素尺寸标准化到最近的档位。最终影片仍由 FFmpeg 缩放到界面
选择的 720p/1080p。长 PDF 建议先使用 1K；3K/4K 不但约一分钟一张，也会显著增加内存、
网络传输和临时文件体积。

Agnes Video 使用异步流程：`POST /v1/videos` 创建任务，再通过
`GET /agnesapi?video_id=...` 获取状态，完成后下载 `metadata.url`。程序会：

- 根据场景目标时长计算 `num_frames`，自动满足 `8n+1` 且不超过 441 帧。
- 帧率可选 24/30 fps；24 fps 时单次模型视频最长约 18.4 秒。
- 生成分辨率可选 480p、720p、1080p，服务端仍可能映射到最接近的标准尺寸。
- 下载后统一转为项目尺寸、25 fps、H.264/yuv420p，并精确裁剪或延长到场景/TTS 时长。
- 超过模型单次时长时保留生成视频，并以最后一帧延长剩余画面，避免截断旁白。

免费视频为 1 RPM，而且提交与轮询都计入程序限速。一个场景通常至少等待一次约一分钟的
轮询；20 个场景可能需要几十分钟，页面显示 queued/in_progress/progress 时请勿重启服务。
连接测试不会创建视频任务，只检查 Key、URL 和模型配置格式，实际鉴权在首次生成时完成。

Agnes 图生视频要求图片是**公网可访问 URL**。本项目的 PDF 页面和 AI 图片默认是本地文件，
不会上传到公网，因此 Agnes 引擎当前使用场景 `image_prompt` 做文生视频；只有场景已有
`image_url`/`source_image_url` 公网地址时才自动走图生视频。这样不会隐式泄露用户 PDF。

#### 中国题材年代与文化提示词优化

为避免图像或视频模型弄错故事年代、人物国籍或服装装备，程序在三层统一处理：

1. 剧情分析要求 LLM 先识别准确年份/时期、国家、地域和人物国籍，再生成符合该年代的服饰、
   军服、装备、车辆、发式和建筑；不再假定所有中国连环画都是古代故事。
2. 调用 OpenAI、NVIDIA 或 Agnes 图片模型前，程序再次追加固定的
   `CULTURAL AND PERIOD ACCURACY` 约束。即使某个 LLM 输出过于简略，生图请求仍会要求保持年代。
3. Agnes 视频和导出的网页版视频提示词加入负向约束，排除错误国籍、错误军服、虚构朝代和
   时代混搭。近现代场景额外排除古装、盔甲、刀剑和宫殿。

对越自卫反击战等题材会明确写入 `1979 Sino-Vietnamese border war`、
`Chinese People's Liberation Army`、20 世纪军服与装备，并保留越南人员的真实国籍；不得把
双方生成成古代武士或欧美士兵。AI 封面也会使用当前故事标题、摘要和前三个场景提示词，
不再套用固定的“中国历史漫画”封面模板。程序会从整篇 OCR 识别该战争背景，并传给每一个
剧情分析批次和每一个最终生图提示词，避免长篇 PDF 后面的批次因缺少标题上下文又变成古装。

默认电影、动漫、油画和漫画风格也已移除“好莱坞”“日本动画”“美漫”等容易造成文化偏移
的描述。约束保留一个必要例外：如果原作明确写了外国人物或外国场景，应按原作身份生成，
不会强行把该人物改成中国人。固定规则位于 `core/prompt_optimizer.py`，可按具体作品调整。

适配依据：

- <https://agnes-ai.com/zh-Hans/docs/agnes-20-flash>
- <https://agnes-ai.com/zh-Hans/docs/agnes-image-20-flash>
- <https://agnes-ai.com/zh-Hans/docs/agnes-video-v20>
- <https://wiki.agnes-ai.com/zh-Hans/docs/tokenplan>

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

### SenseNova 调用额度与重试

常用 SenseNova 文本模型已经加入 LLM 模型下拉框。程序按当前公开的 5 小时调用额度做
保守的均匀发送，避免批量 PDF 分析在短时间内集中提交：

| Model ID | 公开额度 | 程序最小请求间隔 |
|----------|----------|------------------|
| `sensenova-6.7-flash-lite` | 每 5 小时 1500 次 | 约 12.2 秒 |
| `sensenova-u1-fast` | 每 5 小时 1500 次 | 约 12.2 秒 |
| `deepseek-v4-flash`（SenseNova） | 每 5 小时 500 次 | 约 36.2 秒 |

`sensenova-*` 模型名会自动启用限速。`deepseek-v4-flash` 只有当 Base URL 包含
`sensenova` 或 `sensecore` 时才启用，避免误限制同名的本地模型。连接测试、剧情分析批次、
自动重试和用户点击重试共享同一个进程内限速器。多个 Flask 进程或其他程序使用同一 Key
时无法互相协调，服务端额度仍是最终依据。

收到 HTTP 429/503 时，程序优先使用服务端 `Retry-After`；没有该响应头时按上表间隔等待，
然后自动重试一次。单次自动等待最多 300 秒，避免额度窗口已经耗尽时后台无提示地挂起数小时。
仍失败时页面会显示 **“重试当前步骤”**、**“无 AI 继续”** 和 **“退出任务”**：

- “重试当前步骤”保留 PDF、OCR 和已经生成成功的图片，再按限速器等待后重新请求。
- “无 AI 继续”降级到 OCR 手动场景或 PDF 原页面，不再调用当前失败的 AI 步骤。
- “退出任务”终止当前任务。

`JSONDecodeError: Unterminated string` 表示 LLM 返回的场景 JSON 被截断或格式不完整，不等同于
429。SenseNova 模型会把每个剧情分析批次从默认 8 页降低为 4 页，以减少长 JSON 被截断；
任一批次仍发生此错误时，程序会自动把该批次二分并重试，直至缩小到单页。单页响应仍不完整时
还会自动重试一次；只有再次失败才暂停任务并显示“重试当前步骤”和“无 AI 继续”。已经完成的
PDF 提取、OCR 和其他分析批次不会丢失。该恢复机制同样适用于不满一个完整批次的短文档。

`sensenova-u1-fast` 也已加入图像模型下拉框。该模型的 `/images/generations` 接口不接受
OpenAI 常用的 `1024x1024`：程序在横屏影片中自动发送 `2752x1536`，竖屏影片中自动发送
`1536x2752`，连接测试使用横屏规格。“美化为彩色图片”也会按原页面方向选择对应尺寸，
但仍要求服务商同时实现 `images.edit`；只支持文生图时应使用“启用 AI 画面生成”。
SenseNova U1 Fast 的文本分析、连接测试、生图和图片编辑共用同一个约 12.2 秒限速器。

`IncompleteRead`、`RemoteProtocolError` 或“peer closed connection”表示大尺寸图片响应已开始返回，
但 SenseNova/中间代理在完整数据到达前关闭了连接，并不表示模型名不支持。场景生图会在继续遵守
12.2 秒限速的前提下采用递增等待，最多自动尝试 5 次；URL 图片下载会优先重试同一个地址，避免
重复消耗一次生图额度。图片先写入临时文件并校验完整后才替换正式文件，失败不会留下半张图片。
仍然失败时任务会暂停并允许“重试当前步骤”或“无 AI 继续”，已成功场景会直接复用。

SenseNova 图片接口返回 `message: sensitive image`、`invalid_request_error`、`code: 18` 时，
表示当前场景提示词触发内容过滤，不是模型名称、Key 或固定分辨率错误。程序会把它识别为
“AI 生图提示词被过滤”，显示当前提示词供用户修改后重新提交。当前场景再次被过滤时会使用
对应 PDF 原页面继续，已经成功生成的其他场景不会重做，也不会终止整个影片任务。

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

勾选“美化为彩色图片”后，普通 OpenAI 兼容模型调用 `images.edit`，Agnes Image 则通过
`/images/generations` 的 `extra_body.image` 进行图生图设色；该功能不是
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
某个批次仍返回残缺 JSON 时会自动按 8→4→2→1 页缩小范围重试；SenseNova 初始批次为 4 页，
按 4→2→1 页重试。输出提示还会限制标题、摘要和画面提示词长度，减少模型无意义扩写造成的截断。
如果分批结果在场景 8 和场景 9 之间漏掉某个 PDF 页，程序会根据原始 OCR 页码检测并自动
补齐该页的独立场景。AI 剧情分析模式严格保持一页一个场景，不会把缺失页横向或纵向合并
到相邻场景；多页合并只由手动模式的“每段视频包含页数”控制。
勾选“PDF 第一页作为无旁白封面”后，即使 OCR 在封面识别到书名，该页也不会交给 LLM；
程序直接创建独立封面场景并保留原 PDF 图片，AI 从第 2 页开始分析和绑定，避免后续整体错位。
每个批次返回后还会校正 `page_source/page_sources`，防止模型在新批次中重新从第 1 页编号，
从源头避免场景画面与伴读文字错位。

#### 第四步：预览与下载

- 生成完成后，页面内嵌播放器可在线预览影片
- 点击"下载影片"按钮保存 MP4 文件到本地
- 点击“下载字幕 SRT”保存与场景音画时间轴对应的字幕文件；包含旁白和对白

---

## 艺术风格

| 风格 ID | 中文名 | 描述 |
|---------|--------|------|
| `chinese_ink` | 水墨彩绘 | 中国传统水墨彩绘风格，色彩鲜明，东方美学 |
| `cinematic` | 电影写实 | 中国题材电影级写实风格，人物服饰、装备与环境符合原作年代 |
| `anime` | 动漫 | 精致动漫风格，色彩饱和度高，人物造型优美 |
| `oil_painting` | 油画 | 油画质感，厚重笔触和丰富光影，人物与场景保持原作年代 |
| `illustration` | 插画 | 精美插画风，色彩温暖，细节丰富，适合故事叙述 |
| `comic` | 连环画 | 中国连环画彩色漫画风格，线条有力，保留人物身份与准确年代 |
| `gongbi` | 工笔重彩 | 中国工笔重彩画风格，精细工整，色彩艳丽 |

---

## TTS 语音选项

### OCR 引擎选择

界面可在 `RapidOCR（ONNX）` 和 `EasyOCR` 之间选择。RapidOCR 已列入默认依赖；EasyOCR 为可选依赖，
需要在项目虚拟环境中单独安装：

```powershell
python -m pip --python .venv\Scripts\python.exe install easyocr
```

OCR 引擎只在 OCR 阶段按选择加载，不会在 Flask 启动时同时导入所有引擎。实际识别在独立子进程中
执行：RapidOCR/ONNXRuntime 初始化超过 120 秒、单页超过 90 秒无进展或总 OCR 时间超过 1800 秒时，
程序会终止该子进程并返回明确错误，不会让网页永久停在 1%。可通过环境变量
`OCR_STARTUP_TIMEOUT_SECONDS`、`OCR_PAGE_TIMEOUT_SECONDS` 和 `OCR_TOTAL_TIMEOUT_SECONDS` 调整这些
上限。EasyOCR 首次使用可能下载模型，RapidOCR 则要求 `rapidocr_onnxruntime` 与当前 Python/ONNXRuntime
版本匹配；若 RapidOCR 初始化超时，请在页面切换到 EasyOCR，或重新安装匹配的 RapidOCR/ONNXRuntime。

Windows 的 `install.bat` 使用 `venv --without-pip` 创建项目环境，再通过系统 pip 的 `--python`
选项把依赖安装到 `.venv`。这是为了绕过部分 Python 3.14 + Windows Python Manager 环境中
`ensurepip` 长时间无响应的问题；该 `.venv` 不要求自身先安装 pip，`run.bat` 可直接启动应用。
安装依赖阶段会显示 pip 下载/解析信息，并设置有限的网络超时和重试次数；如果网络不可达会明确失败，
不会继续无限停留在虚拟环境创建提示。

| 语音 ID | 名称 | 特点 |
|---------|------|------|
| `zh-CN-YunxiNeural` | 云希 | 男·沉稳（默认） |
| `zh-CN-YunjianNeural` | 云健 | 男·浑厚 |
| `zh-CN-YunyangNeural` | 云扬 | 男·专业 |
| `zh-CN-XiaoxiaoNeural` | 晓晓 | 女·温柔 |
| `zh-CN-XiaoyiNeural` | 晓伊 | 女·活泼 |
| `zh-CN-XiaohanNeural` | 晓涵 | 女·大气 |

> `edge-tts` 默认需要访问微软在线语音服务 `speech.platform.bing.com`。Windows 上如果在线
> 服务不可达，程序会自动尝试系统已安装的中文 SAPI 语音（例如 Microsoft Huihui Desktop）；
> 若系统没有中文语音，则才会降级为无声影片。Linux/macOS 仍需在线服务或自行接入本地 TTS。

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

Agnes 可使用：

```bash
$env:OPENAI_API_KEY = "your-agnes-api-key"
$env:OPENAI_BASE_URL = "https://apihub.agnes-ai.com/v1"
$env:LLM_MODEL = "agnes-2.0-flash"
$env:IMAGE_MODEL = "agnes-image-2.0-flash"
$env:AGNES_API_KEY = "your-agnes-api-key"
$env:AGNES_VIDEO_MODEL = "agnes-video-v2.0"
```

设置后重启服务生效。界面中填写的 Key 优先级高于环境变量。

公网部署请保持 `ENABLE_SERVER_SETTINGS=false`（默认值）。只有个人电脑上的单用户实例需要
兼容旧版客户端的 `/api/settings` 接口时，才可设置 `ENABLE_SERVER_SETTINGS=true`；该兼容
接口会在服务器磁盘明文保存 Key，不适合共享服务。新版网页始终使用下述浏览器存储方式。

邮箱认证与会话配置见 `.env.example`。公网至少应设置：

```text
AUTH_REQUIRED=true
AUTH_PREVIEW_CODES=false
PUBLIC_BASE_URL=https://film.example.com
SESSION_TTL_HOURS=12
EMAIL_CODE_TTL_MINUTES=10
CAPTCHA_TTL_MINUTES=10
AUTH_CODE_COOLDOWN_SECONDS=60
```

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
├── README_EN.md                # English documentation
│
├── core/                       # 核心处理模块
│   ├── __init__.py
│   ├── pdf_processor.py        # PDF → 页面图片（默认进程内 PyMuPDF，必要时降级到 pdf_helper.py）
│   ├── auth_service.py         # 邮箱格式、验证码和 SMTP 邮件发送
│   ├── captcha_service.py      # Security_center 同款点阵字形、干扰线图形验证码
│   ├── persistence.py          # SQLite 用户、会话、验证码、任务和检查点持久化
│   ├── ocr_processor.py        # OCR 文字识别（RapidOCR）
│   ├── ocr_worker.py           # 可终止的 OCR 子进程：防止 ONNXRuntime 初始化永久阻塞任务
│   ├── story_analyzer.py       # AI 剧情理解（OpenAI Chat Completions 兼容接口）
│   ├── image_generator.py      # OpenAI/NVIDIA/Agnes 生图与 Agnes 图生图
│   ├── prompt_optimizer.py     # 中国题材人物、年代、服饰/军服与建筑文化准确性约束
│   ├── rate_limiter.py         # NVIDIA / Agnes 进程内 RPM 限速器
│   ├── tts_engine.py           # TTS 配音（旁白/对白双声音，去角色名前缀）
│   ├── subtitle_builder.py     # 按场景实际时长生成 SRT 字幕
│   ├── video_prompt.py         # 生成可粘贴到网页版AI视频工具的中英双语提示词（本地拼装）
│   ├── video_builder.py        # 影片合成：逐场景音画对齐 + 拼接 + BGM
│   └── video_engines/          # 视频引擎（可扩展）
│       ├── __init__.py         #   引擎工厂 get_engine()
│       ├── base.py             #   引擎接口 VideoEngine.generate_clip
│       ├── static.py           #   静态全画面（完整显示、不运动）
│       ├── kenburns.py         #   Ken Burns 运镜（默认）
│       ├── seedance.py         #   火山 Seedance 占位（接入扩展点）
│       └── agnes.py            #   Agnes Video V2.0 异步任务、轮询、下载和规整
│
├── templates/
│   └── index.html              # Web 界面
│
├── static/
│   ├── favicon.svg             # 浏览器标签页图标（绿色圆底、白色 V）
│   ├── css/style.css           # 样式
│   └── js/app.js               # 前端交互逻辑
│
├── data/                       # SQLite 数据库（运行时自动创建，不入 Git）
│   └── app.db                  # 用户、会话、验证码哈希和任务元数据
├── uploads/                    # 按用户 ID 隔离的上传文件（运行时自动创建）
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
README_EN.md
install.bat / install.sh
run.bat / run.sh
stop.bat / stop.sh
```

以下目录和文件由安装或运行过程生成，不应提交到代码库：

| 路径 | 内容 | 处理方式 |
|------|------|----------|
| `.venv/` | 项目 Python 虚拟环境和已安装的第三方库 | 不入库；执行安装脚本重建 |
| `.env` | 本机 API Key、Base URL 等私密配置 | 不入库；从 `.env.example` 创建 |
| `user_settings.json` | 旧版服务器端 API 配置（默认已停用） | 不入库；仅在本地单用户兼容模式下生成 |
| `__pycache__/`、`*.pyc` | Python 字节码缓存 | 不入库；Python 自动生成 |
| `uploads/` | 用户上传的 PDF 和 BGM | 不入库；应用启动时自动创建 |
| `outputs/` | 页面图片、AI 场景图、音频和最终影片 | 不入库；任务运行时生成 |
| `data/` | SQLite 登录、会话和任务数据库 | 不入库；应用启动时自动创建并持续备份 |
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
OCR 会保留原始 PDF 页码；因此使用 `1,3,5-10` 等非连续选择时，后续场景仍会对应
第 1、3、5 页，而不会被错误地重新编号为第 1、2、3 页。

关闭“AI 剧情分析”后，可设置每段包含的 PDF 页数、统一时长、逐段时长以及每段伴读文字。
选择 1-40 页时，AI 模式下 LLM 仍会按剧情自行拆分场景，场景数量不保证等于 40；如需严格
一页一个片段，请关闭“启用 AI 剧情分析”并将“每段视频包含页数”设为 1。
伴读文字留空时直接使用 OCR 文本；也可以在页面导入 SRT，程序会提取字幕文本作为逐段伴读内容。
OCR 不在配置页单独预览。开始生成后只执行一次 OCR，随后在 TTS 前的场景确认界面中显示
每个场景的图片和 OCR 伴读文字，用户可逐场景修改；修改后的文字会用于 TTS 和 SRT。

### 浏览器保存 API 配置

页面中的 LLM、图像和视频服务 Key、Base URL、模型名会在点击“连接测试”或“开始生成”时，
按用户选择保存在浏览器中。默认不再向服务器 `/api/settings` 写入 `user_settings.json`：

| 模式 | 保存位置和生命周期 | 适用场景 |
|------|------------------|----------|
| 仅当前标签页 | `sessionStorage`，关闭标签页后清除 | 公网默认和推荐模式 |
| 保存在此浏览器 | `localStorage`，直到用户清除站点数据 | 个人设备上需要跨会话自动填充 |
| 不保存 | 不写入浏览器存储 | 共享设备或最高保守级别 |

页面提供“清除浏览器保存配置”按钮，会同时删除两种存储中的 AI 配置并清空当前页面的 API Key。
也可以在浏览器的站点数据设置中清除该站点的数据。

`sessionStorage` 和 `localStorage` 都不是操作系统密钥保险库，内容可被同源 JavaScript 读取；
后者还是明文长期保存。公网必须使用 HTTPS、只加载可信前端脚本，并持续防范 XSS。程序调用
第三方模型时仍必须把用户 Key 发送到本服务器，再由服务器转发给模型服务，因此浏览器存储
只解决“服务器配置文件持久化”问题，并不能让服务器在处理期间看不到 Key。

如需让旧版客户端或已有本地工具继续调用设置接口，可在 `.env` 中设置
`ENABLE_SERVER_SETTINGS=true` 后重启服务。此时 `/api/settings` 才允许读写
`user_settings.json`，但新版网页仍使用浏览器存储。公网部署必须保持为 `false`；默认禁用时
GET 只返回存储功能已禁用的状态，POST 返回 403，不会泄露旧文件中可能残留的 Key。

程序已经提供邮箱验证码身份认证，并按用户隔离上传、任务、图片、视频和字幕访问权限。公网
正式运营仍应在反向代理层配置有效 TLS、可信代理头、请求和任务配额、上传大小限制、数据库
备份及过期文件清理策略；浏览器保存方式不能替代这些服务器安全措施。

---

## API 接口

任务元数据和检查点保存在 `data/app.db`，页面、OCR、剧情、画面、TTS 和最终影片保存在
`outputs/<task_id>/`。关闭浏览器不会停止后台任务；重新登录同一邮箱后，“我的任务”会显示
运行中、暂停、失败和已完成的任务。服务进程重启时，原来处于等待或运行状态的任务会自动
标记为“已暂停”，可从最近完成的检查点继续，不会从头重复已经落盘的页面、OCR、场景图和
TTS 文件。

“我的任务”弹窗顶部可直接进入“新任务”。已完成、失败或已暂停且没有运行线程的任务可点击
垃圾桶图标删除；删除会移除该任务的数据库记录和 `outputs/<task_id>/` 下的生成文件。上传的
源 PDF 只有在没有其他任务引用时才会一并清理，避免删除一个任务影响同一用户的其他任务。

“暂停任务”是协作式暂停：程序会先完成当前单页 OCR、单场景模型请求、单段 TTS 或当前
FFmpeg 原子操作，再写入检查点并暂停，不会强杀正在写文件的进程。因此在耗时 API 或 FFmpeg
调用中点击暂停后，状态可能短暂显示“正在暂停”。继续任务时，浏览器会重新提交当前页面中的
LLM、图像和视频 Key；这些 Key 只在当前工作线程内使用，不会写入 `data/app.db` 的任务配置。
若浏览器未保存 Key，应先回到配置页重新填写，再点击“继续”。

影片合成阶段若 FFmpeg 失败，错误信息会同时保留时间戳、输入流、编码和封装相关的关键行
以及日志末尾，而不是只显示最后的编码统计；多段拼接会自动重新生成时间戳并统一为 CFR，
降低音频拼接时的封装失败概率。对于超过 8 个场景的长 PDF，程序会自动分批拼接视频和
音轨，再合并批次，不会在一条 FFmpeg 命令中同时打开全部场景文件。拼接阶段还会限制每个
H.264 输入的解码线程、滤镜线程和输出编码线程，避免小内存服务器因 FFmpeg 默认按 CPU 核数
为多路视频分配缓冲而出现 `h264 ... get_buffer failed`。这些处理不会改变场景顺序、设定时长
或无 TTS 时生成的静音轨。

如果 TTS 网络请求中断，可能留下存在但损坏的 AAC 文件。合成前程序会使用 `ffprobe`
验证音频流，并让 FFmpeg 完整解码验证；无法解码的音频会自动替换为等长静音轨，不会阻止视频继续生成。
TTS 现在按场景独立重试 3 次；单个场景失败不会让后续场景全部跳过，最终会显示成功和失败的场景数量。
启用 TTS 时，生成配音前会暂停显示场景卡片；每个场景图片下方都有独立的伴读文本框，
可逐场景编辑后提交。如果 60 秒内没有操作，系统会自动使用当前文本继续生成配音；后端
保留 180 秒兜底等待：即使浏览器进入后台、网络中断或 JavaScript 定时器被系统节流，
后端超时后也会自动采用原有场景文字继续，不会再抛出“TTS 伴读文本确认失败且等待用户
决定超时”并终止任务。
片头封面固定为无旁白，确认页中的封面文本框不可编辑。正文文本按明确的场景索引提交，
即使旧浏览器提交时丢失空白项，也不会再把第一页旁白写入封面并导致后续整体错位。
确认页会在每个正文场景标题中显示对应的 PDF 页码，便于提交配音前直接核对映射。
如果选择了封面但封面生成或上传失败，任务状态会明确提示“未插入封面”；此时第一项
“场景 1（PDF 第 1 页）”是正文第一页，不是封面。
用户点击或开始编辑任意场景文本后，倒计时会停止，需要手动点击确认。此时页面显示的是
“请确认伴读文字”，不是错误状态；前端不会在下一次状态轮询时重新启动 60 秒倒计时。
为防止浏览器关闭后任务永久挂起，编辑状态仍受后端 180 秒兜底限制；超过该时间未提交时，
系统使用编辑前已保存的场景文字自动继续。
OCR 伴读文字进入 TTS 前会直接去除换行符，不插入额外空格，避免 OCR 排版换行造成停顿；
场景边界仍由场景数组独立维护，不会因换行被误认为新场景。
手动模式会按实际提取页数校验分段数量，例如 1-40 页、每段 2 页必须生成 20 个视频段。
单个场景视频时长上限为 90 秒；如果 OCR 文本过多或 TTS 异常导致音频超过该长度，合成时会截取到 90 秒，避免生成数百秒片段导致 FFmpeg 超时。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web 界面 |
| GET | `/api/auth/session` | 获取当前登录状态和 CSRF Token |
| GET | `/api/captcha` | 创建图形验证码挑战并返回 BMP Data URL |
| POST | `/api/auth/send-code` | 发送邮箱验证码 |
| POST | `/api/auth/verify` | 验证邮箱验证码并建立会话 |
| POST | `/api/auth/logout` | 退出登录 |
| GET | `/api/tasks` | 当前用户的任务列表 |
| POST | `/api/tasks/<task_id>/pause` | 协作式暂停任务 |
| POST | `/api/tasks/<task_id>/resume` | 携带当前浏览器 Key 从检查点继续 |
| DELETE | `/api/tasks/<task_id>` | 删除当前用户已结束或已暂停的任务及其生成文件 |
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

所有业务接口都要求登录，POST 还要求 `X-CSRF-Token`。日常使用应直接操作网页。脚本调用时，
先通过 `/api/auth/verify` 保存会话 Cookie，并从响应 JSON 取得 `csrf_token`；之后上传和启动
请求都同时带 Cookie 与该请求头。下面仅展示已登录后的请求结构：

```bash
# 1) 上传 PDF，返回 { "path": "...uploads/xxxx_10辕门射戟.pdf", "page_count": ... }
curl -X POST http://127.0.0.1:5000/api/upload \
  -b cookies.txt -H "X-CSRF-Token: <csrf_token>" \
  -F "file=@10辕门射戟.pdf"

# 2) 用上一步返回的 path 启动生成
curl -X POST http://127.0.0.1:5000/api/process \
  -b cookies.txt -H "X-CSRF-Token: <csrf_token>" \
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
curl -b cookies.txt http://127.0.0.1:5000/api/progress/<task_id>
```

---

## 常见问题

### Q: 没有 OpenAI API Key 能用吗？

可以。关闭“AI 剧情分析”“AI 画面生成”和 AI 封面后，可使用 PDF 原页、OCR、手动分段、
TTS 和本地 FFmpeg 完成影片。只有启用对应的文本、图像或视频 AI 功能时才需要该服务的 Key。

### Q: TTS 配音失败怎么办？

TTS 首先使用 `edge-tts` 访问微软在线语音服务；Windows 网络不可用时会自动回退到本机中文
SAPI 语音。若日志仍显示“本地中文 TTS 回退也不可用”，请在 Windows 设置中安装中文语音包，
或恢复对 `speech.platform.bing.com` 的访问。所有方案都不可用时才会生成无声影片。

### Q: OCR 识别准确率不高？

- 确保使用 200 DPI 或更高分辨率提取页面
- 扫描质量差的页面识别率会下降
- 系统已针对中文优化（RapidOCR + 中文模型）

### Q: 任务一直显示 0%，或出现 `ONNXRuntime inferece failed`？

如果任务目录尚未创建、FFmpeg 进程也不存在，通常卡在 OCR 引擎加载，而不是视频合成。
程序支持在界面选择 `RapidOCR` 或 `EasyOCR`，并且只在真正需要 OCR 时加载所选引擎；Python 3.14
可以使用 EasyOCR，但 RapidOCR 是否可用取决于本机 `rapidocr_onnxruntime` 与 ONNXRuntime 的匹配版本。
OCR 已在独立子进程中设置初始化、单页和总时长上限；超过上限会显示引擎和具体页码，而不是永久显示 1%。
如果某个引擎加载无响应，可返回配置页切换另一个引擎；也可以在 Python 3.10/3.11 的独立 venv 中安装对应依赖。
OCR 推理阶段会限制 OpenBLAS/ONNX 线程数，将输入缩放到最大 1200px，并在失败时使用 800px 图片重试。

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
- 内置 `static`、`kenburns`（默认）、`seedance`（占位）和已可用的 `agnes`。

`agnes` 的真实调用实现在 `core/video_engines/agnes.py`：负责提交、按免费 1 RPM 轮询、
下载和 FFmpeg 规整。它遵守与其他引擎相同的“输出精确 duration 秒无声片段”契约，所以上层
TTS 对齐、字幕、拼接和 BGM 混音不需要 Agnes 专用分支。

接入火山 Seedance 的步骤：
1. 在 `core/video_engines/seedance.py` 的 `_submit_task` / `_poll_task` 里实现火山的
   「提交任务 → 轮询 → 取视频 URL」，再下载并用 ffmpeg 规整到目标尺寸/时长。
2. 前端「视频引擎」选 Seedance 后会出现 **API Key** 输入框（也可用环境变量
   `SEEDANCE_API_KEY` / `SEEDANCE_BASE_URL` / `SEEDANCE_MODEL`）。
3. 在 `config.py` 的 `VIDEO_ENGINES` 里登记新引擎名即可出现在下拉中。

> 未接入真实调用前，选择 Seedance 会**自动降级为 Ken Burns**，保证流程不中断。

### 关于音画同步

`video_builder` 采用「逐场景对齐」：每个场景先生成一段画面长度**等于该场景配音长度**的
片段，再按同一场景顺序拼接音频和画面。片头封面没有旁白时，会使用与封面时长完全相同的
静音轨，确保正文旁白从封面结束的位置开始。

#### 音画同步实现说明

每个场景会统一规整为 25 fps；音频全部解码为 44100 Hz 双声道后，先将所有场景（包括封面静音）拼成一条连续音轨，最后只编码一次 AAC。这样不会把每段 MP3/AAC 的编码延迟累积到后续场景，也不会出现启用 AI 封面后开头不同步、随后逐渐追上的现象。修改代码后请先重启 Flask 服务再测试。
