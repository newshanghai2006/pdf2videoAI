# PDF to AI Video Generator

[中文](README.md) | [English](README_EN.md)

Create an MP4 video from a scanned comic or illustrated PDF. The application renders PDF pages, recognizes dialogue and narration with OCR, optionally uses AI services to analyze the story and generate images, creates narration and subtitles, then builds the final video with FFmpeg.

## Workflow

```text
PDF -> PyMuPDF page rendering -> OCR -> optional LLM story analysis
    -> optional image generation or colorization -> optional TTS
    -> FFmpeg video + SRT subtitles
```

When no AI key is available, disable AI story analysis, AI image generation, AI colorization, and AI cover generation. The application can still create videos from original PDF pages with manual scene grouping, OCR text, TTS, subtitles, and FFmpeg.

## Requirements

| Component | Requirement |
|---|---|
| OS | Windows, macOS, or Linux |
| Python | Python 3.10 or newer |
| FFmpeg | Available on `PATH`; test with `ffmpeg -version` |
| OCR | RapidOCR is installed by default; EasyOCR is optional |
| Network | Needed only for remote AI services and online Edge TTS |

PyMuPDF is installed in the project virtual environment. The default PDF renderer runs in-process and does not need a separately configured system Python installation.

## Quick Start

Always run the project with its own `.venv`, not an unrelated global Python installation.

### Windows

1. Run `install.bat` once.
2. Run `run.bat`.
3. Open `http://127.0.0.1:5000`.
4. Run `stop.bat` to stop the service.

### macOS or Linux

```bash
./install.sh
./run.sh
./stop.sh
```

### Manual Setup

```bash
cd path/to/pdf2video
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

In PowerShell, use the call operator when starting with an explicit path:

```powershell
& .venv\Scripts\python.exe app.py
```

## Login and Public Deployment

The web application uses a graphic CAPTCHA and email verification code login. Sessions use `HttpOnly` and `SameSite=Strict` cookies; state-changing requests require a CSRF token.

For public deployment:

- Set `AUTH_REQUIRED=true`.
- Configure real SMTP settings in `.env`.
- Set `AUTH_PREVIEW_CODES=false`.
- Serve the application through HTTPS and a reverse proxy.
- Set `PUBLIC_BASE_URL` to the public HTTPS URL.
- Keep `ENABLE_SERVER_SETTINGS=false`; user API keys are retained in browser storage instead of server-side settings files.
- Set `TRUST_PROXY_HOPS` only for a trusted proxy.

Never commit `.env`, API keys, user uploads, generated videos, or the task database.

## AI Service Configuration

Text services use the OpenAI Chat Completions style. The app supports OpenAI-compatible providers, local OpenAI-compatible servers, NVIDIA, Agnes AI, SenseNova, and compatible gateways when their real base URL and model names are provided.

Text, image, and video services have separate key, base URL, and model fields:

| Service | Required capability | Used for |
|---|---|---|
| Text LLM | Chat completions | Story analysis and image prompts |
| Image model | Text-to-image; image editing for colorization | Scene images, AI cover, colorization |
| Video model | Video-generation API | Optional dynamic video engine |

Example of an OpenAI-compatible text service:

```text
API key:  sk_sample
Base URL: https://sample.com/openapi
Model:    gpt-5.5
```

Use the URL documented by the provider. Standard OpenAI-compatible services commonly use a `/v1` API root. The connection test checks only enabled services; a text-only endpoint cannot act as an image endpoint.

### NVIDIA and Agnes

The NVIDIA preset uses NVIDIA's provider-specific image endpoint and applies request rate limiting. Select an available model such as `black-forest-labs/flux.2-klein-4b`. If an image is content-filtered, the UI displays the source prompt so it can be edited and submitted again.

The Agnes preset fills text, image, and video defaults. Use 1K and a short page range for an initial test; higher image resolutions and video generation use more time and have lower free-tier RPM limits.

## Main Options

| Option | Behavior |
|---|---|
| Page selection | Supports `1,3,5-10,12-20`; original PDF page numbers are retained |
| AI story analysis | LLM creates narration and image prompts per source page |
| Manual mode | Define pages per segment, duration, layout, and narration yourself |
| Pages per segment | Combine a configurable number of PDF pages in manual mode |
| Layout | Auto, horizontal, or vertical arrangement for combined pages |
| First page as cover | First selected PDF page is silent and excluded from story analysis/TTS |
| OCR language | Simplified Chinese, Traditional Chinese, or English |
| OCR engine | RapidOCR (ONNX) or EasyOCR |
| TTS | Scene narration; video duration can extend to actual TTS duration |
| Video engine | Static full frame, Ken Burns, or configured external engines |
| AI cover | Generate a cover or upload one; its duration is configurable |
| Captions | Import SRT text or download generated SRT after completion |

The TTS confirmation screen contains one editable narration field per scene. OCR line breaks are removed before TTS so printed layout does not create artificial pauses.

## OCR Reliability

OCR starts only at the OCR stage and runs in an isolated child process. A native ONNXRuntime problem therefore cannot freeze the Flask task thread.

| Limit | Default |
|---|---|
| OCR engine initialization | 120 seconds |
| No progress for one page | 90 seconds |
| Full OCR operation | 1800 seconds |

Adjust the limits through environment variables:

```text
OCR_STARTUP_TIMEOUT_SECONDS=120
OCR_PAGE_TIMEOUT_SECONDS=90
OCR_TOTAL_TIMEOUT_SECONDS=1800
```

If RapidOCR initialization exceeds the limit, the task shows a clear error instead of remaining at 1%. Return to configuration and select EasyOCR, or reinstall compatible `rapidocr_onnxruntime` and ONNXRuntime packages. Install EasyOCR with:

```powershell
python -m pip --python .venv\Scripts\python.exe install easyocr
```

## Video and FFmpeg Reliability

Scene video clips are created at exact durations before a continuous narration timeline is muxed. This prevents accumulated AAC encoder delay and keeps scene boundaries aligned.

For long PDFs, video and audio are concatenated in batches of at most eight scenes. H.264 inputs use one decoder thread, filter graphs use one thread, and H.264 encoding uses two threads. This lowers server memory use and prevents errors such as:

```text
h264 ... thread_get_buffer failed
h264 ... get_buffer failed
```

Batching preserves scene order, configured duration, and silent tracks when TTS is disabled.

## Tasks and Downloads

Task metadata and checkpoints are stored in `data/app.db`. Generated pages, OCR results, images, audio, subtitles, and videos are stored under `outputs/<task_id>/`.

- **My Tasks** lists the signed-in user's tasks.
- Completed, failed, and paused tasks can be deleted.
- **New Task** starts another workflow without logging out.
- Pause is cooperative: the current page, model request, TTS clip, or FFmpeg operation completes before a checkpoint is written.
- After a server restart, interrupted tasks become paused and can resume from persisted checkpoints.
- The final page provides MP4, SRT, preview, and optional AI-video-prompt downloads.

## Project Layout

```text
pdf2video/
├── app.py                  Flask routes and pipeline orchestration
├── config.py               Application defaults and model presets
├── requirements.txt        Python dependencies
├── install.* / run.* / stop.*
├── README.md               Chinese documentation
├── README_EN.md            English documentation
├── core/
│   ├── pdf_processor.py    PDF rendering
│   ├── ocr_processor.py    OCR orchestration and timeout protection
│   ├── ocr_worker.py       Killable OCR worker process
│   ├── story_analyzer.py   OpenAI-compatible story analysis
│   ├── image_generator.py  OpenAI/NVIDIA/Agnes image generation
│   ├── tts_engine.py       TTS generation
│   ├── subtitle_builder.py SRT generation
│   └── video_builder.py    FFmpeg assembly and BGM mixing
├── templates/              Web UI HTML
└── static/                 CSS, JavaScript, and favicon
```

Commit source code, templates, static assets, scripts, `requirements.txt`, `.env.example`, `.gitignore`, and both README files. Do not commit `.env`, `.venv/`, `data/`, `uploads/`, `outputs/`, `user_settings.json`, caches, logs, or generated media. The supplied `.gitignore` covers these files.

## Troubleshooting

### The task stays at 1%

Restart with the current version. Startup enters PDF extraction before optional AI/video modules load. OCR reports elapsed initialization time and stops at the configured limit instead of waiting forever.

### FFmpeg fails during video assembly

Check free RAM and disk space, update to the current version, and retry. Long videos use bounded batches and constrained FFmpeg threads. Errors retain high-signal lines and the final FFmpeg log tail.

### There is no audio

Verify TTS is enabled and that the server can reach Microsoft's Edge TTS service. On Windows, a local Chinese SAPI fallback is attempted. If neither service is available, the video is built with a silent audio track.

### AI image generation fails

Disable AI image generation to continue with source PDF pages, or verify that the selected image provider supports its selected model and route. Image colorization requires an image-editing or image-to-image model; a normal text LLM cannot colorize an image.

## Security

Treat all API keys as secrets. Use HTTPS on public deployments, avoid untrusted frontend scripts, and rotate any key that is exposed in a commit or server log.
