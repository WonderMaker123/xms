"""
配置文件 - xms v3
"""
import os
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


class Config(BaseModel):
    # 服务配置
    host: str = "0.0.0.0"
    port: int = 9528
    secret_key: str = "xms-secret-key-change-me"

    # 管理员账号密码（本地登录）
    username: str = "admin"
    password: str = "admin123"

    # 光鸭云盘 Token
    guangya_access_token: Optional[str] = None
    guangya_refresh_token: Optional[str] = None
    guangya_device_id: Optional[str] = None

    # 媒体库路径
    media_root: str = "/app/media"
    strm_output_dir: str = "/app/media/strm"

    # TMDB
    tmdb_key: str = ""

    # TG 机器人
    tg: TGConfig = TGConfig()

    # Emby
    emby: EmbyConfig = EmbyConfig()

    # 缓存配置
    cache_ttl: int = 300       # 普通缓存 TTL（秒）
    cache_max_size: int = 2000  # LRU 缓存最大条数

    # 预加载配置
    preload_enabled: bool = True
    preload_ahead: int = 3      # 预加载接下来几集

    @classmethod
    def load(cls, path: str = "/app/config/config.json") -> "Config":
        import json
        p = Path(path)
        if p.exists():
            try:
                data = json.loads(p.read_text())
                return cls(**data)
            except Exception:
                pass
        return cls()

    def save(self, path: str = "/app/config/config.json"):
        import json
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.model_dump(), indent=2, ensure_ascii=False))


config = Config.load()
