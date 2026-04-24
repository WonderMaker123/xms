"""
API 路由 - xms 完整 API
包含：登录、文件、STRM、定时任务、Webhook、插件、设置
"""
import time
import uuid
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel
from typing import Optional, List
from ..main import get_client, get_strm_service, get_scheduler, get_webhook, get_plugin_manager
from ..config import config

router = APIRouter(prefix="/api")


# ===== 登录 API =====

@router.post("/auth/phone/send_code")
async def send_code(phone: str, captcha_token: Optional[str] = None):
    client = get_client()
    r = client.login_sms_init(phone, captcha_token)
    return r

@router.post("/auth/phone/verify")
async def verify_code(verification_id: str, verification_code: str):
    client = get_client()
    return client.login_sms_verify(verification_id, verification_code)

@router.post("/auth/phone/signin")
async def signin(verification_code: str, verification_token: str, username: str, captcha_token: str = ""):
    client = get_client()
    r = client.login_sms_signin(verification_code, verification_token, username, captcha_token)
    config.guangya_access_token = client.access_token
    config.guangya_refresh_token = client.refresh_token
    config.save()
    return {"success": True, "access_token": client.access_token}

@router.post("/auth/qrcode/generate")
async def generate_qrcode():
    client = get_client()
    return client.qrcode_generate()

@router.post("/auth/qrcode/check")
async def check_qrcode(device_code: str):
    client = get_client()
    r = client.qrcode_check(device_code)
    if r.get("access_token"):
        config.guangya_access_token = client.access_token
        config.guangya_refresh_token = client.refresh_token
        config.save()
    return r

@router.get("/auth/status")
async def auth_status():
    client = get_client()
    if not client.access_token:
        return {"logged_in": False}
    try:
        info = client.user_info()
        return {"logged_in": True, "user": info}
    except Exception:
        return {"logged_in": False}


# ===== 文件 API =====

@router.get("/files")
async def list_files(parent_id: Optional[str] = None, page: int = 0, page_size: int = 50):
    client = get_client()
    return client.fs_files(parent_id=parent_id, page=page, page_size=page_size)

@router.get("/files/video")
async def list_videos(parent_id: Optional[str] = None, page: int = 0, page_size: int = 50):
    client = get_client()
    return client.fs_video_list(parent_id=parent_id, page=page, page_size=page_size)


# ===== STRM API =====

@router.post("/strm/sync")
async def sync_strm(background_tasks: BackgroundTasks, parent_id: Optional[str] = None, folder_path: str = "", depth: int = 3):
    service = get_strm_service()
    async def _do():
        def prog(name, count):
            print(f"[SYNC] {name} ({count})")
        return service.sync_folder(parent_id=parent_id, folder_path=folder_path, depth=depth, progress_callback=prog)
    import asyncio
    result = await asyncio.create_task(_do())
    return {"status": "ok", "success": result.get("success", 0), "errors": result.get("errors", 0)}

@router.get("/strm/status")
async def strm_status():
    service = get_strm_service()
    from ..stream_cache import stream_cache
    strm_dir = service.output_dir
    files = list(strm_dir.glob("*.strm")) if strm_dir.exists() else []
    return {
        "count": len(files),
        "last_sync": config.last_sync or "从未",
        "cache_count": len(stream_cache.url_cache),
    }

@router.post("/strm/refresh")
async def refresh_strm(file_id: str, file_path: str):
    service = get_strm_service()
    path = service.refresh_file(file_id, file_path)
    return {"success": True, "path": str(path)}


# ===== 缓存 API =====

@router.get("/cache/stats")
async def cache_stats():
    from ..stream_cache import stream_cache
    return {"cached_count": len(stream_cache.url_cache), "max_size": stream_cache.url_cache.maxsize}

@router.post("/cache/clear")
async def cache_clear():
    from ..stream_cache import stream_cache
    stream_cache.clear()
    return {"status": "ok"}


# ===== 定时任务 API =====

@router.get("/scheduler/tasks")
async def list_tasks():
    scheduler = get_scheduler()
    tasks = scheduler.get_tasks() if scheduler else []
    return {
        "tasks": [
            {
                "id": t.id,
                "name": t.name,
                "cron": t.cron,
                "depth": t.depth,
                "enabled": t.enabled,
                "last_run": t.last_run,
                "parent_id": t.parent_id,
                "folder_path": t.folder_path,
            }
            for t in tasks
        ]
    }

@router.post("/scheduler/tasks")
async def create_task(name: str, parent_id: Optional[str], folder_path: str, cron: str, depth: int = 3):
    from ..services.scheduler import SyncTask
    scheduler = get_scheduler()
    task = SyncTask(
        id=str(uuid.uuid4())[:8],
        name=name,
        parent_id=parent_id,
        folder_path=folder_path,
        cron=cron,
        depth=depth,
    )
    scheduler.add_task(task)
    return {"id": task.id}

@router.post("/scheduler/tasks/{task_id}/toggle")
async def toggle_task(task_id: str, enabled: bool):
    scheduler = get_scheduler()
    for t in scheduler.get_tasks():
        if t.id == task_id:
            t.enabled = enabled
            scheduler._schedule_task(t)
    return {"status": "ok"}

@router.post("/scheduler/tasks/{task_id}/run")
async def run_task(task_id: str):
    scheduler = get_scheduler()
    for t in scheduler.get_tasks():
        if t.id == task_id:
            t.last_run = time.time()
    return {"status": "ok"}

@router.delete("/scheduler/tasks/{task_id}")
async def delete_task(task_id: str):
    scheduler = get_scheduler()
    scheduler.remove_task(task_id)
    return {"status": "ok"}


# ===== Webhook API =====

@router.get("/webhook/list")
async def list_webhooks():
    wh = get_webhook()
    hooks = wh.get_webhooks() if wh else []
    return {
        "webhooks": [
            {"id": h.id, "name": h.name, "url": h.url, "events": h.events, "enabled": h.enabled}
            for h in hooks
        ]
    }

@router.post("/webhook")
async def create_webhook(name: str, url: str, events: List[str]):
    from ..services.webhook import Webhook
    wh = get_webhook()
    hook = Webhook(id=str(uuid.uuid4())[:8], name=name, url=url, events=events)
    wh.add_webhook(hook)
    return {"id": hook.id}

@router.delete("/webhook/{hook_id}")
async def delete_webhook(hook_id: str):
    wh = get_webhook()
    wh.remove_webhook(hook_id)
    return {"status": "ok"}


# ===== 插件 API =====

@router.get("/plugin/list")
async def list_plugins():
    pm = get_plugin_manager()
    plugins = pm.get_plugins() if pm else []
    return {
        "plugins": [
            {"id": p.id, "name": p.name, "type": p.type, "enabled": p.enabled, "config": p.config}
            for p in plugins
        ]
    }

@router.post("/plugin")
async def create_plugin(name: str, plugin_type: str, config: dict):
    from ..services.plugin import Plugin, PluginType
    pm = get_plugin_manager()
    plugin = Plugin(
        id=str(uuid.uuid4())[:8],
        name=name,
        type=PluginType(plugin_type),
        config=config,
    )
    pm.register(plugin)
    return {"id": plugin.id}

@router.delete("/plugin/{plugin_id}")
async def delete_plugin(plugin_id: str):
    pm = get_plugin_manager()
    pm.unregister(plugin_id)
    return {"status": "ok"}


# ===== 设置 API =====

@router.get("/config")
async def get_config():
    return {
        "username": config.username,
        "strm_dir": config.strm_output_dir,
        "media_root": config.media_root,
        "tmdb_key": getattr(config, 'tmdb_key', ''),
    }

@router.post("/config")
async def update_config(
    username: Optional[str] = None,
    password: Optional[str] = None,
    strm_dir: Optional[str] = None,
    tmdb_key: Optional[str] = None,
):
    if username: config.username = username
    if password: config.password = password
    if strm_dir: config.strm_output_dir = strm_dir
    if hasattr(config, 'tmdb_key') and tmdb_key: config.tmdb_key = tmdb_key
    config.save()
    return {"status": "ok"}
