# -*- coding: utf-8 -*-
"""全局配置 - AI 影片生成系统"""
import os


def _load_env_file():
    """从项目根目录的 .env 读取环境变量（若存在）。

    优先使用 python-dotenv；若未安装则手动解析，避免额外依赖。
    只在变量尚未设置时补全，不会覆盖已有环境变量。
    """
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
        return
    except Exception:
        pass
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


_load_env_file()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
STATIC_DIR = os.path.join(BASE_DIR, "static")
SETTINGS_FILE = os.path.join(BASE_DIR, "user_settings.json")
# 公网部署默认禁止把网页中输入的 API Key 写入服务器文件。
# 仅本地单用户需要兼容旧版客户端的 user_settings.json 接口时显式开启。
ENABLE_SERVER_SETTINGS = os.environ.get(
    "ENABLE_SERVER_SETTINGS", "false"
).strip().lower() in ("1", "true", "yes", "on")

# 视频分辨率
RESOLUTIONS = {
    "1080p_land": (1920, 1080),
    "720p_land": (1280, 720),
    "1080p_port": (1080, 1920),
    "720p_port": (720, 1280),
}

FPS = 25
DEFAULT_DURATION = 5  # 每场景默认秒数
DEFAULT_QUALITY = "1080p"

# 字体（跨平台探测；当前管线未做文字叠加，此项为预留/可选）
def _detect_cjk_font():
    """按操作系统探测一个可用的中文字体路径，找不到返回 None。"""
    import sys
    candidates = []
    if sys.platform.startswith("win"):
        base = os.environ.get("WINDIR", "C:/Windows") + "/Fonts/"
        candidates = [base + f for f in ("simhei.ttf", "msyh.ttc", "simsun.ttc")]
    elif sys.platform == "darwin":
        candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    else:  # Linux / 其它
        candidates = [
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


FONT_PATH = _detect_cjk_font()
FONT_FALLBACK = FONT_PATH

# ffmpeg
FFMPEG_BIN = "ffmpeg"
FFPROBE_BIN = "ffprobe"

# 上传限制
MAX_CONTENT_LENGTH = 200 * 1024 * 1024  # 200MB

# ===== AI 配置 =====

# OpenAI API
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
IMAGE_API_KEY = os.environ.get("IMAGE_API_KEY", "")
IMAGE_BASE_URL = os.environ.get("IMAGE_BASE_URL", "")

# LLM 模型（用于剧情理解和场景分析）
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-5.5")

# 图像生成模型
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "gpt-image-1")
IMAGE_QUALITY = "standard"  # standard / hd

# 常见模型预设（供前端下拉选择，也可手动输入任意模型名）
LLM_MODELS = {
    "gpt-5.5": "GPT-5.5（你的默认模型）",
    "gpt-5.4": "GPT-5.4",
    "gpt-5": "GPT-5",
    "gpt-4o": "GPT-4o",
    "gpt-4o-mini": "GPT-4o mini（快速省钱）",
    "deepseek-chat": "DeepSeek Chat",
    "sensenova-6.7-flash-lite": "SenseNova 6.7 Flash-Lite（1500次/5小时）",
    "sensenova-u1-fast": "SenseNova U1 Fast（1500次/5小时）",
    "deepseek-v4-flash": "SenseNova DeepSeek V4 Flash（500次/5小时）",
    "claude-3-5-sonnet": "Claude 3.5 Sonnet",
}

IMAGE_MODELS = {
    "gpt-image-1": "GPT-Image-1（OpenAI 新版图像模型，推荐）",
    "dall-e-3": "DALL·E 3",
    "dall-e-2": "DALL·E 2",
    "sensenova-u1-fast": "SenseNova U1 Fast（固定大尺寸自动适配）",
    "flux-1": "Flux（部分代理支持）",
    "stable-diffusion-3": "Stable Diffusion 3",
    "sd-xl": "Stable Diffusion XL",
}

NVIDIA_LLM_MODELS = {
    "meta/llama-3.1-70b-instruct": "Llama 3.1 70B Instruct",
    "meta/llama-3.1-8b-instruct": "Llama 3.1 8B Instruct",
    "mistralai/mixtral-8x7b-instruct-v0.1": "Mixtral 8x7B Instruct",
    "mistralai/mistral-nemo-minitron-8b-8k-instruct": "Mistral NeMo Minitron",
    "qwen/qwen2.5-coder-32b-instruct": "Qwen 2.5 Coder 32B",
    "z-ai/glm-5.2": "z-ai/glm-5.2",
}

NVIDIA_IMAGE_MODELS = {
    "black-forest-labs/flux.1-dev": "black-forest-labs/flux.1-dev (NVIDIA NIM)",
    "black-forest-labs/flux.1-schnell": "black-forest-labs/flux.1-schnell (NVIDIA NIM)",
    "black-forest-labs/flux.2-klein-4b": "FLUX.2 klein 4B (NVIDIA NIM)",
    "stabilityai/stable-diffusion-xl": "Stable Diffusion XL (NVIDIA NIM)",
}

AGNES_BASE_URL = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
AGNES_LLM_MODELS = {
    "agnes-2.0-flash": "Agnes 2.0 Flash（免费文本/视觉理解）",
}
AGNES_IMAGE_MODELS = {
    "agnes-image-2.0-flash": "Agnes Image 2.0 Flash（免费文生图/图生图）",
}
AGNES_VIDEO_MODELS = {
    "agnes-video-v2.0": "Agnes Video V2.0（免费异步视频生成）",
}

# Agnes Image 的档位式尺寸。服务端可能把精确像素请求标准化到这些尺寸。
AGNES_IMAGE_SIZES = {
    "1K": "1K（免费默认 20 RPM）",
    "2K": "2K（免费默认 10 RPM）",
    "3K": "3K（免费默认 1 RPM）",
    "4K": "4K（免费默认 1 RPM）",
}

# 艺术风格预设
ART_STYLES = {
    "chinese_ink": "中国传统水墨彩绘风格，色彩鲜明，笔触流畅，具有东方美学",
    "cinematic": "中国历史电影级写实风格，光影戏剧化，人物服饰与建筑符合中国朝代背景",
    "anime": "中国动画电影风格，色彩饱和度高，人物造型优美并保留中国文化特征",
    "oil_painting": "古典油画质感，厚重笔触与丰富光影，人物和场景仍严格保持中国历史特征",
    "illustration": "精美插画风，色彩温暖，细节丰富，适合故事叙述",
    "comic": "中国连环画彩色漫画风格，线条有力，色彩浓烈，保留中国人物与时代特征",
    "gongbi": "中国工笔重彩画风格，精细工整，色彩艳丽，具有传统国画韵味",
}

# TTS 语音
TTS_VOICES = {
    "zh-CN-XiaoxiaoNeural": "晓晓（女·温柔）",
    "zh-CN-YunxiNeural": "云希（男·沉稳）",
    "zh-CN-YunjianNeural": "云健（男·浑厚）",
    "zh-CN-XiaoyiNeural": "晓伊（女·活泼）",
    "zh-CN-YunyangNeural": "云扬（男·专业）",
    "zh-CN-XiaohanNeural": "晓涵（女·大气）",
}

# 旁白默认语音 / 对白默认语音（对白用不同声音，更像广播剧）
DEFAULT_NARRATION_VOICE = os.environ.get("NARRATION_VOICE", "zh-CN-YunxiNeural")
DEFAULT_DIALOGUE_VOICE = os.environ.get("DIALOGUE_VOICE", "zh-CN-XiaoxiaoNeural")

# ===== 视频引擎（画面动态的生成方式，可扩展）=====
# kenburns：本地用图像 + 缓动运镜合成（默认，无需额外 API）
# seedance：火山引擎 Seedance 文生/图生视频（真实动态视频，需火山 API，规划中）
# 新增引擎只需在 core/video_engines/ 下实现一个模块并在此登记。
VIDEO_ENGINES = {
    "static": "静态全画面（完整显示，不运动）",
    "kenburns": "Ken Burns 运镜（图像+缓动，默认，无需额外API）",
    "seedance": "火山 Seedance 文生视频（规划中，需火山 API Key）",
    "agnes": "Agnes Video V2.0（免费异步文生视频）",
}
DEFAULT_VIDEO_ENGINE = os.environ.get("VIDEO_ENGINE", "kenburns")
