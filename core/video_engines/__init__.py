# -*- coding: utf-8 -*-
"""视频引擎抽象层。

每个引擎负责「把一个场景变成一段固定时长的无声视频片段」。上层的
video_builder 负责统一的音轨对齐、拼接与 BGM 混音，与具体引擎无关。

内置引擎：
  - kenburns：本地图像 + 缓动运镜（默认，无需额外 API）
  - seedance：火山引擎 Seedance 文生/图生视频（预留占位，接入后即可用）

新增引擎：在本目录新建模块实现 VideoEngine.generate_clip，并在 _REGISTRY 登记。
"""
from .base import VideoEngine
from .agnes import AgnesVideoEngine
from .kenburns import KenBurnsEngine
from .seedance import SeedanceEngine
from .static import StaticEngine

# 引擎名 → 类
_REGISTRY = {
    "agnes": AgnesVideoEngine,
    "kenburns": KenBurnsEngine,
    "static": StaticEngine,
    "seedance": SeedanceEngine,
}


def get_engine(name, **opts):
    """按名称获取引擎实例；未知名称回退到 kenburns。"""
    cls = _REGISTRY.get((name or "kenburns").lower(), KenBurnsEngine)
    return cls(**opts)


__all__ = ["VideoEngine", "get_engine"]
