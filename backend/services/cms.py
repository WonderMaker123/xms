"""
CMS 媒体库管理服务 - xms
功能：媒体订阅 / RSS / 下载队列 / 任务历史 / 多用户 / 媒体信息
"""
import asyncio
import time
import uuid
import json
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class SubStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class MediaSubscription:
    """媒体订阅"""
    id: str
    title: str
    year: int = 0
    media_type: str = "movie"  # movie / series
    tmdb_id: int = 0
    status: SubStatus = SubStatus.PENDING
    season: int = 1  # 剧集订阅的季数
    episode_offset: int = 0  # 已有的集数
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    note: str = ""


@dataclass
class DownloadItem:
    """下载项"""
    id: str
    title: str
    url: str
    media_type: str = "movie"
    status: str = "pending"  # pending / downloading / done / failed
    progress: int = 0
    file_size: int = 0
    downloaded: int = 0
    created_at: float = field(default_factory=time.time)
    error: str = ""


class CMSServices:
    """
    CMS 核心服务 - 媒体库管理
    包含：
    1. 订阅管理：订阅电影/剧集，自动追踪更新
    2. 下载队列：管理转存/下载任务
    3. 任务历史：所有操作记录
    4. 媒体信息库：缓存 TMDB 数据，加速检索
    5. 多用户支持：TG/管理员分离
    """

    def __init__(self):
        self._subs: Dict[str, MediaSubscription] = {}
        self._downloads: Dict[str, DownloadItem] = {}
        self._history: List[Dict] = []
        self._history_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        # 统计
        self._stats = {
            "total_strm": 0,
            "total_size": 0,
            "total_subs": 0,
            "total_downloads": 0,
        }

    # ===== 历史记录 =====

    async def add_history(self, action: str, detail: str, user: str = "system"):
        async with self._history_lock:
            self._history.insert(0, {
                "id": uuid.uuid4().hex[:8],
                "action": action,
                "detail": detail,
                "user": user,
                "time": time.time(),
            })
            if len(self._history) > 500:
                self._history = self._history[:500]

    async def get_history(self, limit: int = 50) -> List[Dict]:
        async with self._history_lock:
            return self._history[:limit]

    # ===== 订阅管理 =====

    async def add_subscription(self, title: str, media_type: str, year: int = 0, tmdb_id: int = 0, season: int = 1) -> str:
        """添加订阅，返回订阅ID"""
        async with self._lock:
            sub_id = hashlib.md5(f"{title}{year}{media_type}".encode()).hexdigest()[:12]
            if sub_id in self._subs:
                return sub_id  # 已存在

            sub = MediaSubscription(
                id=sub_id,
                title=title,
                year=year,
                media_type=media_type,
                tmdb_id=tmdb_id,
                season=season,
            )
            self._subs[sub_id] = sub
            self._stats["total_subs"] = len(self._subs)
            await self.add_history("subscribe", f"订阅 {media_type}: {title}", "user")
            return sub_id

    async def remove_subscription(self, sub_id: str):
        async with self._lock:
            if sub_id in self._subs:
                sub = self._subs[sub_id]
                await self.add_history("unsubscribe", f"取消订阅: {sub.title}", "user")
                del self._subs[sub_id]
                self._stats["total_subs"] = len(self._subs)

    async def list_subscriptions(self, media_type: str = None) -> List[MediaSubscription]:
        async with self._lock:
            subs = list(self._subs.values())
        if media_type:
            subs = [s for s in subs if s.media_type == media_type]
        return sorted(subs, key=lambda x: x.created_at, reverse=True)

    async def update_subscription(self, sub_id: str, **kwargs):
        async with self._lock:
            if sub_id in self._subs:
                sub = self._subs[sub_id]
                for k, v in kwargs.items():
                    if hasattr(sub, k):
                        setattr(sub, k, v)
                sub.updated_at = time.time()

    # ===== 下载队列 =====

    async def add_download(self, title: str, url: str, media_type: str = "movie") -> str:
        """添加下载项"""
        async with self._lock:
            dl_id = hashlib.md5(f"{url}{time.time()}".encode()).hexdigest()[:12]
            dl = DownloadItem(id=dl_id, title=title, url=url, media_type=media_type)
            self._downloads[dl_id] = dl
            self._stats["total_downloads"] = len(self._downloads)
            await self.add_history("download_add", f"添加下载: {title}", "user")
            return dl_id

    async def update_download(self, dl_id: str, **kwargs):
        async with self._lock:
            if dl_id in self._downloads:
                dl = self._downloads[dl_id]
                for k, v in kwargs.items():
                    if hasattr(dl, k):
                        setattr(dl, k, v)

    async def list_downloads(self, status: str = None) -> List[DownloadItem]:
        async with self._lock:
            dls = list(self._downloads.values())
        if status:
            dls = [d for d in dls if d.status == status]
        return sorted(dls, key=lambda x: x.created_at, reverse=True)

    async def remove_download(self, dl_id: str):
        async with self._lock:
            if dl_id in self._downloads:
                dl = self._downloads[dl_id]
                await self.add_history("download_remove", f"移除下载: {dl.title}", "user")
                del self._downloads[dl_id]
                self._stats["total_downloads"] = len(self._downloads)

    # ===== 统计 =====

    async def get_stats(self) -> dict:
        async with self._lock:
            return {
                "total_subs": self._stats["total_subs"],
                "total_downloads": self._stats["total_downloads"],
                "total_strm": self._stats["total_strm"],
                "total_size": self._stats["total_size"],
                "active_downloads": len([d for d in self._downloads.values() if d.status == "downloading"]),
                "pending_downloads": len([d for d in self._downloads.values() if d.status == "pending"]),
            }

    async def update_stats(self, **kwargs):
        async with self._lock:
            for k, v in kwargs.items():
                if k in self._stats:
                    self._stats[k] = v


# 全局实例
cms_service: Optional[CMSServices] = None


def get_cms_service() -> Optional[CMSServices]:
    return cms_service
