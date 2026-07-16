# -*- coding: utf-8 -*-
"""火山引擎 Seedance 视频引擎（预留占位）。

Seedance 是火山引擎（Volcengine / 豆包）的文生视频 / 图生视频模型，可直接生成
真正动态的视频片段。此处提供接入骨架：一旦填入真实 API 调用即可启用。

接入方式（未来）：
  1. 在环境变量或前端提供 SEEDANCE_API_KEY（火山 ARK/视觉智能的 Key）。
  2. 在 _submit_task / _poll_task 中实现火山的「提交任务 → 轮询 → 取结果视频 URL」。
  3. 下载生成的视频后，用 ffmpeg 规整到目标 width×height 与 duration。

在真正实现前，本引擎默认**自动降级**到 Ken Burns，保证流程不中断；
若显式要求严格模式（strict=True）则抛出未实现错误。
"""
import os

from .base import VideoEngine
from .kenburns import KenBurnsEngine


class SeedanceEngine(VideoEngine):
    name = "seedance"

    def __init__(self, api_key=None, base_url=None, model=None,
                 strict=False, **_ignored):
        # 火山 API 凭据（预留）
        self.api_key = (api_key or os.environ.get("SEEDANCE_API_KEY", "")).strip()
        self.base_url = (base_url or os.environ.get("SEEDANCE_BASE_URL", "")).strip()
        self.model = (model or os.environ.get("SEEDANCE_MODEL", "seedance")).strip()
        # strict=True 时不降级，直接报错（便于联调阶段暴露问题）
        self.strict = strict
        self._fallback = KenBurnsEngine()

    # ---- 下面两个方法是接入火山时需要真正实现的扩展点 ----
    def _submit_task(self, scene, width, height, duration):
        """提交一个文生/图生视频任务，返回 task_id。（待实现）"""
        raise NotImplementedError

    def _poll_task(self, task_id, timeout=600):
        """轮询任务直到完成，返回生成视频的下载 URL。（待实现）"""
        raise NotImplementedError

    def generate_clip(self, scene, out_path, width, height, duration,
                      fade_in=False, fade_out=False, index=0, total=1,
                      progress_callback=None):
        # 已配置凭据且实现了真实调用时，走真实 Seedance 流程。
        if self.api_key:
            try:
                # task_id = self._submit_task(scene, width, height, duration)
                # video_url = self._poll_task(task_id)
                # _download_and_normalize(video_url, out_path, width, height, duration)
                # return out_path
                raise NotImplementedError("Seedance 真实调用尚未实现")
            except NotImplementedError:
                if self.strict:
                    raise
                if progress_callback:
                    progress_callback("Seedance 尚未接入，本场景降级为 Ken Burns")
        else:
            if self.strict:
                raise RuntimeError(
                    "选择了 Seedance 引擎但未提供火山 API Key（SEEDANCE_API_KEY）")
            if progress_callback:
                progress_callback("未配置火山 Key，本场景使用 Ken Burns 合成")

        # 降级：用 Ken Burns 生成，保证片段时长严格等于 duration（音画同步不受影响）
        return self._fallback.generate_clip(
            scene, out_path, width, height, duration,
            fade_in=fade_in, fade_out=fade_out,
            index=index, total=total, progress_callback=progress_callback,
        )
