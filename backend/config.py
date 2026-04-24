"""
配置文件 - xms v3 完整配置
所有配置均可通过 UI 修改
"""
import os
import json
from pathlib import Path
from pydantic import BaseModel
from typing import Optional, List


class TGConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    admin_ids: List[int] = []


class EmbyConfig(BaseModel):
    enabled: bool = False
    api_key: str = ""
    host: str = "http://localhost:8096"


class PreloadConfig(BaseModel):
    enabled: bool = True
    ahead_count: int = 3   # 预加载接下来几集


class StreamCacheConfig(BaseModel):
    ttl: int = 300        # 缓存 TTL（秒）
    max_size: int = 2000  # LRU 最大条数
    dns_cache_ttl: int = 3600  # DNS 缓存 TTL
    tcp_preconnect: bool = True  # TCP 预连接
    http2_enabled: bool = True   # HTTP/2


class CMSSubscriptionConfig(BaseModel):
    auto_sync: bool = False          # 订阅自动同步
    rss_refresh_minutes: int = 30   # RSS 刷新间隔
    download_path: str = "/app/media/downloads"  # 下载保存路径


class Config(BaseModel):
    # ===== 基础 =====
    secret_key: str = "xms-secret-key-change-me"

    # ===== 管理员账号 =====
    username: str = "admin"
    password: str = "admin123"  # 建议首次登录后修改

    # ===== 光鸭云盘 Token =====
    guangya_access_token: Optional[str] = None
    guangya_refresh_token: Optional[str] = None
    guangya_device_id: Optional[str] = None

    # ===== 媒体库路径 =====
    media_root: str = "/app/media"
    strm_output_dir: str = "/app/media/strm"

    # ===== TMDB =====
    tmdb_key: str = ""
    tmdb_proxy: str = ""  # TMDB 代理地址

    # ===== Emby =====
    emby: EmbyConfig = EmbyConfig()

    # ===== TG 机器人 =====
    tg: TGConfig = TGConfig()

    # ===== 预加载 =====
    preload: PreloadConfig = PreloadConfig()

    # ===== Stream 缓存 =====
    stream_cache: StreamCacheConfig = StreamCacheConfig()

    # ===== CMS =====
    cms: CMSSubscriptionConfig = CMSSubscriptionConfig()

    @classmethod
    def load(cls, path: str = "/app/config/config.json") -> "Config":
        p = Path(path)
        if p.exists():
            try:
                data = json.loads(p.read_text())
                return cls(**data)
            except Exception:
                pass
        return cls()

    def save(self, path: str = "/app/config/config.json"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.model_dump(), indent=2, ensure_ascii=False))


config = Config.load()
