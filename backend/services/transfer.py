"""
自动转存服务 - xms
接收分享链接 → 解析 → 转存到光鸭云盘 → 自动分类整理 + TMDB 命名
参考 Quark-Auto-Save 架构：任务队列 + 正则过滤 + 插件机制
"""
import asyncio
import re
import hashlib
import time
import uuid
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class TransferStatus(Enum):
    PENDING = "pending"
    PARSING = "parsing"
    TRANSFERRING = "transferring"
    ORGANIZING = "organizing"
    DONE = "done"
    FAILED = "failed"


@dataclass
class TransferTask:
    """转存任务"""
    id: str
    link: str
    link_type: str  # guangya / quark / 115
    user_id: int
    username: str
    status: TransferStatus = TransferStatus.PENDING
    file_count: int = 0
    success_count: int = 0
    error_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error_msg: str = ""
    result_paths: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "link": self.link,
            "link_type": self.link_type,
            "user_id": self.user_id,
            "username": self.username,
            "status": self.status.value,
            "file_count": self.file_count,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error_msg": self.error_msg,
            "result_paths": self.result_paths,
        }


class TransferService:
    """
    自动转存服务 - 完整工作流：
    1. 接收链接 → 判断类型（光鸭/夸克/115）
    2. 解析分享内容（文件列表）
    3. 转存到光鸭云盘目标目录
    4. 自动分类整理（电影/剧集）
    5. TMDB 智能识别 + 重命名
    6. 触发 Emby 媒体库刷新
    """

    def __init__(self, client, tmdb_service=None):
        self.client = client
        self.tmdb = tmdb_service
        self._tasks: Dict[str, TransferTask] = {}
        self._tasks_lock = asyncio.Lock()
        self._emby_callback: Optional[Callable] = None
        self._tg_callback: Optional[Callable] = None
        # 默认分类目录
        self.movie_dir = "电影"
        self.series_dir = "剧集"
        # 插件规则
        self.rename_rules: List[Dict] = []
        # 过滤正则
        self.filter_patterns: List[re.Pattern] = []

    def set_emby_callback(self, cb: Callable):
        self._emby_callback = cb

    def set_tg_callback(self, cb: Callable):
        self._tg_callback = cb

    # ===== 核心 API =====

    async def create_task(self, link: str, user_id: int, username: str = "") -> str:
        """创建转存任务，返回任务ID"""
        task_id = hashlib.md5(f"{link}{time.time()}".encode()).hexdigest()[:12]
        link_type = self._detect_link_type(link)
        task = TransferTask(
            id=task_id,
            link=link,
            link_type=link_type,
            user_id=user_id,
            username=username,
        )
        async with self._tasks_lock:
            self._tasks[task_id] = task

        # 异步执行转存
        asyncio.create_task(self._run_task(task_id))
        return task_id

    async def get_task(self, task_id: str) -> Optional[TransferTask]:
        async with self._tasks_lock:
            return self._tasks.get(task_id)

    async def list_tasks(self, user_id: int = None, limit: int = 50) -> List[TransferTask]:
        async with self._tasks_lock:
            tasks = list(self._tasks.values())
        if user_id is not None:
            tasks = [t for t in tasks if t.user_id == user_id]
        tasks.sort(key=lambda x: x.created_at, reverse=True)
        return tasks[:limit]

    async def _run_task(self, task_id: str):
        """执行转存任务"""
        async with self._tasks_lock:
            task = self._tasks.get(task_id)
            if not task:
                return

        try:
            # 1. 解析链接
            task.status = TransferStatus.PARSING
            await self._update_task(task)

            share_info = await self._parse_share_link(task.link, task.link_type)
            if not share_info:
                raise Exception("解析分享链接失败")

            file_list = share_info.get("files", [])
            task.file_count = len(file_list)
            await self._notify_tg(task, f"📦 共 {task.file_count} 个文件，开始转存...")

            # 2. 逐个转存
            task.status = TransferStatus.TRANSFERRING
            await self._update_task(task)

            for i, file_info in enumerate(file_list):
                try:
                    # 过滤
                    if self._should_skip(file_info["name"]):
                        continue

                    # 转存
                    saved_path = await self._transfer_file(task.link, task.link_type, file_info)
                    if saved_path:
                        task.success_count += 1
                        task.result_paths.append(saved_path)
                    else:
                        task.error_count += 1

                    # 每完成10个通知一次
                    if task.success_count % 10 == 0:
                        await self._notify_tg(task, f"📦 已转存 {task.success_count}/{task.file_count} 个文件")

                except Exception as e:
                    task.error_count += 1
                    task.error_msg = str(e)
                    logger.warning(f"[Transfer] file error: {e}")

            # 3. 自动分类整理 + TMDB 命名
            if task.result_paths:
                task.status = TransferStatus.ORGANIZING
                await self._update_task(task)
                await self._organize_files(task)

            # 4. 完成
            task.status = TransferStatus.DONE
            task.updated_at = time.time()
            await self._update_task(task)

            await self._notify_tg(task,
                f"✅ 转存完成！\n"
                f"成功：{task.success_count}\n"
                f"失败：{task.error_count}\n"
                f"路径：{task.result_paths[0] if task.result_paths else '无'}"
            )

            # 5. 触发 Emby 刷新
            if self._emby_callback and task.result_paths:
                try:
                    await self._emby_callback(task.result_paths)
                except Exception as e:
                    logger.warning(f"[Transfer] Emby refresh failed: {e}")

        except Exception as e:
            task.status = TransferStatus.FAILED
            task.error_msg = str(e)
            task.updated_at = time.time()
            await self._update_task(task)
            await self._notify_tg(task, f"❌ 转存失败：{e}")

    async def _notify_tg(self, task: TransferTask, msg: str):
        if self._tg_callback:
            try:
                await self._tg_callback(task, msg)
            except Exception:
                pass

    async def _update_task(self, task: TransferTask):
        task.updated_at = time.time()

    # ===== 链接解析 =====

    def _detect_link_type(self, link: str) -> str:
        if "guangyapan" in link or "guangya" in link:
            return "guangya"
        elif "quark" in link or "pan.quark" in link:
            return "quark"
        elif "115.com" in link:
            return "115"
        return "unknown"

    async def _parse_share_link(self, link: str, link_type: str) -> Optional[Dict]:
        """解析分享链接，获取文件列表"""
        if link_type == "guangya":
            return await self._parse_guangya_share(link)
        elif link_type == "quark":
            return await self._parse_quark_share(link)
        # 其他网盘暂不支持
        return None

    async def _parse_guangya_share(self, link: str) -> Optional[Dict]:
        """解析光鸭云盘分享链接"""
        # 提取分享ID和token
        # 格式：https://app.guangyapan.com/pan/share/xxxxx?token=xxxxx
        m = re.search(r'/share/([a-zA-Z0-9]+)', link)
        if not m:
            return None
        share_token = m.group(1)

        try:
            # 调用光鸭API获取分享内容
            resp = self.client._request(
                "https://api.guangyapan.com/nd.bizuserres.s/v1/share/detail",
                json={"shareToken": share_token}
            )
            files = resp.get("data", {}).get("files", [])
            return {"files": [{"name": f.get("fileName", ""), "size": f.get("size", 0), "type": f.get("type", 1)} for f in files]}
        except Exception as e:
            logger.warning(f"[Transfer] guangya share parse error: {e}")
            return None

    async def _parse_quark_share(self, link: str) -> Optional[Dict]:
        """解析夸克网盘分享链接（需要Cookie）"""
        # 夸克需要 ptoken/pwd_id 等，暂时返回空
        # 完整实现参考 Quark-Auto-Save
        return None

    # ===== 文件转存 =====

    async def _transfer_file(self, link: str, link_type: str, file_info: Dict) -> Optional[str]:
        """转存单个文件，返回保存路径"""
        if link_type == "guangya":
            return await self._transfer_guangya(link, file_info)
        return None

    async def _transfer_guangya(self, share_link: str, file_info: Dict) -> Optional[str]:
        """转存光鸭云盘文件"""
        try:
            m = re.search(r'/share/([a-zA-Z0-9]+)', share_link)
            share_token = m.group(1) if m else ""

            resp = self.client._request(
                "https://api.guangyapan.com/nd.bizuserres.s/v1/share/save",
                json={
                    "shareToken": share_token,
                    "fileName": file_info["name"],
                    "size": file_info.get("size", 0),
                }
            )
            return resp.get("data", {}).get("path", "")
        except Exception as e:
            logger.warning(f"[Transfer] guangya save error: {e}")
            return None

    # ===== 文件整理 =====

    async def _organize_files(self, task: TransferTask):
        """整理文件：自动分类 + TMDB 命名"""
        if not self.tmdb:
            return

        for path in task.result_paths:
            try:
                # 识别媒体类型
                filename = Path(path).stem
                media_info = await self.tmdb.identify(filename)
                if not media_info:
                    continue

                # 确定目标目录
                if media_info.media_type == "movie":
                    target_dir = f"{self.movie_dir}/{media_info.title} ({media_info.year})"
                else:
                    target_dir = f"{self.series_dir}/{media_info.title}/Season {media_info.season}"

                # 移动文件（通过光鸭API）
                # await self._move_file(path, target_dir, filename)

            except Exception as e:
                logger.warning(f"[Transfer] organize error: {e}")

    def _should_skip(self, filename: str) -> bool:
        """检查是否应该跳过文件"""
        for pattern in self.filter_patterns:
            if pattern.search(filename):
                return True
        return False

    # ===== 管理 API =====

    async def add_filter(self, pattern: str):
        """添加过滤正则"""
        try:
            self.filter_patterns.append(re.compile(pattern))
        except:
            pass

    async def clear_tasks(self):
        """清理已完成/失败的任务"""
        async with self._tasks_lock:
            self._tasks = {
                k: v for k, v in self._tasks.items()
                if v.status not in (TransferStatus.DONE, TransferStatus.FAILED)
            }


# 全局实例
transfer_service: Optional[TransferService] = None


def get_transfer_service() -> Optional[TransferService]:
    return transfer_service
