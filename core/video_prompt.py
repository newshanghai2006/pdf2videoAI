# -*- coding: utf-8 -*-
"""视频生成提示词构建器（本地拼装，不调用任何 API）。

把剧情分析阶段已产出的场景字段（image_prompt/narration/mood/duration/dialogue）
组织成可**直接粘贴进网页版 AI 视频工具**（火山 Seedance、即梦、Runway、Pika、
可灵等）的提示词。中英双语 + 镜头运动 + 时长建议 + 负向词。

输出两种形态：
  - build_scene_prompt(scene, ...) -> dict：单场景结构化提示词
  - build_prompts_document(scenes, ...) -> str：整篇可直接复制的文本文档
"""

# mood -> (中文氛围, 英文 atmosphere)
_MOOD_MAP = {
    "tense":      ("紧张", "tense, suspenseful atmosphere"),
    "calm":       ("平静", "calm, peaceful atmosphere"),
    "heroic":     ("英勇", "heroic, majestic atmosphere"),
    "tragic":     ("悲壮", "tragic, sorrowful atmosphere"),
    "joyful":     ("欢快", "joyful, cheerful atmosphere"),
    "mysterious": ("神秘", "mysterious, enigmatic atmosphere"),
    "epic":       ("史诗", "epic, grand cinematic atmosphere"),
}

# 按 mood 建议的镜头运动（中文 / 英文）
_CAMERA_MAP = {
    "tense":      ("缓慢推近，手持轻微晃动", "slow push-in, subtle handheld shake"),
    "calm":       ("平稳横移，轻微推近", "gentle lateral pan, slight push-in"),
    "heroic":     ("低角度仰拍，缓慢上升", "low-angle shot, slow crane-up"),
    "tragic":     ("缓慢拉远，静止长镜", "slow pull-back, static long take"),
    "joyful":     ("轻快环绕，跟随移动", "light orbiting move, follow shot"),
    "mysterious": ("缓慢横摇，逐渐揭示", "slow pan revealing the scene"),
    "epic":       ("大范围推轨，广角展开", "sweeping dolly, wide establishing move"),
}

_DEFAULT_CAMERA = ("平稳推近", "smooth slow push-in")
_DEFAULT_MOOD = ("电影感", "cinematic atmosphere")

# 通用负向词（画质/畸变类），适配大多数视频工具
_NEGATIVE = (
    "低画质, 模糊, 变形, 多余的手指, 畸变肢体, 文字水印, 字幕, LOGO, "
    "闪烁, 拼接痕迹, 过曝, 噪点"
)
_NEGATIVE_EN = (
    "low quality, blurry, distorted, extra fingers, deformed limbs, "
    "text, watermark, subtitles, logo, flicker, artifacts, overexposed, noise"
)


def _clean(s):
    return (s or "").strip()


def build_scene_prompt(scene, art_style_desc="", index=0):
    """为单个场景构建视频提示词结构。

    Returns dict:
      { scene_number, duration, mood_cn, camera_cn, camera_en,
        prompt_cn, prompt_en, negative_cn, negative_en }
    """
    mood_key = _clean(scene.get("mood")).lower()
    mood_cn, mood_en = _MOOD_MAP.get(mood_key, _DEFAULT_MOOD)
    cam_cn, cam_en = _CAMERA_MAP.get(mood_key, _DEFAULT_CAMERA)

    duration = scene.get("duration", 5) or 5
    scene_no = scene.get("scene_number", index + 1)

    narration = _clean(scene.get("narration"))
    image_prompt = _clean(scene.get("image_prompt"))

    # 台词（去掉说话人，只保留内容会更适合画面描述；此处保留原文供参考）
    dialogues = [d for d in (scene.get("dialogue") or []) if _clean(d)]

    # ---- 中文提示词 ----
    cn_parts = []
    if narration:
        cn_parts.append(narration)
    if art_style_desc:
        cn_parts.append(f"画风：{art_style_desc}")
    cn_parts.append(f"镜头：{cam_cn}")
    cn_parts.append(f"氛围：{mood_cn}")
    cn_parts.append("高清，电影质感，稳定连贯的动态")
    prompt_cn = "，".join(cn_parts)

    # ---- 英文提示词 ----
    en_parts = []
    if image_prompt:
        en_parts.append(image_prompt)
    en_parts.append(f"Camera: {cam_en}")
    en_parts.append(mood_en)
    en_parts.append("high definition, cinematic quality, smooth coherent motion")
    prompt_en = ". ".join(en_parts)

    return {
        "scene_number": scene_no,
        "duration": duration,
        "mood_cn": mood_cn,
        "camera_cn": cam_cn,
        "camera_en": cam_en,
        "dialogues": dialogues,
        "prompt_cn": prompt_cn,
        "prompt_en": prompt_en,
        "negative_cn": _NEGATIVE,
        "negative_en": _NEGATIVE_EN,
    }


def build_all_scene_prompts(scenes, art_style_desc=""):
    """为所有场景构建提示词结构列表。"""
    return [build_scene_prompt(s, art_style_desc, i) for i, s in enumerate(scenes)]


def build_prompts_document(scenes, title="", art_style_desc="", engine_hint=""):
    """构建可直接复制粘贴的整篇提示词文档（纯文本）。

    Args:
        scenes: 场景列表
        title: 故事标题（可选）
        art_style_desc: 艺术风格中文描述（可选）
        engine_hint: 目标工具提示（如 '火山 Seedance / 即梦'），仅用于文档抬头

    Returns:
        str
    """
    prompts = build_all_scene_prompts(scenes, art_style_desc)
    lines = []
    lines.append("=" * 60)
    lines.append(f"AI 视频生成提示词{('：' + title) if title else ''}")
    lines.append("用法：把每个场景的提示词逐条粘贴到网页版 AI 视频工具"
                 f"{('（' + engine_hint + '）') if engine_hint else ''}的输入框。")
    lines.append(f"共 {len(prompts)} 个场景。")
    lines.append("=" * 60)
    lines.append("")

    for p in prompts:
        lines.append(f"────── 场景 {p['scene_number']}（建议时长 {p['duration']}s）──────")
        lines.append(f"【中文提示词】{p['prompt_cn']}")
        lines.append(f"【English Prompt】{p['prompt_en']}")
        lines.append(f"【镜头 Camera】{p['camera_cn']} / {p['camera_en']}")
        if p["dialogues"]:
            lines.append("【台词参考】" + " ｜ ".join(p["dialogues"]))
        lines.append(f"【负向词 Negative】{p['negative_cn']}")
        lines.append(f"【Negative (EN)】{p['negative_en']}")
        lines.append("")

    return "\n".join(lines)
