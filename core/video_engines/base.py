# -*- coding: utf-8 -*-
"""视频引擎基类。"""


class VideoEngine:
    """视频引擎接口。

    子类实现 generate_clip：把单个场景渲染成一段**无声**、时长**恰好 duration 秒**、
    尺寸 width×height 的视频片段（H.264 / yuv420p）。音频对齐与拼接由上层统一处理，
    引擎不需要关心配音与 BGM。
    """

    # 引擎标识，供日志/错误信息使用
    name = "base"

    def __init__(self, **_ignored):
        # 默认接受并忽略额外构造参数，使 get_engine(**engine_opts) 对所有引擎通用。
        pass

    def generate_clip(self, scene, out_path, width, height, duration,
                      fade_in=False, fade_out=False, index=0, total=1,
                      progress_callback=None):
        """渲染单个场景片段。

        Args:
            scene: 场景字典（含 image_path / image_prompt / mood 等）
            out_path: 输出 mp4 路径
            width, height: 目标分辨率
            duration: 片段时长（秒），必须精确匹配以保证音画同步
            fade_in / fade_out: 是否在片段首/尾做淡入淡出
            index, total: 当前场景序号 / 总数（用于运镜变化或进度）
            progress_callback: 可选回调 (message)

        Returns:
            str: out_path
        """
        raise NotImplementedError
