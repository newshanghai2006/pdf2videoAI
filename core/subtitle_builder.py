# -*- coding: utf-8 -*-
"""根据场景文本和音画时长生成 SRT 字幕。"""
import os
import subprocess

from config import FFPROBE_BIN


def _duration(scene, use_tts, auto_duration_tts=True):
    audio = scene.get("audio_path") if use_tts else None
    if audio and os.path.exists(audio):
        try:
            result = subprocess.run(
                [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", audio],
                capture_output=True, text=True, timeout=10,
            )
            audio_duration = max(1.0, float(result.stdout.strip()))
            if auto_duration_tts:
                return max(float(scene.get("duration", 5) or 5), audio_duration)
            return audio_duration
        except Exception:
            pass
    return max(1.0, float(scene.get("duration", 5) or 5))


def _timestamp(seconds):
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_srt(scenes, output_path, use_tts=True, auto_duration_tts=True):
    cursor = 0.0
    blocks = []
    for scene in scenes:
        duration = _duration(scene, use_tts, auto_duration_tts)
        narration = str(scene.get("narration") or "").strip()
        dialogue = scene.get("dialogue") or []
        if isinstance(dialogue, str):
            dialogue = [dialogue]
        text = "\n".join([narration] + [str(x).strip() for x in dialogue if str(x).strip()]).strip()
        if text:
            blocks.append(
                f"{len(blocks) + 1}\n{_timestamp(cursor)} --> {_timestamp(cursor + duration)}\n{text}\n"
            )
        cursor += duration
    with open(output_path, "w", encoding="utf-8-sig", newline="\n") as handle:
        handle.write("\n".join(blocks))
    return output_path
