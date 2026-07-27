# -*- coding: utf-8 -*-
"""外部服务调用频率限制。"""
import threading
import time


class IntervalRateLimiter:
    """通过最小请求间隔限制整个进程的调用频率。"""

    def __init__(self, requests_per_minute, safety_seconds=0.05):
        self.interval = 60.0 / float(requests_per_minute) + safety_seconds
        self._lock = threading.Lock()
        self._last_request = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delay = self.interval - (now - self._last_request)
            if delay > 0:
                time.sleep(delay)
            self._last_request = time.monotonic()


# NVIDIA 免费 API 限制为 40 RPM。LLM 与图像请求共用额度，保留 0.05 秒余量。
nvidia_limiter = IntervalRateLimiter(40, safety_seconds=0.05)

# Agnes AI 免费/default Key 的当前公开“实际 RPM”。不同能力和图片档位
# 使用不同额度池；同一进程中的并发任务共享这些 limiter。
agnes_text_limiter = IntervalRateLimiter(20, safety_seconds=0.10)
agnes_image_limiters = {
    "1K": IntervalRateLimiter(20, safety_seconds=0.10),
    "2K": IntervalRateLimiter(10, safety_seconds=0.10),
    "3K": IntervalRateLimiter(1, safety_seconds=0.25),
    "4K": IntervalRateLimiter(1, safety_seconds=0.25),
}
agnes_video_limiter = IntervalRateLimiter(1, safety_seconds=0.25)


def get_agnes_image_limiter(size_tier):
    """返回 Agnes 图片档位对应的免费 RPM 限速器。"""
    return agnes_image_limiters.get(str(size_tier or "1K").upper(),
                                    agnes_image_limiters["1K"])
