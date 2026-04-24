"""
STRM 增量监控服务 - xms
功能：监听媒体文件夹变化 → 检测新视频 → 生成 STRM → 触发 Emby 入库

CMS 核心逻辑：
1. 记录每个文件的 sha1 + mtime，建立本地索引
2. 定时对比光鸭云盘文件列表，找出新增/删除
3. 新增文件 → 生成 STRM + 记录索引
4. 删除文件 → 删除 STRM + 更新索引
5. 触发 Emby 扫描对应目录
"""
import asyncio
import time
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts', '.mts', '.m2ts', '.srt', '.ass'}


class ChangeType(Enum):
    ADDED = "added"
    DELETED = "deleted"
    UNCHANGED = "unchanged"


@dataclass
class MediaFile:
    file_id: str
    name: str
    path: str
    size: int
    sha1: str = ""
    mtime: float = 0
    change: ChangeType = ChangeType.UNCHANGED


@dataclass
class WatchEvent:
    id: str
    type: str  # "add" / "delete"
    file_id: str
    name: str
    path: str
    strm_path: str = ""
    emby_notified: bool = False
    status: str = "pending"  # pending / done / failed
    error: str = ""
    created_at: float = field(default_factory=time.time)


class StrmWatchService:
    """
    STRM 增量监控服务

    工作流程：
    1. 启动时加载索引文件（记录每个文件的 sha1）
    2. 每 interval 秒扫描光鸭云盘指定目录
    3. 对比索引，发现新增/删除文件
    4. 新增视频 → 生成 STRM → 触发 Emby 扫描 → 更新索引
    5. 删除视频 → 删除 STRM → 从索引移除
    """

    def __init__(
        self,
        watch_cid: str,            # 监控的云盘文件夹 CID
        strm_output_dir: str,       # STRM 输出目录
        guangya_client,             # 光鸭客户端
        strm_service,              # STRM 生成服务
        emby_client,               # Emby 客户端（可选）
        interval: int = 30,         # 扫描间隔（秒）
    ):
        self.watch_cid = watch_cid
        self.strm_output_dir = Path(strm_output_dir)
        self.guangya = guangya_client
        self.strm_service = strm_service
        self.emby = emby_client
        self.interval = interval

        self._index_file = Path("/app/config/strm_watch_index.json")
        self._events_file = Path("/app/config/strm_watch_events.json")

        # 索引：file_id -> {name, path, sha1, mtime}
        self._index: Dict[str, Dict] = {}
        # 待处理事件
        self._pending_events: List[WatchEvent] = []
        # 运行状态
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

        self._load_index()
        self._load_events()

    # ============ 索引管理 ============

    def _load_index(self):
        if self._index_file.exists():
            try:
                self._index = json.loads(self._index_file.read_text())
                logger.info(f"Loaded watch index: {len(self._index)} entries")
            except Exception as e:
                logger.error(f"Failed to load index: {e}")
                self._index = {}

    def _save_index(self):
        self._index_file.parent.mkdir(parents=True, exist_ok=True)
        self._index_file.write_text(json.dumps(self._index, ensure_ascii=False, indent=2))

    def _load_events(self):
        if self._events_file.exists():
            try:
                data = json.loads(self._events_file.read_text())
                self._pending_events = [WatchEvent(**e) for e in data]
            except Exception:
                self._pending_events = []

    def _save_events(self):
        self._events_file.parent.mkdir(parents=True, exist_ok=True)
        data = [vars(e) for e in self._pending_events[-100:]]  # 只保留最近100条
        self._events_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def _sha1_file(self, path: Path) -> str:
        h = hashlib.sha1()
        try:
            with open(path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    h.update(chunk)
        except Exception:
            pass
        return h.hexdigest()

    # ============ 核心：检测变化 ============

    async def _scan_remote_files(self) -> Dict[str, MediaFile]:
        """扫描光鸭云盘监控目录，返回 file_id -> MediaFile"""
        files = {}
        try:
            result = await self.guangya.list_dir(self.watch_cid)
            for item in result.get('file_list', []):
                if item.get('type') == 'file':
                    ext = Path(item['name']).suffix.lower()
                    if ext in VIDEO_EXTENSIONS:
                        mf = MediaFile(
                            file_id=item['file_id'],
                            name=item['name'],
                            path=item.get('path', ''),
                            size=item.get('size', 0),
                            mtime=item.get('mtime', 0),
                        )
                        files[mf.file_id] = mf
        except Exception as e:
            logger.error(f"Scan remote files failed: {e}")
        return files

    async def _detect_changes(self, remote_files: Dict[str, MediaFile]) -> List[MediaFile]:
        """对比索引，返回有变化的文件列表"""
        changed = []
        remote_ids = set(remote_files.keys())
        indexed_ids = set(self._index.keys())

        # 新增文件
        for fid in remote_ids - indexed_ids:
            mf = remote_files[fid]
            mf.change = ChangeType.ADDED
            changed.append(mf)

        # 删除文件
        for fid in indexed_ids - remote_ids:
            mf = MediaFile(
                file_id=fid,
                name=self._index[fid]['name'],
                path=self._index[fid]['path'],
                size=0,
                change=ChangeType.DELETED,
            )
            changed.append(mf)

        return changed

    # ============ STRM 生成 + Emby 入库 ============

    async def _handle_added(self, mf: MediaFile) -> Optional[WatchEvent]:
        """处理新增文件：生成 STRM + 触发 Emby"""
        event = WatchEvent(
            id=f"add_{mf.file_id}_{int(time.time()*1000)}",
            type="add",
            file_id=mf.file_id,
            name=mf.name,
            path=mf.path,
        )
        async with self._lock:
            self._pending_events.append(event)
            self._save_events()

        try:
            # 获取直链
            direct_url = await self.guangya.get_direct_link(mf.file_id)
            if not direct_url:
                raise Exception("无法获取直链")

            # 构造相对路径（从 strm_output_dir 的父目录开始的光鸭路径）
            # 假设 strm_output_dir = /app/media/strm，媒体在 /app/media/电影/xxx.mp4
            # STRM 放在 /app/media/strm/电影/xxx.strm，内容是直链
            rel_path = self._build_strm_rel_path(mf.name, mf.path)
            strm_path = self.strm_output_dir / rel_path
            strm_content = direct_url

            # 生成 STRM 文件
            strm_path.parent.mkdir(parents=True, exist_ok=True)
            strm_path.write_text(strm_content)
            event.strm_path = str(strm_path)

            # 记录索引
            self._index[mf.file_id] = {
                'name': mf.name,
                'path': mf.path,
                'strm_path': str(strm_path),
                'sha1': mf.sha1 or '',
                'mtime': mf.mtime,
            }
            self._save_index()

            # 触发 Emby 扫描（秒级入库）
            await self._trigger_emby_scan(strm_path.parent)

            event.status = "done"
            event.emby_notified = True

        except Exception as e:
            event.status = "failed"
            event.error = str(e)
            logger.error(f"Handle added file {mf.name} failed: {e}")

        async with self._lock:
            self._save_events()
        return event

    def _build_strm_rel_path(self, name: str, remote_path: str) -> str:
        """根据远程路径构建 STRM 相对路径"""
        # remote_path 形如 "/电影/阿凡达.mp4"
        # 去掉根目录前缀，转成 STRM 路径
        if remote_path.startswith('/'):
            remote_path = remote_path[1:]
        # 改扩展名为 .strm
        base = Path(remote_path).with_suffix('')
        return f"{base}.strm"

    async def _handle_deleted(self, mf: MediaFile) -> WatchEvent:
        """处理删除文件：删除 STRM + 更新索引"""
        event = WatchEvent(
            id=f"del_{mf.file_id}_{int(time.time()*1000)}",
            type="delete",
            file_id=mf.file_id,
            name=mf.name,
            path=mf.path,
        )

        try:
            if mf.file_id in self._index:
                strm_path = self._index[mf.file_id].get('strm_path', '')
                if strm_path and Path(strm_path).exists():
                    Path(strm_path).unlink()
                    event.strm_path = strm_path

                del self._index[mf.file_id]
                self._save_index()

                # 触发 Emby 扫描
                if strm_path:
                    await self._trigger_emby_scan(Path(strm_path).parent)

            event.status = "done"
        except Exception as e:
            event.status = "failed"
            event.error = str(e)
            logger.error(f"Handle deleted file {mf.name} failed: {e}")

        async with self._lock:
            self._pending_events.append(event)
            self._save_events()
        return event

    async def _trigger_emby_scan(self, folder_path: Path):
        """触发 Emby 扫描指定目录，实现秒级入库"""
        if not self.emby or not self.emby.api_key:
            return

        try:
            import httpx
            # Emby 触发媒体库扫描
            await self.emby.trigger_library_scan(str(folder_path))
            logger.info(f"Emby scan triggered for {folder_path}")
        except Exception as e:
            logger.error(f"Emby scan trigger failed: {e}")

    # ============ 轮询主循环 ============

    async def _poll_loop(self):
        """定时扫描 + 增量对比 + 处理变化"""
        logger.info(f"STRM watch started, interval={self.interval}s")
        while self._running:
            try:
                remote_files = await self._scan_remote_files()
                changes = await self._detect_changes(remote_files)

                for mf in changes:
                    if mf.change == ChangeType.ADDED:
                        await self._handle_added(mf)
                    elif mf.change == ChangeType.DELETED:
                        await self._handle_deleted(mf)

            except Exception as e:
                logger.error(f"Poll loop error: {e}")

            await asyncio.sleep(self.interval)

    # ============ 控制接口 ============

    async def start(self):
        """启动监控"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("STRM watch service started")

    async def stop(self):
        """停止监控"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("STRM watch service stopped")

    def is_running(self) -> bool:
        return self._running

    def get_events(self, limit: int = 50) -> List[WatchEvent]:
        return self._pending_events[-limit:]

    def get_index_count(self) -> int:
        return len(self._index)

    async def manual_scan(self) -> Dict:
        """手动触发一次全量扫描"""
        remote_files = await self._scan_remote_files()
        changes = await self._detect_changes(remote_files)
        results = {'added': 0, 'deleted': 0}
        for mf in changes:
            if mf.change == ChangeType.ADDED:
                await self._handle_added(mf)
                results['added'] += 1
            elif mf.change == ChangeType.DELETED:
                await self._handle_deleted(mf)
                results['deleted'] += 1
        return results
