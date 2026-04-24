"""
STRM 文件生成服务
将光鸭云盘的视频文件生成 .strm 文件，供 Emby/Jellyfin 扫描
"""
import os
import json
import hashlib
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass
from .guangya_client import GuangyaClient


@dataclass
class FileItem:
    file_id: str
    name: str
    parent_id: str
    size: int
    type: int  # 1=文件夹, 2=视频
    path: str  # 相对路径
    ext: str = ""

    @classmethod
    def from_api(cls, data: Dict, parent_path: str = "") -> "FileItem":
        name = data.get("fileName", data.get("name", ""))
        file_id = data.get("fileId", data.get("id", ""))
        parent_id = data.get("parentId", "")
        size = data.get("size", 0)
        file_type = data.get("type", data.get("fileType", 1))
        ext = os.path.splitext(name)[1].lower()
        full_path = os.path.join(parent_path, name) if parent_path else name
        return cls(
            file_id=file_id,
            name=name,
            parent_id=parent_id,
            size=size,
            type=file_type,
            path=full_path,
            ext=ext
        )


class STRMService:
    """STRM 生成服务"""

    def __init__(self, client: GuangyaClient, output_dir: str, base_url: str = ""):
        self.client = client
        self.output_dir = Path(output_dir)
        self.base_url = base_url.rstrip("/")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _get_stream_url(self, file_id: str) -> str:
        """获取 302 重定向的流地址"""
        url = self.client.get_stream_url(file_id)
        if url and url.startswith("http"):
            return url
        return f"{self.base_url}/stream/{file_id}"

    def _sanitize_name(self, name: str) -> str:
        """清理文件名，去掉非法字符"""
        import re
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
        return name.strip()

    def _get_strm_path(self, file_path: str) -> Path:
        """将媒体库路径转换为 strm 输出路径"""
        strm_path = file_path.replace("/", "_").replace("\\", "_")
        if strm_path.startswith("_"):
            strm_path = strm_path[1:]
        return self.output_dir / f"{strm_path}.strm"

    def _should_include(self, item: FileItem) -> bool:
        """判断是否应该生成 strm"""
        video_exts = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm',
                      '.m4v', '.mpg', '.mpeg', '.3gp', '.ts', '.m2ts', '.rmvb', '.vob'}
        return item.ext in video_exts

    def _write_strm(self, item: FileItem, stream_url: str):
        """写入 strm 文件"""
        strm_path = self._get_strm_path(item.path)
        strm_path.parent.mkdir(parents=True, exist_ok=True)
        strm_path.write_text(stream_url, encoding="utf-8")
        return strm_path

    def sync_folder(self, parent_id: Optional[str] = None, folder_path: str = "",
                    depth: int = 10, progress_callback=None) -> Dict:
        """
        同步文件夹，生成所有视频的 strm
        """
        results = {"success": 0, "skipped": 0, "errors": 0, "files": []}

        def _sync_recursive(pid: Optional[str], path: str, current_depth: int):
            if current_depth <= 0:
                return

            page = 0
            page_size = 100
            while True:
                try:
                    resp = self.client.fs_files(parent_id=pid, page=page, page_size=page_size)
                except Exception as e:
                    results["errors"] += 1
                    break

                items = resp.get("data", {}).get("list", [])
                if not items:
                    break

                for raw in items:
                    item = FileItem.from_api(raw, parent_path=path)

                    if item.type == 1:  # 文件夹
                        new_path = os.path.join(path, item.name) if path else item.name
                        _sync_recursive(item.file_id, new_path, current_depth - 1)
                    elif self._should_include(item):
                        try:
                            stream_url = self._get_stream_url(item.file_id)
                            strm_path = self._write_strm(item, stream_url)
                            results["success"] += 1
                            results["files"].append({
                                "name": item.name,
                                "strm_path": str(strm_path),
                                "stream_url": stream_url
                            })
                            if progress_callback:
                                progress_callback(item.name, results["success"])
                        except Exception as e:
                            results["errors"] += 1

                page += 1
                if len(items) < page_size:
                    break

        _sync_recursive(parent_id, folder_path, depth)
        return results

    def refresh_file(self, file_id: str, file_path: str) -> Optional[Path]:
        """刷新单个文件的 strm"""
        item = FileItem(
            file_id=file_id,
            name=os.path.basename(file_path),
            parent_id="",
            size=0,
            type=2,
            path=file_path
        )
        stream_url = self._get_stream_url(file_id)
        return self._write_strm(item, stream_url)
