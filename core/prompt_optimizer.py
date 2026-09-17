# -*- coding: utf-8 -*-
"""项目级视觉提示词约束，保持中国题材的年代与文化准确性。"""

CHINESE_CULTURAL_ACCURACY = (
    "CULTURAL AND PERIOD ACCURACY: Preserve the exact country, nationality, time period, "
    "location and political or military context stated or implied by the source. A Chinese "
    "story is not necessarily ancient. Never invent a dynasty or convert a modern or "
    "twentieth-century event into an ancient costume drama. Clothing, hairstyles, uniforms, "
    "insignia, equipment, vehicles, weapons, furniture, architecture and landscape must "
    "match the identified year and region. Chinese characters must have natural Chinese/East "
    "Asian facial features; explicitly foreign characters must retain their actual nationality "
    "and appearance. Preserve each character's age, gender, identity and role. Keep recurring "
    "characters visually identical across every scene: the same facial structure, skin tone, "
    "age, hairstyle, body build, signature clothing colors, accessories and distinguishing marks. "
    "Only change clothing, injuries or age when the source explicitly describes that change. Do not replace "
    "people or settings with an unrelated culture or era. Natural facial proportions, "
    "respectful non-caricatured depiction."
)

CHINESE_VISUAL_NEGATIVE_EN = (
    "wrong nationality, changed ethnicity, anachronistic clothing, incorrect military "
    "uniforms or insignia, unrelated architecture, invented dynasty, mixed historical eras, "
    "modern event depicted as ancient costume drama, inconsistent face, identity drift, face swap, "
    "changing age, changing hairstyle, changing costume colors, duplicate character"
)

CHINESE_VISUAL_NEGATIVE_CN = (
    "错误国籍、改变人物族群、年代错误的服装、错误军服或徽记、无关建筑、"
    "虚构朝代、时代混搭、把现代事件画成古装戏、人物换脸、身份漂移、年龄变化、"
    "发型变化、服装颜色变化、重复人物"
)

CHARACTER_CONTINUITY_RULE = (
    "CHARACTER CONTINUITY: Treat every named recurring person as the same actor across all shots. "
    "Obey the supplied identity locks exactly. Preserve facial geometry, apparent age, skin tone, "
    "hairstyle, body build, signature wardrobe colors, insignia, accessories and handed props. "
    "Do not merge two people, duplicate one person, swap faces, beautify beyond recognition or "
    "redesign a character between scenes."
)


MODERN_PERIOD_ACCURACY = (
    " MODERN PERIOD ACCURACY: This is a modern or twentieth-century scene. Use the exact "
    "period's uniforms, practical hairstyles, equipment, vehicles, buildings and terrain. "
    "Do not show ancient robes, imperial armor, swords, spears, palaces or premodern troops. "
    "For the 1979 Sino-Vietnamese border war, clearly distinguish period-accurate Chinese "
    "People's Liberation Army personnel from Vietnamese personnel without changing either "
    "side into Western soldiers or ancient warriors."
)

HISTORICAL_PERIOD_ACCURACY = (
    " HISTORICAL PERIOD ACCURACY: This is a premodern historical scene. Match the specific "
    "Chinese dynasty, region, clothing, grooming, architecture and material culture stated "
    "by the source; do not introduce modern uniforms, vehicles or buildings."
)

_MODERN_MARKERS = (
    "modern", "twentieth-century", "20th-century", "1979", "1980", "1990", "2000",
    "sino-vietnam", "vietnamese border", "people's liberation army", "pla soldier",
    "type 65 uniform", "type 56 rifle", "对越自卫反击战", "解放军", "越南战争",
)

_HISTORICAL_MARKERS = (
    "ancient", "dynasty", "imperial china", "emperor", "traditional han chinese",
    "qin dynasty", "han dynasty", "tang dynasty", "song dynasty", "yuan dynasty",
    "ming dynasty", "qing dynasty", "古代", "朝代", "皇帝",
)


def optimize_chinese_visual_prompt(prompt):
    """为场景提示词追加幂等的中国题材文化与年代约束。"""
    cleaned = " ".join(str(prompt or "").split()).strip()
    if not cleaned:
        cleaned = "A cinematic Chinese story scene with the source period preserved exactly."
    lowered = cleaned.lower()
    period_rule = ""
    if any(marker in lowered for marker in _MODERN_MARKERS):
        period_rule = MODERN_PERIOD_ACCURACY
    elif any(marker in lowered for marker in _HISTORICAL_MARKERS):
        period_rule = HISTORICAL_PERIOD_ACCURACY
    additions = []
    if "CULTURAL AND PERIOD ACCURACY:" not in cleaned:
        additions.append(CHINESE_CULTURAL_ACCURACY + period_rule)
    if "CHARACTER CONTINUITY:" not in cleaned:
        additions.append(CHARACTER_CONTINUITY_RULE)
    return " ".join([cleaned, *additions]).strip()


def _compact(value):
    return " ".join(str(value or "").split()).strip()


def build_character_identity_lock(character_bible, character_names=None,
                                  max_characters=4):
    """Build a compact reusable identity block for image/video prompts."""
    entries = [item for item in (character_bible or []) if isinstance(item, dict)]
    if isinstance(character_names, str):
        character_names = [character_names]
    requested = {_compact(name).casefold() for name in (character_names or []) if _compact(name)}
    if requested:
        selected = []
        for item in entries:
            labels = [_compact(item.get("name"))]
            aliases = item.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            labels.extend(_compact(alias) for alias in aliases)
            if any(label.casefold() in requested for label in labels if label):
                selected.append(item)
    else:
        selected = entries[:max_characters]

    locks = []
    for item in selected[:max_characters]:
        name = _compact(item.get("name") or item.get("character"))
        if not name:
            continue
        details = []
        for key in (
            "identity", "age", "gender", "nationality_ethnicity", "facial_features",
            "hair", "body_build", "signature_costume", "signature_colors",
            "accessories_props", "appearance_prompt", "consistency_prompt",
        ):
            value = _compact(item.get(key))
            if value and value not in details:
                details.append(value)
        description = "; ".join(details)
        if len(description) > 520:
            description = description[:517].rsplit(" ", 1)[0] + "..."
        locks.append(f"{name}: {description}" if description else name)
    if not locks:
        return ""
    return (
        "CHARACTER IDENTITY LOCK (repeat exactly in every relevant shot; do not redesign): "
        + " | ".join(locks)
        + "."
    )


def add_character_identity_lock(prompt, identity_lock):
    """Append an identity lock once without changing the scene's main description."""
    cleaned = _compact(prompt)
    lock = _compact(identity_lock)
    if not lock or "CHARACTER IDENTITY LOCK" in cleaned:
        return cleaned
    return f"{cleaned} {lock}".strip()


def apply_character_identity_locks(story):
    """Attach deterministic character locks to every applicable story scene."""
    if not isinstance(story, dict):
        return story
    bible = [item for item in (story.get("character_bible") or [])
             if isinstance(item, dict)]
    for scene in story.get("scenes") or []:
        names = scene.get("characters_present") or []
        if isinstance(names, str):
            names = [names]
        names = [_compact(name) for name in names if _compact(name)]
        if not names:
            searchable = " ".join([
                _compact(scene.get("narration")),
                _compact(scene.get("image_prompt")),
                *[_compact(line) for line in (scene.get("dialogue") or [])],
            ]).casefold()
            for item in bible:
                labels = [_compact(item.get("name"))]
                aliases = item.get("aliases") or []
                if isinstance(aliases, str):
                    aliases = [aliases]
                labels.extend(_compact(alias) for alias in aliases)
                if any(label and label.casefold() in searchable for label in labels):
                    names.append(_compact(item.get("name")))
        scene["characters_present"] = names
        lock = build_character_identity_lock(bible, names) if names else ""
        scene["character_identity_lock"] = lock
        scene["image_prompt"] = add_character_identity_lock(
            scene.get("image_prompt", ""), lock)
    return story
