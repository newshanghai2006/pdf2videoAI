# -*- coding: utf-8 -*-
"""静态全画面引擎：完整显示图片，不做缩放、平移或裁剪。"""
import os
import subprocess

from config import FFMPEG_BIN, FPS
from .base import VideoEngine


class StaticEngine(VideoEngine):
    name = "static"

    def generate_clip(self, scene, out_path, width, height, duration,
                      fade_in=False, fade_out=False, index=0, total=1,
                      progress_callback=None):
        image_path = scene.get("image_path")
        if not image_path or not os.path.exists(image_path):
            raise RuntimeError(f"场景 {index + 1} 缺少可用画面")
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            "setsar=1"
        )
        cmd = [
            FFMPEG_BIN, "-y", "-loop", "1", "-i", image_path,
            "-vf", vf, "-t", str(duration), "-r", str(FPS),
            "-pix_fmt", "yuv420p", "-c:v", "libx264",
            "-preset", "medium", "-crf", "20", out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 静态画面生成失败: {result.stderr[-500:]}")
        return out_path
