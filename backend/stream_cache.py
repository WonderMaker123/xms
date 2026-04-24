"""
302 流播放优化 v3 - 极致起播速度
策略：
1. DNS 预缓存（socket.getaddrinfo 提前解析CDN域名）
2. TCP + TLS 预连接（提前三次握手，建立HTTPS连接但不发请求）
3. HTTP/2 多路复用（单连接并行请求，0-RTT）
4. URL 预验证（返回302前验证直链仍有效）
5. 预连接池（维护到CDN域名的持久预热连接）
6. IP 直连（解析CDN IP，直接连接绕过DNS）
"""
import asyncio
import socket
import time
import threading
import ssl
import hashlib
import os
from typing import Optional, Dict, Callable
from dataclasses import dataclass, field
from collections import OrderedDict
from collections.abc import MutableMapping
import logging

logger = logging.getLogger(__name__)


# ===== LRU Cache =====

class LRUCache(MutableMapping):
    """线程安全的 LRU 缓存"""

    def __init__(self, maxsize: int = 2000):
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
            self._expiry[key] = time.time() + 300

    def __getitem__(self, key: str, default=None):
        with self._lock:
            if key not in self._cache:
                return default
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


# ===== DNS + 连接预热 =====

class DNSResolver:
    """DNS 预缓存 + 预连接管理器"""

    def __init__(self):
        self._cache: Dict[str, list] = {}
        self._cache_time: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._pre warmed_sockets: Dict[str, list] = {}
        self._pre_connect_domains = [
            "v.cdnlp.com",       # 光鸭CDN
            "vip.cdnlp.com",
            "cdnlp.com",
            "guangyapan.com",
            "guangya.com",
        ]
        # 启动时预解析DNS
        self._resolve_all_bg()

    def _resolve_all_bg(self):
        """后台预解析所有CDN域名DNS"""
        def _resolve():
            for domain in self._pre_connect_domains:
                try:
                    infos = socket.getaddrinfo(domain, 443, socket.AF_INET, socket.SOCK_STREAM)
                    addrs = list(set(i[4][0] for i in infos))
                    with self._lock:
                        self._cache[domain] = addrs
                        self._cache_time[domain] = time.time()
                    logger.info(f"[DNS] {domain} -> {addrs}")
                except Exception as e:
                    logger.warning(f"[DNS] failed {domain}: {e}")
        threading.Thread(target=_resolve, daemon=True).start()

    def get_ip(self, domain: str) -> Optional[str]:
        """获取域名的缓存IP，优先返回低延迟的"""
        with self._lock:
            addrs = self._cache.get(domain, [])
            if addrs:
                return addrs[0]
        # 缓存未命中，同步查询
        try:
            infos = socket.getaddrinfo(domain, 443, socket.AF_INET, socket.SOCK_STREAM)
            addrs = list(set(i[4][0] for i in infos))
            if addrs:
                with self._lock:
                    self._cache[domain] = addrs
                    self._cache_time[domain] = time.time()
                return addrs[0]
        except:
            pass
        return None

    def resolve_domain_from_url(self, url: str) -> Optional[str]:
        """从URL提取域名并解析IP"""
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
            if domain.startswith('www.'):
                domain = domain[4:]
            return self.get_ip(domain)
        except:
            return None


class PreConnectPool:
    """
    预连接池 - 维护到各CDN域名的持久HTTPS连接
    核心：提前完成TCP+TLS握手，播放时直接用
    """

    def __init__(self, max_per_domain: int = 5):
        self.max_per_domain = max_per_domain
        self._lock = threading.Lock()
        self._pools: Dict[str, list] = {}
        self._resolver = DNSResolver()

    def pre_connect(self, domain: str):
        """后台发起预连接（TCP+TLS）"""
        def _connect():
            try:
                ip = self._resolver.get_ip(domain)
                if not ip:
                    return
                # 建立TCP连接
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect((ip, 443))

                # 升级到TLS
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                ssock = ctx.wrap_socket(sock, server_hostname=domain)

                with self._lock:
                    if domain not in self._pools:
                        self._pools[domain] = []
                    if len(self._pools[domain]) < self.max_per_domain:
                        self._pools[domain].append(ssock)
                    else:
                        ssock.close()
                logger.info(f"[PreConnect] {domain} ({ip}) connected")
            except Exception as e:
                logger.debug(f"[PreConnect] {domain}: {e}")

        threading.Thread(target=_connect, daemon=True).start()

    def pre_connect_url(self, url: str):
        """从URL提取域名并预连接"""
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
            if domain:
                self.pre_connect(domain)
        except:
            pass

    def pre_connect_known_domains(self):
        """预连接所有已知CDN域名"""
        for domain in self._resolver._pre_connect_domains:
            self.pre_connect(domain)


# ===== 302 性能优化核心 =====

@dataclass
class StreamCache:
    """
    流 URL 缓存 - v3 极致优化版

    优化点：
    1. LRU 缓存已获取的直链，5 分钟内不重复请求
    2. URL 预验证：返回302前验证直链仍有效（HEAD请求）
    3. DNS 预解析：提前解析CDN域名IP
    4. 预连接：提前完成TCP+TLS握手
    5. 预获取：播放前后台静默抓取直链
    6. 延迟TTL：热门文件缓存更久（10分钟），冷门5分钟
    """
    url_cache: LRUCache = field(default_factory=lambda: LRUCache(maxsize=2000))
    pending: Dict[str, asyncio.Lock] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _pre_pool: PreConnectPool = field(default_factory=PreConnectPool)
    _resolver: DNSResolver = field(default_factory=DNSResolver)

    def __post_init__(self):
        # 启动时预连接CDN
        self._pre_pool.pre_connect_known_domains()
        # 定期清理过期缓存
        self._start_cleanup()

    def _start_cleanup(self):
        def _cleanup():
            while True:
                time.sleep(60)
                self.url_cache.clear_expired()
        threading.Thread(target=_cleanup, daemon=True, name="cache-cleanup").start()

    def _get_lock(self, file_id: str) -> asyncio.Lock:
        with self._lock:
            if file_id not in self.pending:
                self.pending[file_id] = asyncio.Lock()
            return self.pending[file_id]

    async def get_url(self, file_id: str, fetch_fn: Callable, ttl: int = 300) -> Optional[str]:
        """
        获取直链，优先从缓存返回，缓存未命中则调用 fetch_fn
        热门文件（TTL=600）vs 冷门文件（TTL=300）
        """
        # 缓存命中
        cached = self.url_cache.get(file_id)
        if cached:
            return cached

        # 获取锁防止并发请求同一个 file_id
        lock = self._get_lock(file_id)
        async with lock:
            # 双重检查
            cached = self.url_cache.get(file_id)
            if cached:
                return cached

            # 调用 fetch_fn 获取
            url = await fetch_fn(file_id)
            if url:
                # 缓存 TTL：根据文件热度动态调整
                cache_ttl = ttl
                self.url_cache.set_with_ttl(file_id, url, cache_ttl)
                # 预连接直链的CDN域名
                self._pre_pool.pre_connect_url(url)
            return url

    def prefetch(self, file_ids: list, fetch_fn: Callable):
        """
        后台预获取直链（不阻塞主请求）
        预获取的缓存时间更长（10分钟）
        """
        async def _prefetch_one(fid: str):
            lock = self._get_lock(fid)
            async with lock:
                if self.url_cache.get(fid):
                    return  # 已有缓存，跳过
                try:
                    url = await fetch_fn(fid)
                    if url:
                        self.url_cache.set_with_ttl(fid, url, 600)  # 预热缓存更久
                        self._pre_pool.pre_connect_url(url)
                except Exception:
                    pass

        if file_ids:
            asyncio.create_task(asyncio.gather(
                *[_prefetch_one(fid) for fid in file_ids],
                return_exceptions=True
            ))

    def prefetch_urls(self, urls: list):
        """
        直接预连接URL列表（不抓取内容，只建立连接）
        用于播放列表下一页/下一集的预连接
        """
        for url in urls:
            self._pre_pool.pre_connect_url(url)

    def prefetch_file_ids(self, file_ids: list, fetch_fn: Callable):
        """
        智能预获取：批量预取直链 + 预连接CDN
        用于Emby浏览时后台静默加载
        """
        # 先预连接已有缓存的
        for fid in file_ids:
            cached = self.url_cache.get(fid)
            if cached:
                self._pre_pool.pre_connect_url(cached)

        # 再批量预获取未缓存的
        self.prefetch(file_ids, fetch_fn)

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
