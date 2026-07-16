# -*- coding: utf-8 -*-
"""TTS 引擎：用 edge-tts 生成中文配音（旁白与对白可用不同声音，含容错）。

对白处理：
  - 台词若形如「吕布：台词」/「吕布: 台词」/「吕布“台词”」，只朗读台词本身，
    不把角色名读出来（角色名仅用于挑选/区分声音，不进入语音内容）。
  - 旁白用旁白声音、对白用对白声音，分段合成后按场景拼接为一条场景音频。
"""
import os
import re
import asyncio
import logging
import subprocess

import edge_tts

from config import TTS_VOICES, DEFAULT_NARRATION_VOICE, DEFAULT_DIALOGUE_VOICE
from config import FFMPEG_BIN, FFPROBE_BIN

logger = logging.getLogger(__name__)

# 匹配「角色名 + 分隔符 + 台词」。分隔符支持中英文冒号。
_SPEAKER_RE = re.compile(
    r"^\s*(?P<name>[^：:，。！？\s]{1,12})\s*[：:]\s*(?P<line>.+)$",
    re.S,
)
# 台词外层可能带的引号
_QUOTES = "“”\"'「」『』"


def split_speaker(text):
    """从一句对白中拆出 (角色名, 台词)。无法识别角色名时返回 (None, 原文)。"""
    if not text:
        return None, ""
    m = _SPEAKER_RE.match(text.strip())
    if m:
        name = m.group("name").strip()
        line = m.group("line").strip().strip(_QUOTES).strip()
        return name, line
    return None, text.strip().strip(_QUOTES).strip()


async def _tts_to_file(text, output_path, voice, rate="+0%", volume="+0%"):
    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    await communicate.save(output_path)


def _synth(text, output_path, voice, rate="+0%", volume="+0%"):
    """合成单段语音，成功返回路径，失败返回 None。"""
    if not text or not text.strip():
        return None
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    try:
        asyncio.run(_tts_to_file(text, output_path, voice, rate, volume))
        return output_path if os.path.exists(output_path) else None
    except Exception as e:
        logger.warning(f"TTS生成失败（网络不可达?）: {e}")
        return None


def _concat_audio(parts, output_path):
    """把多段 mp3 拼成一条。单段直接改名；多段用 ffmpeg concat。"""
    parts = [p for p in parts if p and os.path.exists(p)]
    if not parts:
        return None
    if len(parts) == 1:
        if parts[0] != output_path:
            try:
                os.replace(parts[0], output_path)
            except OSError:
                import shutil
                shutil.copy2(parts[0], output_path)
        return output_path

    list_file = output_path + ".parts.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in parts:
            f.write("file '%s'\n" % os.path.abspath(p).replace("\\", "/"))
    try:
        subprocess.run(
            [FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", list_file,
             "-c", "copy", output_path],
            capture_output=True, text=True, timeout=120, check=True,
        )
    except Exception:
        try:
            subprocess.run(
                [FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", list_file,
                 "-c:a", "libmp3lame", output_path],
                capture_output=True, text=True, timeout=180, check=True,
            )
        except Exception as e:
            logger.warning(f"配音拼接失败: {e}")
            output_path = None
    finally:
        try:
            os.remove(list_file)
        except OSError:
            pass
        for p in parts:
            if p != output_path:
                try:
                    os.remove(p)
                except OSError:
                    pass
    return output_path


def generate_narration(text, output_path, voice=None, rate="+0%", volume="+0%"):
    """生成单段旁白配音（兼容旧接口）。失败返回 None。"""
    voice = voice or DEFAULT_NARRATION_VOICE
    return _synth(text, output_path, voice, rate, volume)


def generate_scene_narrations(scenes, output_dir,
                              voice=None, dialogue_voice=None,
                              rate="+0%", progress_callback=None):
    """为所有场景生成配音：旁白用 voice，对白用 dialogue_voice。

    TTS 不可用时静默降级（后续场景跳过），影片仍可生成（无配音）。

    Args:
        scenes: 场景列表
        output_dir: 输出目录
        voice: 旁白语音（默认 config.DEFAULT_NARRATION_VOICE）
        dialogue_voice: 对白语音（默认 config.DEFAULT_DIALOGUE_VOICE）
        rate: 语速
        progress_callback: 回调 (current, total, message)

    Returns:
        list[dict]: 场景列表（新增 audio_path，可能为 None）
    """
    voice = voice or DEFAULT_NARRATION_VOICE
    dialogue_voice = dialogue_voice or DEFAULT_DIALOGUE_VOICE
    total = len(scenes)
    os.makedirs(output_dir, exist_ok=True)
    tts_available = True

    for i, scene in enumerate(scenes):
        scene_out = os.path.join(output_dir, f"narration_{i + 1:04d}.mp3")

        if progress_callback:
            progress_callback(i, total, f"生成配音 {i + 1}/{total}")

        if not tts_available:
            scene["audio_path"] = None
            continue

        # 组装分段：旁白（narration 声音）+ 各条台词（对白声音，去掉角色名）
        seg_specs = []
        if scene.get("narration"):
            seg_specs.append(("narration", scene["narration"].strip()))
        for d in scene.get("dialogue", []):
            _, line = split_speaker(d)
            if line:
                seg_specs.append(("dialogue", line))

        if not seg_specs:
            # 没有任何可读文本，跳过但不判定 TTS 故障
            scene["audio_path"] = None
            if progress_callback:
                progress_callback(i + 1, total, f"场景 {i + 1} 无旁白，跳过")
            continue

        part_paths = []
        any_ok = False
        for j, (kind, txt) in enumerate(seg_specs):
            v = voice if kind == "narration" else dialogue_voice
            part = os.path.join(output_dir, f"n{i + 1:04d}_{j:02d}.mp3")
            res = _synth(txt, part, v, rate)
            if res:
                any_ok = True
                part_paths.append(res)
            else:
                # 第一段就失败通常意味着 TTS 服务不可达 → 整体降级
                tts_available = False
                break

        if not any_ok:
            scene["audio_path"] = None
            if progress_callback:
                progress_callback(i + 1, total, "TTS服务不可用，跳过配音")
            continue

        merged = _concat_audio(part_paths, scene_out)
        scene["audio_path"] = merged
        if progress_callback and tts_available:
            progress_callback(i + 1, total, f"配音完成 {i + 1}/{total}")

    if not tts_available and progress_callback:
        progress_callback(total, total, "TTS不可用（网络限制），影片将无配音")

    return scenes


def get_audio_duration(audio_path):
    """获取音频时长（秒）。"""
    try:
        result = subprocess.run(
            [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except Exception:
        return 5.0
