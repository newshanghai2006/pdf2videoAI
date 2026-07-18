# -*- coding: utf-8 -*-
"""视频合成器：将各场景画面 + 逐场景配音合成影片，并混入可选背景音乐。

音画同步的核心思路（修复旧版累积漂移的关键）：
  旧版把「画面时间线」和「配音时间线」分开拼接，再用 -shortest 硬对齐，
  且画面时长被夹取到 [3,15] 秒而配音未夹取，导致越往后越不同步。

  新版改为「逐场景对齐」：每个场景先生成一段**画面长度 == 该场景配音长度**的
  无声视频（时长取自实际音频，无夹取），再把该场景的配音贴到这段视频上，得到一段
  **自洽同步**的音视频片段；最后把所有片段整体拼接。任何单场景的音画都严丝合缝，
  拼接后也不会累积漂移。无配音的场景用其 duration（默认 5s）作为片段时长。
"""
import os
import subprocess
import tempfile
import shutil

from config import FFMPEG_BIN, FFPROBE_BIN, FPS
from .video_engines import get_engine

MAX_SCENE_DURATION = 60.0


# ===== 基础工具 =====

def _safe_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _run_ffmpeg(args, timeout=300):
    cmd = [FFMPEG_BIN] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
        important = [line for line in lines if any(word in line.lower() for word in (
            'error', 'invalid', 'failed', 'non-monoton', 'timestamp', 'unable', 'could not'
        ))]
        detail = " | ".join((important[-8:] or lines[-12:]))
        raise RuntimeError(f"ffmpeg 失败: {detail[-2500:]}")


def _get_audio_duration(audio_path, default=5.0):
    """获取音频时长（秒）。失败返回 default。"""
    try:
        result = subprocess.run(
            [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        value = float(result.stdout.strip())
        return value if value > 0 else default
    except Exception:
        return default


def _valid_audio_path(audio_path):
    """确认音频存在且包含可解码的音频流。"""
    if not audio_path or not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1024:
        return None
    try:
        result = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name,duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=15,
        )
        values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if result.returncode != 0 or not values:
            return None
        # ffprobe 可能只读到容器元数据；再让 FFmpeg 完整解码到空输出，
        # 捕获 AAC 包损坏、截断或无效帧。
        decoded = subprocess.run(
            [FFMPEG_BIN, "-v", "error", "-xerror", "-err_detect", "explode",
             "-i", audio_path, "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
        )
        if decoded.returncode != 0:
            return None
        return audio_path
    except Exception:
        return None


def _scene_duration(scene, use_tts, auto_duration_tts=True):
    """确定某场景片段应有的时长（秒）。

    有配音 → 取配音真实时长（不夹取，保证音画一致）；
    无配音 → 取场景建议 duration，兜底 5s。仅做一个宽松下限避免 0 时长。
    """
    valid_audio = _valid_audio_path(scene.get("audio_path")) if use_tts and auto_duration_tts else None
    if valid_audio:
        dur = _get_audio_duration(valid_audio, default=scene.get("duration", 5))
    else:
        dur = float(scene.get("duration", 5) or 5)
    # OCR/TTS 异常可能产生数百秒的单段音频；限制单段时长，避免静态 FFmpeg 长时间超时。
    return min(MAX_SCENE_DURATION, max(1.0, dur))


# ===== 逐场景合成：画面 + 配音 → 自洽同步片段 =====

def _mux_scene(silent_video, audio_path, out_path, duration):
    """把单场景配音贴到该场景无声视频上，输出自洽同步的片段。

    - 有配音：视频与音频等长（都等于 duration），直接对齐，无需 -shortest 截断。
    - 无配音：补一条等长静音轨，保证所有片段结构一致（都含音轨），拼接更稳。
    """
    audio_path = _valid_audio_path(audio_path)
    if audio_path:
        args = [
            "-y",
            "-i", silent_video,
            "-i", audio_path,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-ar", "44100", "-ac", "2",
            # 视频与音频均已 ≈duration，用 apad+atrim 把音频精确补/截到 duration，
            # 避免个别编码器的毫秒级尾差造成拼接处对不齐。
            "-af", f"apad,atrim=0:{duration}",
            "-t", f"{duration}",
            out_path,
        ]
    else:
        # 无配音：生成等长静音轨
        args = [
            "-y",
            "-i", silent_video,
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-ar", "44100", "-ac", "2",
            "-t", f"{duration}",
            out_path,
        ]
    _run_ffmpeg(args, timeout=180)
    return out_path


def _concat_av_segments(seg_paths, output_path):
    """拼接多段「已含音轨」的音视频片段。

    统一重编码拼接（而非 -c copy），规避不同片段间时间基/关键帧差异导致的
    音画错位；音视频一起重编码，时间线保持一致。
    """
    if len(seg_paths) == 1:
        shutil.copy2(seg_paths[0], output_path)
        return

    list_file = output_path + ".concat.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in seg_paths:
            p = os.path.abspath(p).replace("\\", "/")
            f.write(f"file '{p}'\n")

    args = [
        "-y", "-fflags", "+genpts", "-f", "concat", "-safe", "0", "-i", list_file,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-r", str(FPS),
        "-fps_mode", "cfr", "-avoid_negative_ts", "make_zero",
        "-max_muxing_queue_size", "1024",
        "-video_track_timescale", "90000",
        output_path,
    ]
    try:
        _run_ffmpeg(args, timeout=900)
    finally:
        _safe_remove(list_file)


def _mix_bgm(video_path, output_path, bgm_path, bgm_volume=0.15):
    """把背景音乐混入已带配音（或静音轨）的视频。BGM 循环铺满、降到设定音量。"""
    duration = _get_audio_duration(video_path)
    args = [
        "-y",
        "-i", video_path,
        "-stream_loop", "-1", "-i", bgm_path,
        "-filter_complex",
        f"[1:a]volume={bgm_volume},aresample=44100[bgm];"
        f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=0[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{duration}",
        output_path,
    ]
    _run_ffmpeg(args, timeout=600)


def build_film(scenes, output_path, width, height,
               bgm_path=None, bgm_volume=0.15,
               use_tts=True, video_engine="kenburns", engine_opts=None,
               progress_callback=None, auto_duration_tts=True):
    """合成最终影片。

    Args:
        scenes: 场景列表（含 image_path / audio_path / narration / duration 等）
        output_path: 输出视频路径
        width, height: 分辨率
        bgm_path: 背景音乐路径（可选）
        bgm_volume: BGM 音量（0-1）
        use_tts: 是否使用配音
        video_engine: 视频引擎名（kenburns / seedance / ...）
        engine_opts: dict，透传给引擎构造（如 seedance 的 api_key/base_url/model）
        progress_callback: 回调 (percent, message)

    Returns:
        str: 输出视频路径
    """
    total = len(scenes)
    engine = get_engine(video_engine, **(engine_opts or {}))
    tmp_dir = tempfile.mkdtemp(prefix="film_")

    try:
        segments = []
        for i, scene in enumerate(scenes):
            image_path = scene.get("image_path")
            if not image_path or not os.path.exists(image_path):
                continue

            if progress_callback:
                pct = int(i / total * 70)
                progress_callback(pct, f"合成场景 {i + 1}/{total}")

            duration = _scene_duration(scene, use_tts, auto_duration_tts)

            # 1) 引擎生成「时长恰为 duration」的无声画面片段
            silent = os.path.join(tmp_dir, f"silent_{i:04d}.mp4")
            engine.generate_clip(
                scene, silent, width, height, duration,
                fade_in=(i == 0), fade_out=(i == total - 1),
                index=i, total=total,
                progress_callback=(
                    lambda m: progress_callback(int(i / total * 70), m))
                if progress_callback else None,
            )

            # 2) 贴上本场景配音（或静音轨），得到自洽同步片段
            audio_path = scene.get("audio_path") if use_tts else None
            seg = os.path.join(tmp_dir, f"seg_{i:04d}.mp4")
            _mux_scene(silent, audio_path, seg, duration)
            segments.append(seg)

        if not segments:
            raise RuntimeError("没有可用的场景画面")

        # 3) 拼接所有自洽片段
        if progress_callback:
            progress_callback(78, "拼接场景片段...")
        merged = os.path.join(tmp_dir, "merged.mp4")
        _concat_av_segments(segments, merged)

        # 4) 可选：混入背景音乐
        if bgm_path and os.path.exists(bgm_path):
            if progress_callback:
                progress_callback(90, "混合背景音乐...")
            _mix_bgm(merged, output_path, bgm_path, bgm_volume)
        else:
            shutil.copy2(merged, output_path)

        if progress_callback:
            progress_callback(100, "影片合成完成!")

        return output_path

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
