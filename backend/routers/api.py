"""
API 路由 - xms v3 完整 API
包含：账号登录、光鸭登录、文件、STRM、定时任务、Webhook、插件、设置
CMS：订阅、下载、历史、统计
TG：TG机器人配置
转存：链接转存任务
预加载：Emby预加载控制
"""
import time
import uuid
import hashlib
import asyncio
from pathlib import Path
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query, Header
from pydantic import BaseModel
from typing import Optional, List
from ..main import (
    get_client, get_strm_service, get_scheduler, get_webhook,
    get_plugin_manager, get_tmdb, get_emby_preload_handler,
    get_telegram_bot, get_transfer, get_cms,
)
from ..config import config

router = APIRouter(prefix="/api")

# ===== 简单会话认证 =====
_session_store: dict = {}  # token -> {username, expires}


def _make_token(username: str) -> str:
    token = hashlib.sha256(f"{username}{time.time()}".encode()).hexdigest()[:32]
    _session_store[token] = {"username": username, "expires": time.time() + 86400 * 7}
    return token


def _verify_token(token: str) -> bool:
    info = _session_store.get(token)
    if not info or info["expires"] < time.time():
        return False
    return True


# ===== 账号密码登录 =====

@router.post("/auth/login")
async def admin_login(username: str, password: str):
    """账号密码登录（管理后台）"""
    if username != config.username or password != config.password:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = _make_token(username)
    return {"token": token, "username": username}


@router.post("/auth/logout")
async def admin_logout(token: str = Header(...)):
    if token in _session_store:
        del _session_store[token]
    return {"status": "ok"}


@router.get("/auth/me")
async def admin_me(token: str = Header(...)):
    if not _verify_token(token):
        raise HTTPException(status_code=401, detail="未登录")
    info = _session_store.get(token, {})
    return {"username": info.get("username", "")}


# ===== 光鸭云盘登录 =====

@router.post("/guangya/qrcode/generate")
async def generate_qrcode(token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    client = get_client()
    return client.qrcode_generate()


@router.post("/guangya/qrcode/check")
async def check_qrcode(device_code: str, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    client = get_client()
    r = client.qrcode_check(device_code)
    if r.get("access_token"):
        config.guangya_access_token = client.access_token
        config.guangya_refresh_token = client.refresh_token
        config.guangya_device_id = client.device_id
        config.save()
    return r


@router.post("/guangya/phone/send_code")
async def send_code(phone: str, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    client = get_client()
    return client.login_sms_init(phone)


@router.post("/guangya/phone/verify")
async def verify_code(verification_id: str, verification_code: str, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    client = get_client()
    return client.login_sms_verify(verification_id, verification_code)


@router.post("/guangya/phone/signin")
async def signin(verification_code: str, verification_token: str, username: str, captcha_token: str = "", token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    client = get_client()
    r = client.login_sms_signin(verification_code, verification_token, username, captcha_token)
    config.guangya_access_token = client.access_token
    config.guangya_refresh_token = client.refresh_token
    config.save()
    return {"success": True}


@router.get("/guangya/status")
async def guangya_status(token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    client = get_client()
    if not client.access_token:
        return {"logged_in": False}
    try:
        info = client.user_info()
        return {"logged_in": True, "user": info}
    except:
        return {"logged_in": False}


# ===== 文件 API =====

@router.get("/files")
async def list_files(parent_id: Optional[str] = None, page: int = 0, page_size: int = 50, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    client = get_client()
    return client.fs_files(parent_id=parent_id, page=page, page_size=page_size)


@router.get("/files/video")
async def list_videos(parent_id: Optional[str] = None, page: int = 0, page_size: int = 50, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    client = get_client()
    return client.fs_video_list(parent_id=parent_id, page=page, page_size=page_size)


# ===== STRM API =====

@router.post("/strm/sync")
async def sync_strm(parent_id: Optional[str] = None, folder_path: str = "", depth: int = 3, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    service = get_strm_service()
    result = service.sync_folder(parent_id=parent_id, folder_path=folder_path, depth=depth)
    return {"status": "ok", "success": result.get("success", 0), "errors": result.get("errors", 0)}


@router.get("/strm/status")
async def strm_status(token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    service = get_strm_service()
    from ..stream_cache import stream_cache
    strm_dir = service.output_dir
    files = list(Path(strm_dir).glob("**/*.strm")) if Path(strm_dir).exists() else []
    return {
        "count": len(files),
        "last_sync": getattr(config, 'last_sync', '从未'),
        "cache_count": len(stream_cache.url_cache),
    }


@router.post("/strm/refresh")
async def refresh_strm(file_id: str, file_path: str, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    service = get_strm_service()
    path = service.refresh_file(file_id, file_path)
    return {"success": True, "path": str(path)}


# ===== 缓存 API =====

@router.get("/cache/stats")
async def cache_stats(token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    from ..stream_cache import stream_cache
    return {"cached_count": len(stream_cache.url_cache), "max_size": stream_cache.url_cache.maxsize}


@router.post("/cache/clear")
async def cache_clear(token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    from ..stream_cache import stream_cache
    stream_cache.clear()
    return {"status": "ok"}


# ===== 预加载 API =====

@router.post("/preload/rebuild")
async def preload_rebuild(token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    preload = get_emby_preload_handler()
    if preload:
        preload.rebuild_index(config.strm_output_dir)
    return {"status": "ok"}


@router.post("/preload/item")
async def preload_item(item_id: str, title: str, media_type: str, season: int = 1, episode: int = 1, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    from ..stream_cache import stream_cache
    client = get_client()

    # 根据标题找 file_id
    preload = get_emby_preload_handler()
    file_ids = []
    if media_type == "Movie":
        file_ids = preload._find_movie(title, 0) if preload else []
    else:
        file_ids = preload._find_episodes(title, 0, season, episode, count=3) if preload else []

    if file_ids:
        def fetch_fn(fid):
            return client.get_stream_url(fid)
        stream_cache.prefetch(file_ids, fetch_fn)

    return {"status": "ok", "preloading": len(file_ids), "file_ids": file_ids[:5]}


# ===== 定时任务 API =====

@router.get("/scheduler/tasks")
async def list_tasks(token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    scheduler = get_scheduler()
    tasks = scheduler.get_tasks() if scheduler else []
    return {"tasks": [{"id": t.id, "name": t.name, "cron": t.cron, "depth": t.depth, "enabled": t.enabled, "last_run": t.last_run} for t in tasks]}


@router.post("/scheduler/tasks")
async def create_task(name: str, parent_id: Optional[str], folder_path: str, cron: str, depth: int = 3, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    from ..services.scheduler import SyncTask
    scheduler = get_scheduler()
    task = SyncTask(id=str(uuid.uuid4())[:8], name=name, parent_id=parent_id, folder_path=folder_path, cron=cron, depth=depth)
    scheduler.add_task(task)
    return {"id": task.id}


@router.post("/scheduler/tasks/{task_id}/toggle")
async def toggle_task(task_id: str, enabled: bool, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    scheduler = get_scheduler()
    for t in scheduler.get_tasks():
        if t.id == task_id:
            t.enabled = enabled
            scheduler._schedule_task(t)
    return {"status": "ok"}


@router.post("/scheduler/tasks/{task_id}/run")
async def run_task(task_id: str, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    scheduler = get_scheduler()
    for t in scheduler.get_tasks():
        if t.id == task_id:
            t.last_run = time.time()
    return {"status": "ok"}


@router.delete("/scheduler/tasks/{task_id}")
async def delete_task(task_id: str, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    scheduler = get_scheduler()
    scheduler.remove_task(task_id)
    return {"status": "ok"}


# ===== 转存 API =====

@router.post("/transfer/create")
async def transfer_create(link: str, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    transfer = get_transfer()
    if not transfer:
        raise HTTPException(status_code=500, detail="转存服务未初始化")
    task_id = await transfer.create_task(link, user_id=0, username="web")
    return {"task_id": task_id}


@router.get("/transfer/tasks")
async def transfer_list(token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    transfer = get_transfer()
    if not transfer:
        return {"tasks": []}
    tasks = await transfer.list_tasks()
    return {"tasks": [t.to_dict() for t in tasks]}


@router.get("/transfer/tasks/{task_id}")
async def transfer_get(task_id: str, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    transfer = get_transfer()
    task = await transfer.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task.to_dict()


# ===== CMS API =====

@router.get("/cms/stats")
async def cms_stats(token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    cms = get_cms()
    return await cms.get_stats()


@router.get("/cms/history")
async def cms_history(limit: int = 50, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    cms = get_cms()
    return {"history": await cms.get_history(limit)}


@router.get("/cms/subscriptions")
async def cms_subs(token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    cms = get_cms()
    subs = await cms.list_subscriptions()
    return {"subscriptions": [{"id": s.id, "title": s.title, "year": s.year, "media_type": s.media_type, "status": s.status.value, "created_at": s.created_at} for s in subs]}


@router.post("/cms/subscriptions")
async def cms_add_sub(title: str, media_type: str, year: int = 0, season: int = 1, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    cms = get_cms()
    sub_id = await cms.add_subscription(title, media_type, year, season=season)
    return {"id": sub_id}


@router.delete("/cms/subscriptions/{sub_id}")
async def cms_del_sub(sub_id: str, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    cms = get_cms()
    await cms.remove_subscription(sub_id)
    return {"status": "ok"}


@router.get("/cms/downloads")
async def cms_downloads(token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    cms = get_cms()
    dls = await cms.list_downloads()
    return {"downloads": [{"id": d.id, "title": d.title, "status": d.status, "progress": d.progress} for d in dls]}


@router.post("/emby/webhook")
async def emby_webhook(payload: dict):
    """Emby Webhook 端点 - 接收播放事件并触发预加载"""
    from .main import get_emby_preload_handler
    preload = get_emby_preload_handler()
    if preload:
        asyncio.create_task(preload.handle_webhook(payload))
    return {"status": "ok"}


# ===== TG 机器人 API =====

@router.get("/tg/config")
async def tg_config(token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    return {"enabled": config.tg.enabled, "token_set": bool(config.tg.token), "admin_count": len(config.tg.admin_ids)}


@router.post("/tg/config")
async def tg_update_config(enabled: bool, token: str = "", admin_ids: List[int] = [], token2: str = Header(...)):
    if not _verify_token(token2): raise HTTPException(status_code=401, detail="未登录")
    config.tg.enabled = enabled
    if token:
        config.tg.token = token
    config.tg.admin_ids = admin_ids
    config.save()
    return {"status": "ok"}


# ===== Webhook API =====

@router.get("/webhook/list")
async def list_webhooks(token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    wh = get_webhook()
    hooks = wh.get_webhooks() if wh else []
    return {"webhooks": [{"id": h.id, "name": h.name, "url": h.url, "events": h.events, "enabled": h.enabled} for h in hooks]}


@router.post("/webhook")
async def create_webhook(name: str, url: str, events: List[str], token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    from ..services.webhook import Webhook
    wh = get_webhook()
    hook = Webhook(id=str(uuid.uuid4())[:8], name=name, url=url, events=events)
    wh.add_webhook(hook)
    return {"id": hook.id}


@router.delete("/webhook/{hook_id}")
async def delete_webhook(hook_id: str, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    wh = get_webhook()
    wh.remove_webhook(hook_id)
    return {"status": "ok"}


# ===== 插件 API =====

@router.get("/plugin/list")
async def list_plugins(token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    pm = get_plugin_manager()
    plugins = pm.get_plugins() if pm else []
    return {"plugins": [{"id": p.id, "name": p.name, "type": p.type, "enabled": p.enabled, "config": p.config} for p in plugins]}


@router.post("/plugin")
async def create_plugin(name: str, plugin_type: str, config: dict, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    from ..services.plugin import Plugin, PluginType
    pm = get_plugin_manager()
    plugin = Plugin(id=str(uuid.uuid4())[:8], name=name, type=PluginType(plugin_type), config=config)
    pm.register(plugin)
    return {"id": plugin.id}


@router.delete("/plugin/{plugin_id}")
async def delete_plugin(plugin_id: str, token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    pm = get_plugin_manager()
    pm.unregister(plugin_id)
    return {"status": "ok"}


# ===== 设置 API =====

@router.get("/config")
async def get_config(token: str = Header(...)):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    return {
        "username": config.username,
        "strm_dir": config.strm_output_dir,
        "media_root": config.media_root,
        "tmdb_key": config.tmdb_key,
        "preload_enabled": config.preload_enabled,
        "cache_ttl": config.cache_ttl,
        "cache_max_size": config.cache_max_size,
    }


@router.post("/config")
async def update_config(
    username: Optional[str] = None,
    password: Optional[str] = None,
    strm_dir: Optional[str] = None,
    tmdb_key: Optional[str] = None,
    preload_enabled: Optional[bool] = None,
    token: str = Header(...),
):
    if not _verify_token(token): raise HTTPException(status_code=401, detail="未登录")
    if username: config.username = username
    if password: config.password = password
    if strm_dir: config.strm_output_dir = strm_dir
    if tmdb_key: config.tmdb_key = tmdb_key
    if preload_enabled is not None: config.preload_enabled = preload_enabled
    config.save()
    return {"status": "ok"}
