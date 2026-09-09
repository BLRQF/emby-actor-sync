"""限速下载 TMDB 演员原图（并发安全 + 自适应速率）。"""

import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# 注意：不要发送 image/avif、image/webp 之类的 Accept 头，TMDB 会据此
# 返回 WebP/AVIF 格式；不发送时默认返回原始 JPEG，Emby 需要 folder.jpg
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) emby-actor-sync/0.1",
}
_JPEG_MAGIC = b"\xff\xd8"
_PNG_MAGIC = b"\x89PNG"


class RateLimiter:
    """全局自适应限速器。

    多线程共享：每次请求前排队领取时隙；
    成功一次速率 +3%，逐步提速；429 速率减半；其它错误小幅降速。
    """

    def __init__(self, rate: float, max_rate: float, min_rate: float = 0.5):
        self._lock = threading.Lock()
        self.rate = max(min_rate, rate)
        self.max_rate = max(self.rate, max_rate)
        self.min_rate = min_rate
        self._next = 0.0  # 下一个可用时隙的时间点

    @property
    def interval(self) -> float:
        return 1.0 / self.rate

    def pace(self) -> None:
        """等待直到获得一个请求时隙（线程安全）。"""
        with self._lock:
            wait = self._next - time.monotonic()
            self._next = max(time.monotonic(), self._next) + self.interval
        if wait > 0:
            time.sleep(wait)

    def on_success(self) -> None:
        with self._lock:
            self.rate = min(self.max_rate, self.rate * 1.03)

    def on_limited(self) -> None:
        with self._lock:
            self.rate = max(self.min_rate, self.rate * 0.5)

    def on_error(self) -> None:
        with self._lock:
            self.rate = max(self.min_rate, self.rate - 0.5)


class Downloader:
    """按限速器下载，失败自动重试，成功后原子替换目标文件。"""

    def __init__(self, rate: float = 2.0, max_rate: float = 8.0,
                 retries: int = 3, timeout: float = 30.0):
        self.limiter = RateLimiter(rate, max_rate)
        self.retries = max(1, retries)
        self.timeout = timeout

    def download(self, url: str, dest: Path) -> int:
        """下载 url 到 dest（folder.jpg），返回字节数；失败抛出异常。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name("folder.jpg.part")
        last_err: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self.limiter.pace()
            try:
                req = urllib.request.Request(url, headers=_HEADERS)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = resp.read()
                if not data:
                    raise RuntimeError("响应为空")
                if not (data.startswith(_JPEG_MAGIC) or data.startswith(_PNG_MAGIC)):
                    raise RuntimeError(f"响应不是图片（{len(data)} 字节）")
                tmp.write_bytes(data)
                os.replace(tmp, dest)
                self.limiter.on_success()
                return len(data)
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 429:  # 触发限流：全局减速并等待后重试
                    self.limiter.on_limited()
                    try:
                        retry_after = min(float(e.headers.get("Retry-After", 10)), 60)
                    except (TypeError, ValueError):
                        retry_after = 10
                    time.sleep(retry_after)
                else:
                    self.limiter.on_error()
                    time.sleep(2 * attempt)
            except Exception as e:
                last_err = e
                self.limiter.on_error()
                time.sleep(2 * attempt)
            finally:
                tmp.unlink(missing_ok=True)
        assert last_err is not None
        raise last_err
