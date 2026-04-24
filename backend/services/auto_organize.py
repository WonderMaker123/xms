"""
CMS 自动整理服务 - xms
功能：从待整理文件夹读取视频 → TMDB 识别 → 按规则重命名+分类
参考 CloudMediaSynC auto-organize 语法

重命名块语法：
  {变量名}  → 直接取值
  <{变量名}> → 块：当变量不为空时，包含块内容
  <-{变量名}> → 同上，但用 - 分隔符
"""
import re
import os
import hashlib
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ============ 工具函数 ============

def sha1_of_file(path: Path) -> str:
    """计算文件 SHA1"""
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def extract_episode_info(filename: str) -> Optional[Tuple[str, int, int]]:
    """
    从文件名提取剧集信息
    返回 (title, season, episode) 或 None
    支持格式：S01E01, 1x01, E01, 第1集
    """
    patterns = [
        r'(?P<title>.+?)[.\s_-]S(?P<season>\d{1,4})E(?P<episode>\d{1,4})',
        r'(?P<title>.+?)[.\s_-](?P<season>\d{1,4})x(?P<episode>\d{1,4})',
        r'(?P<title>.+?)[.\s_-]E(?P<episode>\d{1,4})',
        r'(?P<title>.+?)[.\s_第](?P<episode>\d{1,4})集',
    ]
    for p in patterns:
        m = re.search(p, filename, re.IGNORECASE)
        if m:
            d = m.groupdict()
            title = re.sub(r'[.\s_-]+$', '', d['title']).strip()
            ep = int(re.sub(r'\D', '', d.get('episode', '0')))
            season = int(re.sub(r'\D', '', d.get('season', '1') or '1'))
            return title, season, ep
    return None


def extract_year(filename: str) -> Optional[int]:
    """从文件名提取年份"""
    m = re.search(r'\[?(\d{4})\]?', filename)
    if m:
        y = int(m.group(1))
        if 1900 <= y <= 2100:
            return y
    return None


# ============ 变量表（来自 CMS） ============

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts', '.mts', '.m2ts'}


def build_organize_path(
    media_type: str,
    title: str,
    year: Optional[int],
    season: int,
    episode: int,
    folder_rule: str,
    file_rule: str,
    extra: Dict,
) -> Tuple[str, str]:
    """
    根据规则生成目标路径
    folder_rule: 文件夹命名规则
    file_rule: 文件命名规则
    返回 (folder_path, filename)
    """
    # 计算首字母
    first_letter = title[0].upper() if title else '#'

    # 构建文件夹名
    folder_name = folder_rule
    folder_name = folder_name.replace('{first_letter}', first_letter)
    folder_name = folder_name.replace('{title}', title)
    folder_name = folder_name.replace('{year}', str(year) if year else '')
    folder_name = folder_name.replace('{tmdb_id}', str(extra.get('tmdb_id', '')))

    # 构建文件名
    file_name = file_rule
    file_name = file_name.replace('{title}', title)
    file_name = file_name.replace('{year}', str(year) if year else '')
    file_name = file_name.replace('{season}', str(season).zfill(2))
    file_name = file_name.replace('{episode}', str(episode).zfill(2))
    file_name = file_name.replace('{tmdb_id}', str(extra.get('tmdb_id', '')))

    # 处理块语法 <.{var}>
    def process_block(text):
        # 匹配 <.{var}> 块
        def replace_block(m):
            inner = m.group(1)
            # 找 {var} 引用
            var_m = re.search(r'\{(\w+)\}', inner)
            if var_m:
                var_name = var_m.group(1)
                val = extra.get(var_name, '')
                if val:
                    # 提取块内容（去掉 {var} 部分，用 . 连接）
                    parts = re.sub(r'\{(\w+)\}', '', inner).split('.')
                    return ''.join(p for p in parts if p)
                return ''
            return inner

        # 找 <...> 块
        while True:
            m = re.search(r'<([^>]+)>', text)
            if not m:
                break
            result = process_block(m.group(1))
            if result:
                text = text[:m.start()] + result + text[m.end():]
            else:
                text = text[:m.start()] + text[m.end():]
        return text

    # 处理 - 分隔的块 <-{var}>
    def process_dash_block(text):
        while True:
            m = re.search(r'<-( \{\w+\} )?([^-]+)>', text)
            if not m:
                break
            var_part = m.group(1) or ''
            content = m.group(2)
            var_m = re.search(r'\{(\w+)\}', var_part)
            if var_m:
                var_name = var_m.group(1)
                val = extra.get(var_name, '')
                if val:
                    text = text[:m.start()] + content.strip() + text[m.end():]
                else:
                    text = text[:m.start()] + text[m.end():]
            else:
                text = text[:m.start()] + content.strip() + text[m.end():]
        return text

    folder_name = process_block(folder_name)
    file_name = process_dash_block(process_block(file_name))

    # 清理非法字符
    folder_name = re.sub(r'[<>:"/\\|?*]', '_', folder_name)
    file_name = re.sub(r'[<>:"/\\|?*]', '_', file_name)

    if media_type == 'movie':
        folder_path = f"电影/{folder_name}"
    else:
        folder_path = f"剧集/{folder_name}/Season {season}"

    return folder_path, file_name


# ============ TMDB 识别 ============

async def tmdb_search(client, query: str, year: Optional[int], media_type: str) -> Optional[Dict]:
    """用 TMDB 搜索媒体信息"""
    try:
        search_type = 'movie' if media_type == 'movie' else 'tv'
        params = {'query': query, 'include_adult': False}
        if year:
            params['year'] = year
        result = await client.search_multi(query, year, search_type)
        return result
    except Exception as e:
        logger.error(f"TMDB search failed: {e}")
        return None


# ============ 自动整理服务 ============

@dataclass
class OrganizeTask:
    id: str
    status: str = 'pending'  # pending / running / done / failed
    source_file: str = ''
    target_path: str = ''
    media_type: str = 'movie'
    title: str = ''
    year: int = 0
    season: int = 1
    episode: int = 0
    error: str = ''
    created_at: float = field(default_factory=lambda: __import__('time').time())


class AutoOrganizeService:
    """
    自动整理服务
    流程：待整理文件夹 → 检查sha1/EMBY → TMDB识别 → 执行重命名 → 移动到媒体库
    """

    def __init__(
        self,
        source_cid: str,          # 待整理文件夹 CID
        existing_cid: str,         # 已存在文件夹 CID
        redundant_cid: str,        # 冗余文件夹 CID
        folder_rule: str,          # 文件夹命名规则
        file_rule: str,           # 文件命名规则
        guangya_client,           # 光鸭客户端
        tmdb_client,             # TMDB 客户端
        emby_client,             # Emby 客户端（可选）
        sync_db: Dict,            # 同步记录 {sha1: {path, type, title, year}}
    ):
        self.source_cid = source_cid
        self.existing_cid = existing_cid
        self.redundant_cid = redundant_cid
        self.folder_rule = folder_rule
        self.file_rule = file_rule
        self.guangya = guangya_client
        self.tmdb = tmdb_client
        self.emby = emby_client
        self.sync_db = sync_db
        self._lock = asyncio.Lock()
        self._tasks: Dict[str, OrganizeTask] = {}

    async def scan_source_folder(self) -> List[Dict]:
        """扫描待整理文件夹，返回所有视频文件"""
        files = []
        try:
            result = await self.guangya.list_dir(self.source_cid)
            for item in result.get('file_list', []):
                if item.get('type') == 'file':
                    ext = Path(item['name']).suffix.lower()
                    if ext in VIDEO_EXTENSIONS:
                        files.append(item)
        except Exception as e:
            logger.error(f"scan source folder failed: {e}")
        return files

    async def organize_file(self, file_item: Dict) -> OrganizeTask:
        """整理单个文件"""
        task = OrganizeTask(
            id=file_item['file_id'],
            source_file=file_item['name'],
        )
        async with self._lock:
            self._tasks[task.id] = task

        try:
            task.status = 'running'

            # 获取直链下载文件
            file_url = await self.guangya.get_direct_link(file_item['file_id'])
            if not file_url:
                raise Exception("无法获取直链")

            # 下载到临时文件计算 sha1
            temp_path = f"/tmp/organize_{task.id}"
            await self._download_file(file_url, temp_path)

            sha1 = sha1_of_file(Path(temp_path))

            # 检查是否已同步过
            if sha1 in self.sync_db:
                # 移动到已存在文件夹
                await self.guangya.move_to_folder(file_item['file_id'], self.existing_cid)
                task.status = 'done'
                task.title = self.sync_db[sha1].get('title', '')
                return task

            # 提取文件名信息
            fname = file_item['name']
            ext = Path(fname).suffix

            ep_info = extract_episode_info(fname)
            if ep_info:
                task.media_type = 'series'
                title, task.season, task.episode = ep_info
            else:
                task.media_type = 'movie'
                title = re.sub(r'\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|mts|m2ts)$', '', fname, flags=re.IGNORECASE).strip()
                title = re.sub(r'\[.*?\]', '', title).strip()

            task.year = extract_year(fname) or 0
            task.title = title

            # TMDB 识别
            tmdb_info = await tmdb_search(self.tmdb, title, task.year if task.year else None, task.media_type)
            if not tmdb_info:
                # 识别失败，移到冗余文件夹
                await self.guangya.move_to_folder(file_item['file_id'], self.redundant_cid)
                task.status = 'failed'
                task.error = 'TMDB 识别失败'
                return task

            task.year = tmdb_info.get('year', task.year)
            extra = {
                'tmdb_id': tmdb_info.get('tmdb_id', ''),
                'resource_pix': tmdb_info.get('resolution', ''),
                'fps': tmdb_info.get('fps', ''),
                'resource_version': tmdb_info.get('version', ''),
                'resource_source': tmdb_info.get('source', ''),
                'resource_type': tmdb_info.get('type', ''),
                'resource_effect': tmdb_info.get('effect', ''),
                'video_encode': tmdb_info.get('video_encode', ''),
                'audio_encode': tmdb_info.get('audio_encode', ''),
                'resource_team': tmdb_info.get('team', ''),
            }

            # 构建目标路径
            folder_path, file_name = build_organize_path(
                task.media_type, task.title, task.year if task.year else None,
                task.season, task.episode,
                self.folder_rule, self.file_rule, extra
            )

            # 在光鸭云盘创建文件夹
            target_cid = await self.guangya.create_folder(folder_path, parent_cid=None)
            if not target_cid:
                # 尝试获取已存在的文件夹
                target_cid = await self.guangya.get_folder_cid(folder_path)

            # 上传文件到目标文件夹
            target_name = f"{file_name}{ext}"
            await self.guangya.upload_file(temp_path, target_cid, target_name)

            # 删除源文件
            await self.guangya.delete_file(file_item['file_id'])

            # 记录 sha1
            self.sync_db[sha1] = {
                'path': f"{folder_path}/{target_name}",
                'type': task.media_type,
                'title': task.title,
                'year': task.year,
            }

            task.target_path = f"{folder_path}/{target_name}"
            task.status = 'done'

        except Exception as e:
            task.status = 'failed'
            task.error = str(e)
            logger.error(f"organize file {file_item['name']} failed: {e}")

        finally:
            async with self._lock:
                self._tasks[task.id] = task
            # 清理临时文件
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass

        return task

    async def _download_file(self, url: str, dest: str):
        """下载文件到本地"""
        import httpx
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
            async with client.stream('GET', url) as resp:
                with open(dest, 'wb') as f:
                    async for chunk in resp.aiter_bytes(8192):
                        f.write(chunk)

    async def run(self, limit: int = 50) -> List[OrganizeTask]:
        """执行自动整理（最多处理 limit 个文件）"""
        files = await self.scan_source_folder()
        if not files:
            return []

        files = files[:limit]
        tasks = []
        for f in files:
            t = await self.organize_file(f)
            tasks.append(t)
        return tasks

    def get_tasks(self) -> List[OrganizeTask]:
        return list(self._tasks.values())
