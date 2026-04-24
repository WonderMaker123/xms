"""
配置文件 - xms
"""
import os
from pathlib import Path
from pydantic import BaseModel
from typing import Optional


class Config(BaseModel):
    # 服务配置
    host: str = "0.0.0.0"
    port: int = 9528
    secret_key: str = "xms-secret-key-change-me"
    
    # 光鸭云盘配置
    guangya_access_token: Optional[str] = None
    guangya_refresh_token: Optional[str] = None
    guangya_device_id: Optional[str] = None
    
    # 媒体库路径
    media_root: str = "/app/media"  # Docker 部署时
    strm_output_dir: str = "/app/media/strm"
    
    # 用户
    username: str = "admin"
    password: str = "admin123"
    
    @classmethod
    def load(cls, path: str = "/app/config/config.json") -> "Config":
        import json
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text())
            return cls(**data)
        return cls()

    def save(self, path: str = "/app/config/config.json"):
        import json
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.model_dump(), indent=2, ensure_ascii=False))


# 全局配置
config = Config.load()
