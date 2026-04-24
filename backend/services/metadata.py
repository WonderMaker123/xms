"""
TMDB 智能识别服务 - 电影/剧集识别 + 批量重命名
"""
import re
import asyncio
import httpx
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass


@dataclass
class MediaInfo:
    title: str
    year: str
    media_type: str  # movie / tv
    tmdb_id: int
    season: int = 0
    episode: int = 0
    name_en: str = ""
    name_cn: str = ""
    poster_url: str = ""


class TMDBService:
    """TMDB 刮削服务"""

    BASE_URL = "https://api.themoviedb.org/3"
    IMAGE_BASE = "https://image.tmdb.org/t/p/"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self._http: Optional[httpx.AsyncClient] = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15.0)
        return self._http

    async def _get(self, path: str, **kwargs) -> dict:
        http = await self._get_http()
        kwargs["params"] = {"api_key": self.api_key, **(kwargs.get("params", {}))}
        resp = await http.get(f"{self.BASE_URL}{path}", **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _parse_title_year(self, filename: str) -> Tuple[str, str, str]:
        """
        从文件名解析标题、年份、类型
        匹配格式如：
        - 流浪地球.The.Wandering.Earth.2019.1080p.BluRay.x264.mp4
        - Breaking.Bad.S01E01.720p.WEB-DL.mp4
        - 怪奇物语 第1季 第02集.mkv
        """
        name = filename
        
        # 去掉扩展名
        name = re.sub(r'\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|mpg|mpeg|3gp)$', '', name, flags=re.I)
        
        # 提取年份 (year between 1900-2099)
        year_match = re.search(r'(19\d{2}|20\d{2})', name)
        year = year_match.group(1) if year_match else ""
        
        # 提取集 SxxExx / SxxExxExx
        season_ep = re.search(r'[Ss](\d{1,2})[Ee](\d{1,2})', name)
        if season_ep:
            media_type = "tv"
            season = int(season_ep.group(1))
            episode = int(season_ep.group(2))
            # 去掉集信息，保留标题
            name = re.sub(r'[Ss]\d{1,2}[Ee]\d{1,2}.*$', '', name)
        else:
            media_type = "movie"
            season = episode = 0

        # 清理标题：去掉分辨率、来源等后缀
        suffix_patterns = [
            r'1080[pi]', r'720[pi]', r'4k', r'8k', r'2160[pi]',
            r'bluray', r'blu-ray', r'webrip', r'web-dl', r'hdtv', r'dvdrip',
            r'x264', r'x265', r'hevc', r'avc', r'h264', r'h265',
            r'aac', r'dts', r'flac', r'truehd',
            r'gb', r' Punish', r'repack', r'内部',
        ]
        for pat in suffix_patterns:
            name = re.sub(pat, '', name, flags=re.I)
        
        # 清理分隔符和多余空格
        name = re.sub(r'[\.\-_]+', ' ', name).strip()
        
        return name, year, media_type

    async def search(self, filename: str) -> Optional[MediaInfo]:
        """搜索媒体信息"""
        if not self.api_key:
            return None
        
        title, year, media_type = self._parse_title_year(filename)
        
        try:
            if media_type == "movie":
                result = await self._search_movie(title, year)
            else:
                result = await self._search_tv(title, year)
            
            if not result:
                return None

            return MediaInfo(
                title=title,
                year=year,
                media_type=media_type,
                tmdb_id=result.get("id", 0),
                season=season if media_type == "tv" else 0,
                episode=episode if media_type == "tv" else 0,
                name_en=result.get("original_title", ""),
                name_cn=result.get("title", ""),
                poster_url=self._poster_url(result.get("poster_path", "")),
            )
        except Exception:
            return None

    async def _search_movie(self, title: str, year: str) -> Optional[dict]:
        params = {"query": title}
        if year:
            params["year"] = year
        data = await self._get("/search/movie", params=params)
        results = data.get("results", [])
        if not results:
            return None
        # 取第一个结果
        return results[0]

    async def _search_tv(self, title: str, year: str) -> Optional[dict]:
        params = {"query": title}
        data = await self._get("/search/tv", params=params)
        results = data.get("results", [])
        if not results:
            return None
        return results[0]

    def _poster_url(self, path: str, size: str = "w500") -> str:
        if not path:
            return ""
        return f"{self.IMAGE_BASE}{size}{path}"

    async def batch_search(self, filenames: List[str]) -> Dict[str, MediaInfo]:
        """批量搜索（并发）"""
        results = {}
        tasks = [self.search(fn) for fn in filenames]
        done = await asyncio.gather(*tasks, return_exceptions=True)
        for fn, result in zip(filenames, done):
            if isinstance(result, MediaInfo):
                results[fn] = result
        return results

    def format_strm_name(self, info: MediaInfo) -> str:
        """
        生成标准化 STRM 文件名
        剧集: {title}.S{season:02d}E{episode:02d}.strm
        电影: {title}.{year}.strm
        """
        if info.media_type == "tv":
            return f"{info.title}.S{info.season:02d}E{info.episode:02d}.strm"
        else:
            return f"{info.title}.{info.year}.strm"
