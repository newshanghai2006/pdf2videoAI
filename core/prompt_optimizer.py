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
    "and appearance. Preserve each character's age, gender, identity and role. Do not replace "
    "people or settings with an unrelated culture or era. Natural facial proportions, "
    "respectful non-caricatured depiction."
)

CHINESE_VISUAL_NEGATIVE_EN = (
    "wrong nationality, changed ethnicity, anachronistic clothing, incorrect military "
    "uniforms or insignia, unrelated architecture, invented dynasty, mixed historical eras, "
    "modern event depicted as ancient costume drama"
)

CHINESE_VISUAL_NEGATIVE_CN = (
    "错误国籍、改变人物族群、年代错误的服装、错误军服或徽记、无关建筑、"
    "虚构朝代、时代混搭、把现代事件画成古装戏"
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
    if "CULTURAL AND PERIOD ACCURACY:" in cleaned:
        return cleaned
    lowered = cleaned.lower()
    period_rule = ""
    if any(marker in lowered for marker in _MODERN_MARKERS):
        period_rule = MODERN_PERIOD_ACCURACY
    elif any(marker in lowered for marker in _HISTORICAL_MARKERS):
        period_rule = HISTORICAL_PERIOD_ACCURACY
    return f"{cleaned} {CHINESE_CULTURAL_ACCURACY}{period_rule}"
