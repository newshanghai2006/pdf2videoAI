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
