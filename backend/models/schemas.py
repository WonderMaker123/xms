"""
Pydantic 数据模型
"""
from pydantic import BaseModel
from typing import Optional, List


class UserInfo(BaseModel):
    user_id: str
    username: str
    phone: Optional[str] = None
    avatar: Optional[str] = None


class FileInfo(BaseModel):
    file_id: str
    name: str
    parent_id: str
    size: int
    type: int
    ext: str = ""


class STRMResult(BaseModel):
    success: int
    skipped: int
    errors: int
    files: List[dict]
