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


# SenseNova 当前公开模型额度按 5 小时窗口计算。这里按窗口平均值均匀发送，
# 避免批量 PDF 分析在短时间内集中消耗请求。实际账户窗口由服务端最终判定。
sensenova_llm_limiters = {
    "sensenova-6.7-flash-lite": IntervalRateLimiter(5, safety_seconds=0.20),
    "sensenova-u1-fast": IntervalRateLimiter(5, safety_seconds=0.20),
    "deepseek-v4-flash": IntervalRateLimiter(500 / 300, safety_seconds=0.20),
}


def get_sensenova_llm_limiter(model, is_sensenova_provider=False):
    """返回 SenseNova 模型限速器；本地同名 DeepSeek 模型不误限速。"""
    name = str(model or "").strip().lower()
    if name.startswith("sensenova-"):
        return sensenova_llm_limiters.get(name)
    if is_sensenova_provider:
        return sensenova_llm_limiters.get(name)
    return None
