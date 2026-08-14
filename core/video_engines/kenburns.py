# -*- coding: utf-8 -*-
"""Ken Burns 引擎：本地图像 + 缓动运镜（zoompan）合成片段。

从旧 video_builder 抽取而来，逻辑保持一致：去透明通道、对齐偶数尺寸、
zoompan 推拉摇移、可选淡入淡出。片段时长严格等于 duration，保证音画同步。
"""
import os
import random
import subprocess
import tempfile

from PIL import Image

from config import FFMPEG_BIN, FPS
from .base import VideoEngine


KEN_BURNS_MODES = [
    "zoom_in", "zoom_out",
    "pan_right", "pan_left",
    "pan_down", "pan_up",
]


def _safe_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _run_ffmpeg(args, timeout=300):
    cmd = [FFMPEG_BIN] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {result.stderr[-500:]}")


def _prepare_source_image(image_path, out_path):
    """去透明通道（填白）+ 对齐偶数尺寸，规避 libx264 的 get_buffer() 崩溃。"""
    img = Image.open(image_path).convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    img = Image.alpha_composite(bg, img).convert("RGB")
    w, h = img.size
    if w % 2 != 0 or h % 2 != 0:
        img = img.crop((0, 0, w - (w % 2), h - (h % 2)))
    img.save(out_path)
    return out_path


class KenBurnsEngine(VideoEngine):
    name = "kenburns"

    def __init__(self, **_ignored):
        # 接受并忽略额外参数（如从统一入口透传的 seedance api_key 等），
        # 保证 get_engine(**engine_opts) 对任何引擎都能构造。
        pass

    def generate_clip(self, scene, out_path, width, height, duration,
                      fade_in=False, fade_out=False, index=0, total=1,
                      progress_callback=None):
        image_path = scene.get("image_path")
        if not image_path or not os.path.exists(image_path):
            raise RuntimeError(f"场景 {index + 1} 缺少可用画面，无法用 Ken Burns 合成")

        # 运镜模式：按序轮换，保证相邻场景不同
        mode = KEN_BURNS_MODES[index % len(KEN_BURNS_MODES)]

        prepared = os.path.join(
            tempfile.gettempdir(),
            f"_kb_prep_{os.path.basename(out_path)}.png",
        )
        try:
            _prepare_source_image(image_path, prepared)
        except Exception:
            prepared = image_path

        frames = int(duration * FPS)

        if mode == "zoom_in":
            zoom_expr = "min(zoom+0.0015,1.5)"
            x_expr = "iw/2-(iw/zoom/2)"; y_expr = "ih/2-(ih/zoom/2)"
        elif mode == "zoom_out":
            zoom_expr = "if(eq(on,0),1.5,max(zoom-0.0015,1.0))"
            x_expr = "iw/2-(iw/zoom/2)"; y_expr = "ih/2-(ih/zoom/2)"
        elif mode == "pan_right":
            zoom_expr = "1.2"
            x_expr = f"(iw - iw/zoom) * on/{frames}"; y_expr = "(ih - ih/zoom) / 2"
        elif mode == "pan_left":
            zoom_expr = "1.2"
            x_expr = f"(iw - iw/zoom) * (1 - on/{frames})"; y_expr = "(ih - ih/zoom) / 2"
        elif mode == "pan_down":
            zoom_expr = "1.2"
            x_expr = "(iw - iw/zoom) / 2"; y_expr = f"(ih - ih/zoom) * on/{frames}"
        elif mode == "pan_up":
            zoom_expr = "1.2"
            x_expr = "(iw - iw/zoom) / 2"; y_expr = f"(ih - ih/zoom) * (1 - on/{frames})"
        else:
            zoom_expr = "min(zoom+0.0015,1.3)"
            x_expr = "iw/2-(iw/zoom/2)"; y_expr = "ih/2-(ih/zoom/2)"

        scale_w = int(width * 1.5)
        scale_h = int(height * 1.5)

        filters = [
            f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=increase",
            f"crop={scale_w}:{scale_h}",
            f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}'"
            f":d={frames}:s={width}x{height}:fps={FPS}",
        ]
        filter_str = ",".join(filters)

        if fade_in:
            filter_str += ",fade=t=in:st=0:d=0.8"
        if fade_out:
            fade_out_start = max(0, duration - 1.0)
            filter_str += f",fade=t=out:st={fade_out_start}:d=1.0"

        args = [
            "-y", "-filter_threads", "1", "-loop", "1", "-i", prepared,
            "-vf", filter_str,
            "-t", str(duration),
            "-r", str(FPS),
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-preset", "medium", "-threads", "2", "-crf", "20",
            out_path,
        ]

        try:
            _run_ffmpeg(args, timeout=120)
        finally:
            if prepared != image_path:
                _safe_remove(prepared)

        return out_path
