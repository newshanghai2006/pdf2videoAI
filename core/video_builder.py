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

MAX_SCENE_DURATION = 90.0
# Keep each FFmpeg filter graph bounded. Opening every scene from a long
# document in one command can exceed a server's file-descriptor or memory limit.
MAX_CONCAT_INPUTS = 16


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
        # A stream-mapping line can be the final "important" line while the
        # actual error is at the end of stderr. Keep both views for diagnosis.
        selected = important[-8:]
        tail = lines[-16:]
        detail = " | ".join(selected + [line for line in tail if line not in selected])
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


def _get_stream_duration(media_path, stream_selector, default=0.0):
    """Return a stream duration for diagnostics and final muxing."""
    try:
        result = subprocess.run(
            [FFPROBE_BIN, "-v", "quiet", "-select_streams", stream_selector,
             "-show_entries", "stream=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", media_path],
            capture_output=True, text=True, timeout=15,
        )
        values = [float(line.strip()) for line in result.stdout.splitlines()
                  if line.strip()]
        return values[0] if values and values[0] > 0 else default
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
    if scene.get('is_cover') or scene.get('is_pdf_cover'):
        return min(MAX_SCENE_DURATION, max(1.0, float(scene.get('duration', 3) or 3)))
    audio_path = scene.get("audio_path") if use_tts and auto_duration_tts else None
    if audio_path and os.path.exists(audio_path):
        # 时长读取与可解码性分开：轻微坏帧不应让片段退回手动时长。
        audio_duration = _get_audio_duration(audio_path, default=scene.get("duration", 5))
        dur = max(float(scene.get("duration", 5) or 5), audio_duration)
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
    return _mux_scene_normalized(silent_video, audio_path, out_path, duration)
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


def _mux_scene_normalized(silent_video, audio_path, out_path, duration):
    """Create a zero-based, equal-duration A/V segment.

    Each scene is re-encoded deliberately. Copying the silent video while
    filtering only the audio preserves edit-list timestamps and can accumulate
    an audible offset at concat boundaries.
    """
    audio_path = _valid_audio_path(audio_path)
    duration = max(1.0, float(duration))
    duration_text = f"{duration:.6f}"
    audio_input = ["-i", audio_path] if audio_path else [
        "-f", "lavfi", "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
    ]
    filter_complex = (
        f"[0:v]setpts=PTS-STARTPTS,fps={FPS},"
        f"tpad=stop_mode=clone:stop_duration={duration_text},"
        f"trim=duration={duration_text},setpts=PTS-STARTPTS[v];"
        f"[1:a]aresample=44100:async=1:first_pts=0,"
        f"asetpts=PTS-STARTPTS,apad,atrim=duration={duration_text},"
        f"asetpts=PTS-STARTPTS[a]"
    )
    args = [
        "-y", "-i", silent_video, *audio_input,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(FPS), "-fps_mode", "cfr",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-map_metadata", "-1", "-avoid_negative_ts", "make_zero",
        "-t", duration_text, out_path,
    ]
    _run_ffmpeg(args, timeout=300)
    return out_path


def _concat_av_segments(seg_paths, output_path):
    """拼接多段「已含音轨」的音视频片段。

    统一重编码拼接（而非 -c copy），规避不同片段间时间基/关键帧差异导致的
    音画错位；音视频一起重编码，时间线保持一致。
    """
    return _concat_av_segments_filter(seg_paths, output_path)

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


def _concat_av_segments_filter(seg_paths, output_path):
    """Concatenate normalized segments while removing per-segment AAC padding."""
    if not seg_paths:
        raise RuntimeError("No video segments to concatenate")
    filter_lines = []
    concat_inputs = []
    input_args = []
    for index, path in enumerate(seg_paths):
        input_args.extend(["-i", path])
        duration = _get_stream_duration(path, "v:0", 1.0)
        duration_text = f"{duration:.6f}"
        filter_lines.append(
            f"[{index}:v]setpts=PTS-STARTPTS,trim=duration={duration_text},"
            f"setpts=PTS-STARTPTS[v{index}]"
        )
        filter_lines.append(
            f"[{index}:a]aresample=44100,asetpts=PTS-STARTPTS,"
            f"atrim=duration={duration_text},asetpts=PTS-STARTPTS[a{index}]"
        )
        concat_inputs.extend([f"[v{index}]", f"[a{index}]"])
    filter_lines.append(
        "".join(concat_inputs)
        + f"concat=n={len(seg_paths)}:v=1:a=1[vout][aout]"
    )
    script_path = output_path + ".filter.txt"
    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write(";\n".join(filter_lines))
    args = [
        "-y", *input_args,
        "-filter_complex_script", script_path,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(FPS), "-fps_mode", "cfr",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-map_metadata", "-1", "-avoid_negative_ts", "make_zero",
        "-video_track_timescale", "90000", output_path,
    ]
    try:
        _run_ffmpeg(args, timeout=900)
    finally:
        _safe_remove(script_path)


def _concat_video_segments_filter_once(seg_paths, durations, output_path):
    """Concatenate silent video segments using the requested scene durations.

    Audio is deliberately kept out of this pass.  Encoding AAC independently
    for every scene introduces encoder priming at each boundary; that becomes
    audible as drift when a silent cover is prepended.  The audio timeline is
    built once in ``_mux_scene_audio`` below.
    """
    if not seg_paths:
        raise RuntimeError("No video segments to concatenate")
    if len(seg_paths) != len(durations):
        raise RuntimeError("Video segment/duration count mismatch")

    filter_lines = []
    input_args = []
    concat_inputs = []
    for index, (path, duration) in enumerate(zip(seg_paths, durations)):
        input_args.extend(["-i", path])
        duration_text = f"{max(1.0, float(duration)):.6f}"
        filter_lines.append(
            f"[{index}:v]setpts=PTS-STARTPTS,fps={FPS},"
            f"tpad=stop_mode=clone:stop_duration={duration_text},"
            f"trim=duration={duration_text},setpts=PTS-STARTPTS[v{index}]"
        )
        concat_inputs.append(f"[v{index}]")
    filter_lines.append(
        "".join(concat_inputs)
        + f"concat=n={len(seg_paths)}:v=1:a=0[vout]"
    )
    script_path = output_path + ".filter.txt"
    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write(";\n".join(filter_lines))
    args = [
        "-y", *input_args,
        "-filter_complex_script", script_path,
        "-map", "[vout]",
        "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(FPS), "-fps_mode", "cfr",
        "-map_metadata", "-1", "-avoid_negative_ts", "make_zero",
        "-video_track_timescale", "90000", output_path,
    ]
    try:
        _run_ffmpeg(args, timeout=900)
    finally:
        _safe_remove(script_path)


def _concat_video_segments_filter(seg_paths, durations, output_path):
    """Concatenate silent scenes in bounded batches for long documents.

    A single concat graph with dozens of MP4 inputs is fragile on constrained
    servers.  Each batch is normalized exactly as before, then its output is
    used as one input in the next round.  The requested scene duration remains
    the source of truth, so batching does not alter the film timeline.
    """
    if not seg_paths:
        raise RuntimeError("No video segments to concatenate")
    if len(seg_paths) != len(durations):
        raise RuntimeError("Video segment/duration count mismatch")
    if len(seg_paths) <= MAX_CONCAT_INPUTS:
        return _concat_video_segments_filter_once(seg_paths, durations, output_path)

    batch_dir = tempfile.mkdtemp(
        prefix="concat_video_", dir=os.path.dirname(os.path.abspath(output_path)) or None
    )
    current_paths = list(seg_paths)
    current_durations = [max(1.0, float(duration)) for duration in durations]
    round_index = 0
    try:
        while len(current_paths) > MAX_CONCAT_INPUTS:
            next_paths = []
            next_durations = []
            for batch_index, start in enumerate(range(0, len(current_paths), MAX_CONCAT_INPUTS)):
                paths = current_paths[start:start + MAX_CONCAT_INPUTS]
                batch_durations = current_durations[start:start + MAX_CONCAT_INPUTS]
                batch_path = os.path.join(
                    batch_dir, f"video_{round_index:02d}_{batch_index:04d}.mp4"
                )
                _concat_video_segments_filter_once(paths, batch_durations, batch_path)
                next_paths.append(batch_path)
                next_durations.append(sum(batch_durations))
            current_paths = next_paths
            current_durations = next_durations
            round_index += 1
        return _concat_video_segments_filter_once(
            current_paths, current_durations, output_path
        )
    finally:
        shutil.rmtree(batch_dir, ignore_errors=True)


def _mux_scene_audio_once(video_path, scenes, durations, output_path, use_tts=True):
    """Mux one continuous scene audio timeline onto the concatenated video.

    All scene audio is decoded and concatenated before the single AAC encode.
    Cover scenes and scenes without TTS use generated PCM silence of exactly
    the scene duration, so the cover never changes the narration start time.
    """
    if len(scenes) != len(durations):
        raise RuntimeError("Scene/duration count mismatch")

    input_args = ["-i", video_path]
    filter_lines = []
    audio_labels = []
    for index, (scene, duration) in enumerate(zip(scenes, durations), start=1):
        audio_path = _valid_audio_path(scene.get("audio_path")) if use_tts else None
        if audio_path:
            input_args.extend(["-i", audio_path])
        else:
            input_args.extend([
                "-f", "lavfi", "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
            ])
        duration_text = f"{max(1.0, float(duration)):.6f}"
        filter_lines.append(
            f"[{index}:a]aresample=44100:async=1:first_pts=0,"
            f"asetpts=PTS-STARTPTS,apad,atrim=duration={duration_text},"
            f"asetpts=PTS-STARTPTS[a{index}]"
        )
        audio_labels.append(f"[a{index}]")
    filter_lines.append(
        "".join(audio_labels)
        + f"concat=n={len(scenes)}:v=0:a=1[aout]"
    )
    script_path = output_path + ".filter.txt"
    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write(";\n".join(filter_lines))
    total_duration = sum(max(1.0, float(d)) for d in durations)
    total_text = f"{total_duration:.6f}"
    args = [
        "-y", *input_args,
        "-filter_complex_script", script_path,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-ar", "44100", "-ac", "2", "-t", total_text,
        "-map_metadata", "-1", "-avoid_negative_ts", "make_zero",
        "-muxdelay", "0", "-muxpreload", "0", output_path,
    ]
    try:
        _run_ffmpeg(args, timeout=900)
    finally:
        _safe_remove(script_path)


def _render_scene_audio_batch(scenes, durations, output_path, use_tts=True):
    """Render one bounded set of scene narration/silence to a PCM WAV file."""
    input_args = []
    filter_lines = []
    audio_labels = []
    for index, (scene, duration) in enumerate(zip(scenes, durations)):
        audio_path = _valid_audio_path(scene.get("audio_path")) if use_tts else None
        if audio_path:
            input_args.extend(["-i", audio_path])
        else:
            input_args.extend([
                "-f", "lavfi", "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
            ])
        duration_text = f"{max(1.0, float(duration)):.6f}"
        filter_lines.append(
            f"[{index}:a]aresample=44100:async=1:first_pts=0,"
            f"asetpts=PTS-STARTPTS,apad,atrim=duration={duration_text},"
            f"asetpts=PTS-STARTPTS[a{index}]"
        )
        audio_labels.append(f"[a{index}]")
    filter_lines.append(
        "".join(audio_labels) + f"concat=n={len(scenes)}:v=0:a=1[aout]"
    )
    script_path = output_path + ".filter.txt"
    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write(";\n".join(filter_lines))
    total_text = f"{sum(max(1.0, float(d)) for d in durations):.6f}"
    args = [
        "-y", *input_args,
        "-filter_complex_script", script_path, "-map", "[aout]",
        "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2",
        "-map_metadata", "-1", "-t", total_text, output_path,
    ]
    try:
        _run_ffmpeg(args, timeout=900)
    finally:
        _safe_remove(script_path)


def _concat_wav_batches(batch_paths, output_path):
    """Join already-normalized PCM WAV batches without a large filter graph."""
    list_path = output_path + ".concat.txt"
    with open(list_path, "w", encoding="utf-8") as handle:
        for path in batch_paths:
            normalized = os.path.abspath(path).replace("\\", "/").replace("'", "'\\''")
            handle.write(f"file '{normalized}'\n")
    try:
        _run_ffmpeg([
            "-y", "-f", "concat", "-safe", "0", "-i", list_path,
            "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2",
            "-map_metadata", "-1", output_path,
        ], timeout=900)
    finally:
        _safe_remove(list_path)


def _mux_scene_audio(video_path, scenes, durations, output_path, use_tts=True):
    """Mux narration onto video, batching long scene lists to protect FFmpeg."""
    if len(scenes) != len(durations):
        raise RuntimeError("Scene/duration count mismatch")
    if len(scenes) <= MAX_CONCAT_INPUTS:
        return _mux_scene_audio_once(video_path, scenes, durations, output_path, use_tts)

    batch_dir = tempfile.mkdtemp(
        prefix="concat_audio_", dir=os.path.dirname(os.path.abspath(output_path)) or None
    )
    try:
        batch_paths = []
        for batch_index, start in enumerate(range(0, len(scenes), MAX_CONCAT_INPUTS)):
            batch_path = os.path.join(batch_dir, f"audio_{batch_index:04d}.wav")
            _render_scene_audio_batch(
                scenes[start:start + MAX_CONCAT_INPUTS],
                durations[start:start + MAX_CONCAT_INPUTS], batch_path, use_tts,
            )
            batch_paths.append(batch_path)

        combined_audio = os.path.join(batch_dir, "combined.wav")
        _concat_wav_batches(batch_paths, combined_audio)
        total_text = f"{sum(max(1.0, float(d)) for d in durations):.6f}"
        _run_ffmpeg([
            "-y", "-i", video_path, "-i", combined_audio,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-ar", "44100", "-ac", "2", "-t", total_text,
            "-map_metadata", "-1", "-avoid_negative_ts", "make_zero",
            "-muxdelay", "0", "-muxpreload", "0", output_path,
        ], timeout=900)
        return output_path
    finally:
        shutil.rmtree(batch_dir, ignore_errors=True)


def _mix_bgm(video_path, output_path, bgm_path, bgm_volume=0.15):
    """把背景音乐混入已带配音（或静音轨）的视频。BGM 循环铺满、降到设定音量。"""
    return _mix_bgm_normalized(video_path, output_path, bgm_path, bgm_volume)
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


def _mix_bgm_normalized(video_path, output_path, bgm_path, bgm_volume=0.15):
    """Mix BGM using the video stream as the master clock."""
    duration = _get_stream_duration(video_path, "v:0", _get_audio_duration(video_path))
    duration_text = f"{duration:.6f}"
    args = [
        "-y", "-i", video_path,
        "-stream_loop", "-1", "-i", bgm_path,
        "-filter_complex",
        f"[0:a]aresample=44100,atrim=duration={duration_text}[voice];"
        f"[1:a]volume={bgm_volume},aresample=44100,"
        f"atrim=duration={duration_text}[bgm];"
        f"[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0,"
        f"aresample=44100:async=1:first_pts=0,atrim=duration={duration_text}[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-ar", "44100", "-ac", "2", "-map_metadata", "-1",
        "-avoid_negative_ts", "make_zero", "-t", duration_text,
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
        rendered_scenes = []
        scene_durations = []
        for i, scene in enumerate(scenes):
            image_path = scene.get("image_path")
            if not image_path or not os.path.exists(image_path):
                continue

            if progress_callback:
                pct = int(i / total * 70)
                progress_callback(pct, f"合成场景 {i + 1}/{total}")

            duration = _scene_duration(scene, use_tts, auto_duration_tts)
            scene['effective_duration'] = duration
            if progress_callback:
                progress_callback(int(i / total * 70),
                                  f"合成场景 {i + 1}/{total}（{duration:.1f} 秒）")

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
            # Keep video-only segments. Audio is concatenated once below so
            # AAC encoder priming cannot accumulate at scene boundaries.
            segments.append(silent)
            rendered_scenes.append(scene)
            scene_durations.append(duration)
            scene['rendered_video_duration'] = duration
            scene['rendered_audio_duration'] = duration if use_tts else 0.0

        if not segments:
            raise RuntimeError("没有可用的场景画面")

        # 3) 拼接所有自洽片段
        if progress_callback:
            progress_callback(78, "拼接场景片段...")
        merged_video = os.path.join(tmp_dir, "merged_video.mp4")
        _concat_video_segments_filter(segments, scene_durations, merged_video)
        merged = os.path.join(tmp_dir, "merged.mp4")
        _mux_scene_audio(merged_video, rendered_scenes, scene_durations, merged, use_tts=use_tts)

        # 4) 可选：混入背景音乐
        if bgm_path and os.path.exists(bgm_path):
            if progress_callback:
                progress_callback(90, "混合背景音乐...")
            _mix_bgm_normalized(merged, output_path, bgm_path, bgm_volume)
        else:
            shutil.copy2(merged, output_path)

        if progress_callback:
            progress_callback(100, "影片合成完成!")

        return output_path

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
