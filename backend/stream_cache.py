"""
302 流播放优化 - 连接池 + URL 缓存 + 预获取
参考 SmartStrm 的高性能 302 策略
"""
import asyncio
import hashlib
import time
import threading
from typing import Optional, Dict
from dataclasses import dataclass, field
from collections import OrderedDict
from collections.abc import MutableMapping


class LRUCache(MutableMapping):
    """线程安全的 LRU 缓存"""

    def __init__(self, maxsize: int = 1000):
        self.maxsize = maxsize
        self._cache: OrderedDict = OrderedDict()
        self._lock = threading.RLock()
        self._expiry: Dict[str, float] = {}

    def __setitem__(self, key: str, value):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self.maxsize:
                    oldest = next(iter(self._cache))
                    del self._cache[oldest]
                    self._expiry.pop(oldest, None)
                self._cache[key] = value
            # 默认 5 分钟过期
            self._expiry[key] = time.time() + 300

    def __getitem__(self, key: str, default=None):
        with self._lock:
            if key not in self._cache:
                return default
            # 检查过期
            if key in self._expiry and time.time() > self._expiry[key]:
                del self._cache[key]
                self._expiry.pop(key, None)
                return default
            self._cache.move_to_end(key)
            return self._cache[key]

    def __delitem__(self, key: str):
        with self._lock:
            del self._cache[key]
            self._expiry.pop(key, None)

    def __iter__(self):
        return iter(self._cache)

    def __len__(self):
        return len(self._cache)

    def get(self, key: str, default=None):
        return self.__getitem__(key, default)

    def set_with_ttl(self, key: str, value, ttl: int = 300):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self.maxsize:
                    oldest = next(iter(self._cache))
                    del self._cache[oldest]
                    self._expiry.pop(oldest, None)
                self._cache[key] = value
            self._expiry[key] = time.time() + ttl

    def invalidate(self, key: str):
        with self._lock:
            self._cache.pop(key, None)
            self._expiry.pop(key, None)

    def clear_expired(self):
        with self._lock:
            now = time.time()
            expired = [k for k, exp in self._expiry.items() if now > exp]
            for k in expired:
                self._cache.pop(k, None)
                self._expiry.pop(k, None)


@dataclass
class StreamCache:
    """
    流 URL 缓存 - 核心优化组件
    1. LRU 缓存已获取的直链，5 分钟内不重复请求
    2. 预获取：播放前提前抓取附近文件的直链
    3. 异步并发预热
    """
    url_cache: LRUCache = field(default_factory=lambda: LRUCache(maxsize=2000))
    pending: Dict[str, asyncio.Lock] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def _get_lock(self, file_id: str) -> asyncio.Lock:
        with self._lock:
            if file_id not in self.pending:
                self.pending[file_id] = asyncio.Lock()
            return self.pending[file_id]

    async def get_url(self, file_id: str, fetch_fn, ttl: int = 300) -> Optional[str]:
        """
        获取直链，优先从缓存返回，缓存未命中则调用 fetch_fn
        """
        # 缓存命中
        cached = self.url_cache.get(file_id)
        if cached:
            return cached

        # 获取锁防止并发请求同一个 file_id
        lock = self._get_lock(file_id)
        async with lock:
            # 双重检查（获取锁期间可能已被其他协程写入）
            cached = self.url_cache.get(file_id)
            if cached:
                return cached

            # 调用 fetch_fn 获取
            url = await fetch_fn(file_id)
            if url:
                self.url_cache.set_with_ttl(file_id, url, ttl)
            return url

    def prefetch(self, file_ids: list, fetch_fn):
        """
        后台预获取直链（不阻塞主请求）
        """
        async def _prefetch_one(fid: str):
            lock = self._get_lock(fid)
            async with lock:
                if self.url_cache.get(fid):
                    return
                url = await fetch_fn(fid)
                if url:
                    self.url_cache.set_with_ttl(fid, url, 600)  # 预热的缓存更久

        asyncio.create_task(asyncio.gather(*[_prefetch_one(fid) for fid in file_ids], return_exceptions=True))

    def invalidate(self, file_id: str):
        """失效缓存"""
        self.url_cache.invalidate(file_id)

    def clear(self):
        """清空所有缓存"""
        with self._lock:
            self.url_cache.clear()
            self.pending.clear()


# 全局缓存实例
stream_cache = StreamCache()
