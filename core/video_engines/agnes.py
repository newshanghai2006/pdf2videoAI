# -*- coding: utf-8 -*-
"""Agnes Video V2.0 异步视频引擎。"""
import json
import math
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from config import FFMPEG_BIN, FPS
from ..prompt_optimizer import (
    CHINESE_VISUAL_NEGATIVE_EN,
    optimize_chinese_visual_prompt,
)
from ..rate_limiter import agnes_video_limiter
from .base import VideoEngine


def _api_root(base_url):
    base = (base_url or "https://apihub.agnes-ai.com/v1").strip().rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _safe_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


class AgnesVideoEngine(VideoEngine):
    name = "agnes"

    def __init__(self, api_key=None, base_url=None, model=None,
                 resolution_tier="720p", frame_rate=24, timeout=1800,
                 **_ignored):
        self.api_key = (api_key or os.environ.get("AGNES_API_KEY", "")).strip()
        self.base_url = _api_root(base_url or os.environ.get("AGNES_BASE_URL", ""))
        self.model = (model or os.environ.get(
            "AGNES_VIDEO_MODEL", "agnes-video-v2.0")).strip()
        tier = str(resolution_tier or "720p").lower()
        self.resolution_tier = tier if tier in ("480p", "720p", "1080p") else "720p"
        self.frame_rate = min(60, max(1, int(frame_rate or 24)))
        self.timeout = max(300, int(timeout or 1800))

    def _request_json(self, request, operation):
        last_error = None
        for attempt in range(2):
            agnes_video_limiter.wait()
            try:
                with urllib.request.urlopen(request, timeout=360) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(
                    f"Agnes 视频{operation}失败: HTTP {error.code}: {detail}"
                )
                if error.code not in (429, 503) or attempt:
                    raise last_error from error
                time.sleep(60 if error.code == 429 else 5)
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = RuntimeError(f"Agnes 视频{operation}连接失败: {error}")
                if attempt:
                    raise last_error from error
                time.sleep(5)
        raise last_error

    def _request_dimensions(self, width, height):
        landscape = width >= height
        long_edge = {"480p": 854, "720p": 1280, "1080p": 1920}[self.resolution_tier]
        short_edge = {"480p": 480, "720p": 720, "1080p": 1080}[self.resolution_tier]
        return (long_edge, short_edge) if landscape else (short_edge, long_edge)

    def _frame_count(self, duration):
        # 向上取到最接近的 8n+1，既覆盖目标秒数又不超过官方 441 帧上限。
        target = min(float(duration), 441.0 / self.frame_rate)
        frames = int(math.ceil(max(1.0, target) * self.frame_rate))
        frames = int(math.ceil(max(1, frames - 1) / 8.0) * 8 + 1)
        return min(441, max(9, frames))

    def _submit_task(self, scene, width, height, duration):
        req_width, req_height = self._request_dimensions(width, height)
        prompt = (scene.get("video_prompt") or scene.get("image_prompt")
                  or scene.get("narration") or "A cinematic story scene").strip()
        prompt = optimize_chinese_visual_prompt(prompt)
        prompt += (
            " Natural coherent motion, stable subject identity, smooth cinematic camera "
            "movement, no subtitles, no logos, no readable text."
        )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "mode": "ti2vid",
            "width": req_width,
            "height": req_height,
            "num_frames": self._frame_count(duration),
            "frame_rate": self.frame_rate,
            "negative_prompt": (
                "flicker, jitter, distorted anatomy, inconsistent character, subtitles, "
                "captions, logos, watermarks, readable text, "
                + CHINESE_VISUAL_NEGATIVE_EN
            ),
        }
        # Agnes 文档要求图生视频图片必须是公网 URL；本地 PDF 路径不能直接提交。
        public_image = scene.get("image_url") or scene.get("source_image_url")
        if isinstance(public_image, str) and public_image.startswith(("http://", "https://")):
            payload["image"] = public_image

        request = urllib.request.Request(
            self.base_url + "/v1/videos",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        result = self._request_json(request, "任务提交")
        video_id = result.get("video_id")
        task_id = result.get("task_id") or result.get("id")
        if not video_id and not task_id:
            raise RuntimeError(f"Agnes 视频任务响应缺少 video_id/task_id: {result}")
        return video_id, task_id, result

    def _poll_task(self, video_id, task_id, progress_callback=None):
        started = time.monotonic()
        while time.monotonic() - started < self.timeout:
            if video_id:
                query = urllib.parse.urlencode({
                    "video_id": video_id,
                    "model_name": self.model,
                })
                url = self.base_url + "/agnesapi?" + query
            else:
                url = self.base_url + "/v1/videos/" + urllib.parse.quote(task_id)
            request = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Accept": "application/json"},
                method="GET",
            )
            result = self._request_json(request, "状态查询")
            status = str(result.get("status") or "").lower()
            progress = result.get("progress")
            if progress_callback:
                suffix = f"（{progress}%）" if progress is not None else ""
                progress_callback(f"Agnes 视频生成中：{status or '等待'}{suffix}")
            if status in ("completed", "succeeded", "success"):
                metadata = result.get("metadata") or {}
                url = metadata.get("url") or result.get("url")
                if not url:
                    raise RuntimeError(f"Agnes 视频任务完成但未返回 metadata.url: {result}")
                return url
            if status in ("failed", "error", "cancelled", "canceled"):
                raise RuntimeError(f"Agnes 视频任务失败: {result.get('error') or result}")
        raise TimeoutError(f"Agnes 视频任务等待超过 {self.timeout} 秒")

    def _download_and_normalize(self, video_url, out_path, width, height, duration,
                                fade_in=False, fade_out=False):
        fd, downloaded = tempfile.mkstemp(prefix="agnes_video_", suffix=".mp4")
        os.close(fd)
        try:
            request = urllib.request.Request(video_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=360) as response:
                with open(downloaded, "wb") as handle:
                    handle.write(response.read())

            duration = max(1.0, float(duration))
            filters = [
                f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
                f"fps={FPS}",
                f"tpad=stop_mode=clone:stop_duration={duration:.6f}",
                f"trim=duration={duration:.6f}",
                "setpts=PTS-STARTPTS",
            ]
            if fade_in:
                filters.append("fade=t=in:st=0:d=0.8")
            if fade_out:
                filters.append(f"fade=t=out:st={max(0.0, duration - 1.0):.6f}:d=1.0")
            command = [
                FFMPEG_BIN, "-y", "-filter_threads", "1", "-threads", "1", "-i", downloaded,
                "-an", "-vf", ",".join(filters), "-t", f"{duration:.6f}",
                "-c:v", "libx264", "-preset", "medium", "-threads", "2", "-crf", "20",
                "-pix_fmt", "yuv420p", out_path,
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                raise RuntimeError(f"Agnes 视频 FFmpeg 规整失败: {result.stderr[-1800:]}")
            return out_path
        finally:
            _safe_remove(downloaded)

    def generate_clip(self, scene, out_path, width, height, duration,
                      fade_in=False, fade_out=False, index=0, total=1,
                      progress_callback=None):
        if not self.api_key:
            raise ValueError("选择 Agnes 视频引擎后必须填写视频 API Key，或复用 Agnes LLM Key")
        if progress_callback:
            progress_callback(f"正在提交 Agnes 视频场景 {index + 1}/{total}")
        video_id, task_id, result = self._submit_task(scene, width, height, duration)
        if progress_callback:
            actual = result.get("seconds")
            progress_callback(
                f"Agnes 视频任务已提交{f'（接口时长 {actual} 秒）' if actual else ''}，等待生成"
            )
        video_url = self._poll_task(video_id, task_id, progress_callback)
        return self._download_and_normalize(
            video_url, out_path, width, height, duration,
            fade_in=fade_in, fade_out=fade_out,
        )
