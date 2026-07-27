# -*- coding: utf-8 -*-
"""项目级视觉提示词约束，减少中国历史场景被生成成西方背景。"""

CHINESE_CULTURAL_ACCURACY = (
    "CULTURAL ACCURACY: This is a Chinese historical story scene. Unless the source "
    "explicitly identifies a foreign character, every human character must be Chinese, "
    "with natural Chinese/East Asian facial features, black hair, and historically "
    "appropriate Chinese grooming. Clothing, hairstyles, headwear, armor, weapons, "
    "furniture, architecture, streets and landscape must match the Chinese dynasty and "
    "region described by the source. If the dynasty is unknown, use historically plausible "
    "traditional Han Chinese design. Preserve each character's age, gender, identity and "
    "role. Do not replace the setting with European medieval, Roman, Viking, Middle Eastern, "
    "Japanese or Korean people, costumes, armor or buildings. Natural facial proportions, "
    "respectful non-caricatured depiction."
)

CHINESE_VISUAL_NEGATIVE_EN = (
    "unrequested Caucasian or European people, blond hair, blue eyes, Western medieval "
    "knights, plate armor, European castles, Roman clothing, Viking clothing, Japanese "
    "samurai, kimono, Korean hanbok, modern Western clothing, culturally incorrect "
    "architecture, mixed historical eras"
)

CHINESE_VISUAL_NEGATIVE_CN = (
    "非原作要求的欧美人物、金发碧眼、西方中世纪骑士、欧式板甲、欧洲城堡、"
    "古罗马服饰、维京服饰、日本武士、和服、韩服、现代西式服装、错误朝代建筑、时代混搭"
)


def optimize_chinese_visual_prompt(prompt):
    """为场景提示词追加可识别且幂等的中国历史文化约束。"""
    cleaned = " ".join(str(prompt or "").split()).strip()
    if not cleaned:
        cleaned = "A cinematic Chinese historical story scene."
    if "CULTURAL ACCURACY:" in cleaned:
        return cleaned
    return f"{cleaned} {CHINESE_CULTURAL_ACCURACY}"
