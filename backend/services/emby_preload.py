"""
Emby 预加载服务 - 观看即预热
原理：Emby Webhook 接收媒体播放事件，识别正在看什么电影/剧集，
自动后台预加载当前集 + 预告告集（下一集/下三集）的直链
效果：用户点播放时直链已在缓存，302 直接返回，秒级起播
"""
import asyncio
import hashlib
import re
import time
import logging
from typing import Optional, Callable, Dict, List, Set
from dataclasses import dataclass, field
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class EmbyItem:
    """Emby 媒体项"""
    item_id: str
    title: str
    media_type: str  # Movie / Episode / Series
    series_id: Optional[str] = None
    season_number: int = 0
    episode_number: int = 0
    year: int = 0
    file_id: str = ""  # 光鸭云盘 file_id（从STRM文件映射）


class EmbyWebhookHandler:
    """
    Emby Webhook 处理器 + 智能预加载引擎

    流程：
    1. Emby 播放/浏览时发送 Webhook -> 我们接收
    2. 解析媒体信息（电影名/剧集名+集数）
    3. 查本地 STRM 映射表，找到对应的光鸭云盘 file_id
    4. 预加载当前集 + 下一集的直链到缓存
    5. 用户点播放 -> 直链命中缓存 -> 302 秒级返回

    映射表格式（从 STRM 文件名解析）：
    /media/strm/电影/流浪地球 (2024)/流浪地球 (2024).strm -> file_id
    /media/strm/剧集/甄嬛传/Season 1/甄嬛传 S01E01.strm -> file_id
    """

    def __init__(self, strm_dir: str, base_url: str):
        self.strm_dir = strm_dir
        self.base_url = base_url
        # file_id -> str mtime
        self._strm_file_map: Dict[str, str] = {}
        # title+year -> list of (file_id, s0xe0e, season, episode, path)
        self._title_index: Dict[str, List] = defaultdict(list)
        # 最近播放（用于推荐预加载）
        self._recent: List[str] = []
        self._recent_lock = asyncio.Lock()
        self._preload_lock = asyncio.Lock()
        self._preload_callback: Optional[Callable] = None
        self._running = False

    def set_preload_callback(self, cb: Callable):
        """设置预加载回调：收到 file_ids 列表后调用"""
        self._preload_callback = cb

    # ===== STRM 文件映射 =====

    def rebuild_index(self, strm_dir: str):
        """重建 STRM 映射索引"""
        import os
        from pathlib import Path

        self._strm_file_map.clear()
        self._title_index.clear()

        strm_path = Path(strm_dir)
        if not strm_path.exists():
            return

        # 扫描所有 .strm 文件
        for strm_file in strm_path.rglob("*.strm"):
            try:
                content = strm_file.read_text().strip()
                # STRM 内容格式：/stream/{file_id} 或 /stream/direct/{file_id}
                file_id = self._extract_file_id(content)
                if not file_id:
                    continue

                mtime = str(strm_file.stat().st_mtime)
                self._strm_file_map[file_id] = mtime

                # 解析文件名建立索引
                parsed = self._parse_strm_path(strm_file, content)
                if parsed:
                    title_key = self._make_title_key(parsed["title"], parsed.get("year", 0))
                    self._title_index[title_key].append({
                        "file_id": file_id,
                        "season": parsed.get("season", 0),
                        "episode": parsed.get("episode", 0),
                        "media_type": parsed.get("media_type", "Movie"),
                        "path": str(strm_file),
                    })
            except Exception as e:
                logger.debug(f"[EmbyPreload] skip {strm_file}: {e}")

        logger.info(f"[EmbyPreload] indexed {len(self._strm_file_map)} STRM files")

    def _extract_file_id(self, content: str) -> Optional[str]:
        """从 STRM 内容提取 file_id"""
        # /stream/{file_id} 或 /stream/direct/{file_id}
        m = re.search(r'/stream/(?:direct/)?([a-zA-Z0-9_-]+)', content)
        return m.group(1) if m else None

    def _parse_strm_path(self, strm_file, content: str) -> Optional[Dict]:
        """解析 STRM 文件路径，提取媒体信息"""
        try:
            rel = strm_file.relative_to(self.strm_dir)
            parts = rel.parts  # e.g. ['电影', '流浪地球 (2024)', '流浪地球 (2024).strm']

            if len(parts) < 2:
                return None

            # 识别是电影还是剧集
            if parts[0] == "电影" and len(parts) >= 2:
                # 电影：电影/标题 (年份)/标题.strm
                title_part = parts[1]
                year = self._extract_year(title_part)
                title = self._extract_title(title_part)
                return {
                    "media_type": "Movie",
                    "title": title,
                    "year": year,
                }

            elif parts[0] == "剧集" and len(parts) >= 3:
                # 剧集：剧集/标题/Season X/标题 S0XE0E.strm
                series_title = parts[1]
                season_part = parts[2]
                season_num = self._extract_season(season_part)
                # 文件名：标题 S01E01.mkv -> S01E01
                filename = strm_file.stem  # 不带扩展名
                ep_info = self._extract_episode(filename)
                return {
                    "media_type": "Series",
                    "title": series_title,
                    "season": season_num,
                    "episode": ep_info,
                }
        except:
            pass
        return None

    def _extract_year(self, s: str) -> int:
        m = re.search(r'\((\d{4})\)', s)
        return int(m.group(1)) if m else 0

    def _extract_title(self, s: str) -> str:
        return re.sub(r'\s*\(\d{4}\)\s*', '', s).strip()

    def _extract_season(self, s: str) -> int:
        m = re.search(r'Season\s*(\d+)', s, re.I)
        return int(m.group(1)) if m else 1

    def _extract_episode(self, s: str) -> int:
        m = re.search(r'S(\d+)E(\d+)', s, re.I)
        if m:
            return int(m.group(2))
        return 0

    def _make_title_key(self, title: str, year: int) -> str:
        """生成标题索引key"""
        t = re.sub(r'[^\w\u4e00-\u9fff]', '', title).lower()
        return f"{t}_{year}" if year else t

    # ===== 预加载逻辑 =====

    async def handle_webhook(self, payload: dict):
        """
        处理 Emby Webhook 事件
        关键事件：
        - playback.start（开始播放） -> 立即预加载当前集 + 下一集
        - item.show（浏览到某个媒体项） -> 后台静默预加载
        - playback.stop（停止播放） -> 记录历史用于推荐
        """
        event = payload.get("Event", "")
        item = payload.get("Item", {})

        if not item:
            return

        title = item.get("Name", "")
        media_type = item.get("Type", "")
        item_id = str(item.get("Id", ""))
        year = item.get("ProductionYear", 0)

        logger.info(f"[EmbyPreload] Event={event} Title={title} Type={media_type}")

        if event == "playback.start":
            await self._on_playback_start(title, media_type, year, item)
        elif event == "playback.stop":
            await self._on_playback_stop(title, media_type)
        elif event == "item.show":
            # 浏览媒体详情页时静默预加载
            asyncio.create_task(self._on_item_browse(title, media_type, year))

    async def _on_playback_start(self, title: str, media_type: str, year: int, item: dict):
        """播放开始：预加载当前集 + 预告告集"""
        await self._recently_played(item.get("SeriesId") or item.get("Id"))

        if media_type == "Movie":
            # 电影：直接找 file_id 预加载
            fids = self._find_movie(title, year)
            await self._preload_file_ids(fids)
        elif media_type == "Episode":
            # 剧集：预加载当前集 + 接下来2集
            fids = self._find_episodes(title, year, item.get("SeasonNumber", 1), item.get("IndexNumber", 1), count=3)
            await self._preload_file_ids(fids)

    async def _on_playback_stop(self, title: str, media_type: str):
        """播放停止：记录历史"""
        async with self._recent_lock:
            self._recent.insert(0, title)
            if len(self._recent) > 50:
                self._recent = self._recent[:50]

    async def _on_item_browse(self, title: str, media_type: str, year: int):
        """浏览媒体项：静默预加载"""
        if media_type == "Movie":
            fids = self._find_movie(title, year)
            await self._preload_file_ids(fids)
        elif media_type == "Series":
            # 预加载剧集第一集
            fids = self._find_episodes(title, year, 1, 1, count=1)
            await self._preload_file_ids(fids)

    async def _preload_file_ids(self, file_ids: List[str]):
        """批量预加载 file_ids 到缓存"""
        if not file_ids or not self._preload_callback:
            return

        async with self._preload_lock:
            logger.info(f"[EmbyPreload] preloading {len(file_ids)} file_ids: {file_ids[:3]}")
            # 调用回调，让 stream_cache 预加载直链
            # 注意：实际 file_id -> 直链 的 fetch_fn 由调用方注入
            if self._preload_callback:
                await self._preload_callback(file_ids)

    def _find_movie(self, title: str, year: int = 0) -> List[str]:
        """查找电影对应的所有 file_id"""
        key = self._make_title_key(title, year)
        matches = self._title_index.get(key, [])
        # 也尝试模糊匹配
        if not matches:
            t = re.sub(r'[^\w\u4e00-\u9fff]', '', title).lower()
            for k, v in self._title_index.items():
                if t in k:
                    matches = v
                    break
        return [m["file_id"] for m in matches]

    def _find_episodes(self, title: str, year: int, season: int, episode: int, count: int = 3) -> List[str]:
        """查找剧集当前集及后续 N 集"""
        key = self._make_title_key(title, year)
        matches = self._title_index.get(key, [])

        if not matches:
            t = re.sub(r'[^\w\u4e00-\u9fff]', '', title).lower()
            for k, v in self._title_index.items():
                if t in k:
                    matches = v
                    break

        # 过滤同季
        season_eps = [m for m in matches if m["season"] == season and m["episode"] >= episode]
        season_eps.sort(key=lambda x: x["episode"])
        result = [m["file_id"] for m in season_eps[:count]]

        # 如果不够，预加载其他季
        if len(result) < count:
            other_eps = [m for m in matches if m["season"] > season]
            other_eps.sort(key=lambda x: (x["season"], x["episode"]))
            result.extend([m["file_id"] for m in other_eps[:count - len(result)]])

        return result

    async def _recently_played(self, item_id: str):
        """记录最近播放，清理过期 STRM 缓存"""
        # 可以用来做推荐预加载：预加载同系列
        pass

    def set_strm_dir(self, strm_dir: str):
        """更新 STRM 目录并重建索引"""
        self.strm_dir = strm_dir
        self.rebuild_index(strm_dir)


# 全局实例
emby_preload: Optional[EmbyWebhookHandler] = None


def get_emby_preload() -> Optional[EmbyWebhookHandler]:
    return emby_preload
