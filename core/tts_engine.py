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
import base64
import logging
import shutil
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


def _synth_windows_sapi(text, output_path, rate="+0%"):
    """Use an installed Windows Speech voice when edge-tts is unreachable."""
    if os.name != "nt" or not text or not text.strip():
        return None
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return None
    try:
        rate_value = int(re.search(r"[-+]?\d+", str(rate)).group(0))
    except (AttributeError, TypeError, ValueError):
        rate_value = 0
    rate_value = max(-10, min(10, round(rate_value / 10)))
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    sapi_path = output_path if output_path.lower().endswith(".wav") else output_path + ".sapi.wav"
    env = os.environ.copy()
    env["PDF2VIDEO_SAPI_TEXT"] = base64.b64encode(text.encode("utf-16-le")).decode("ascii")
    env["PDF2VIDEO_SAPI_PATH"] = base64.b64encode(
        os.path.abspath(sapi_path).encode("utf-16-le")
    ).decode("ascii")
    env["PDF2VIDEO_SAPI_RATE"] = str(rate_value)
    script = r'''
Add-Type -AssemblyName System.Speech
$text = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($env:PDF2VIDEO_SAPI_TEXT))
$path = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($env:PDF2VIDEO_SAPI_PATH))
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $synth.GetInstalledVoices() | Where-Object {
    $_.VoiceInfo.Name -match 'Huihui|Chinese'
} | Select-Object -First 1
if ($null -eq $voice) { throw 'No Chinese Windows SAPI voice installed' }
$synth.SelectVoice($voice.VoiceInfo.Name)
$synth.Rate = [int]$env:PDF2VIDEO_SAPI_RATE
$synth.SetOutputToWaveFile($path)
$synth.Speak($text)
$synth.SetOutputToNull()
$synth.Dispose()
'''
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            capture_output=True, text=True, timeout=90, env=env,
        )
        if result.returncode == 0 and os.path.exists(sapi_path) and os.path.getsize(sapi_path) > 512:
            if sapi_path != output_path:
                os.replace(sapi_path, output_path)
            return output_path
        logger.warning("Windows 本地 TTS 失败: %s", (result.stderr or result.stdout).strip()[-500:])
    except Exception as error:
        logger.warning("Windows 本地 TTS 调用失败: %s", error)
    return None


def _synth(text, output_path, voice, rate="+0%", volume="+0%"):
    """合成单段语音，成功返回路径，失败返回 None。"""
    if not text or not text.strip():
        return None
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    edge_error = None
    try:
        asyncio.run(_tts_to_file(text, output_path, voice, rate, volume))
        if os.path.exists(output_path) and os.path.getsize(output_path) > 512:
            return output_path
    except Exception as e:
        edge_error = e

    # edge-tts requires access to Microsoft's online speech service. On
    # Windows, fall back to an installed local Chinese SAPI voice so a network
    # outage does not silently produce a completely silent film.
    local_path = _synth_windows_sapi(text, output_path, rate)
    if local_path:
        logger.warning("在线 edge-tts 不可用，已改用 Windows 本地中文语音")
        return local_path
    logger.warning(
        "TTS生成失败：在线 edge-tts 需要访问 speech.platform.bing.com；"
        "本机 Windows 中文语音回退也不可用。原始错误: %s", edge_error,
    )
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
    success_count = 0
    failure_count = 0

    for i, scene in enumerate(scenes):
        scene_out = os.path.join(output_dir, f"narration_{i + 1:04d}.mp3")

        if progress_callback:
            progress_callback(i, total, f"生成配音 {i + 1}/{total}")

        # 组装分段：旁白（narration 声音）+ 各条台词（对白声音，去掉角色名）
        seg_specs = []
        if scene.get("narration"):
            narration = str(scene["narration"]).replace('\r', '').replace('\n', '').strip()
            scene['narration'] = narration
            seg_specs.append(("narration", narration))
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
            res = None
            for attempt in range(3):
                res = _synth(txt, part, v, rate)
                if res:
                    break
                if attempt < 2:
                    import time
                    time.sleep(1.5)
            if res:
                any_ok = True
                part_paths.append(res)
            else:
                # 第一段就失败通常意味着 TTS 服务不可达 → 整体降级
                failure_count += 1
                break

        if not any_ok:
            scene["audio_path"] = None
            if progress_callback:
                progress_callback(i + 1, total, "TTS服务不可用，跳过配音")
            continue

        merged = _concat_audio(part_paths, scene_out)
        scene["audio_path"] = merged
        if merged:
            success_count += 1
        else:
            failure_count += 1
        if progress_callback and merged:
            progress_callback(i + 1, total, f"配音完成 {i + 1}/{total}")

    if progress_callback and failure_count:
        progress_callback(total, total, f"TTS完成：{success_count} 个场景成功，{failure_count} 个场景无配音")

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
