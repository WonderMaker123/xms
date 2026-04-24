"""
xms - 光鸭云盘 STRM + 302 播放 + CMS 管理平台
FastAPI 主程序 v3
"""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import config
from .guangya_client import GuangyaClient
from .strm_service import STRMService
from .stream_cache import stream_cache
from .services.scheduler import SchedulerService
from .services.webhook import WebhookService
from .services.plugin import PluginManager
from .services.metadata import TMDBService
from .services.emby_preload import EmbyWebhookHandler, emby_preload
from .services.telegram import TelegramBot
from .services.transfer import TransferService
from .services.cms import CMSServices
from .routers import api, stream


# 全局服务实例
_client: GuangyaClient = None
_strm_service: STRMService = None
_scheduler: SchedulerService = None
_webhook: WebhookService = None
_plugin_mgr: PluginManager = None
_tmdb: TMDBService = None
_emby_preload: EmbyWebhookHandler = None
_telegram_bot: TelegramBot = None
_transfer: TransferService = None
_cms: CMSServices = None
_tg_task: asyncio.Task = None


def get_client() -> GuangyaClient: return _client
def get_strm_service() -> STRMService: return _strm_service
def get_scheduler() -> SchedulerService: return _scheduler
def get_webhook() -> WebhookService: return _webhook
def get_plugin_manager() -> PluginManager: return _plugin_mgr
def get_tmdb() -> TMDBService: return _tmdb
def get_emby_preload_handler() -> EmbyWebhookHandler: return _emby_preload
def get_telegram_bot() -> TelegramBot: return _telegram_bot
def get_transfer() -> TransferService: return _transfer
def get_cms() -> CMSServices: return _cms


async def _setup_telegram():
    """启动 TG 机器人"""
    global _telegram_bot, _tg_task
    if not config.tg.enabled or not config.tg.token:
        return
    _telegram_bot = TelegramBot(
        token=config.tg.token,
        admin_ids=config.tg.admin_ids,
    )
    # 绑定转存回调
    if _transfer:
        _telegram_bot.set_transfer_callback(_transfer.create_task)

    async def tg_status():
        return {
            "strm_count": len(list(Path(config.strm_output_dir).glob("*.strm"))) if Path(config.strm_output_dir).exists() else 0,
            "cache_count": len(stream_cache.url_cache),
            "task_count": len(await _transfer.list_tasks()) if _transfer else 0,
            "preload_count": len(stream_cache.url_cache),
        }

    _telegram_bot.set_status_callback(tg_status)
    _tg_task = asyncio.create_task(_telegram_bot.start())


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client, _strm_service, _scheduler, _webhook, _plugin_mgr, _tmdb
    global _emby_preload, _transfer, _cms

    # 初始化客户端
    _client = GuangyaClient(
        access_token=config.guangya_access_token,
        refresh_token=config.guangya_refresh_token,
        device_id=config.guangya_device_id,
    )

    # STRM 服务
    _strm_service = STRMService(
        client=_client,
        output_dir=config.strm_output_dir,
        base_url=getattr(config, 'base_url', '') or "http://localhost:9528",
    )

    # 定时任务
    _scheduler = SchedulerService()
    _scheduler.set_strm_service(_strm_service)
    _scheduler.start()

    # Webhook + 插件 + TMDB
    _webhook = WebhookService()
    _plugin_mgr = PluginManager()
    _tmdb = TMDBService(api_key=config.tmdb_key or "")

    # Emby 预加载
    _emby_preload = EmbyWebhookHandler(
        strm_dir=config.strm_output_dir,
        base_url=getattr(config, 'base_url', '') or "http://localhost:9528",
    )
    _emby_preload.rebuild_index(config.strm_output_dir)
    # 绑定预加载回调：收到 Emby 事件后预热直链
    async def preload_callback(file_ids: list):
        def fetch_fn(fid):
            return _client.get_stream_url(fid)
        stream_cache.prefetch(file_ids, fetch_fn)

    _emby_preload.set_preload_callback(preload_callback)

    # 转存服务
    _transfer = TransferService(client=_client, tmdb_service=_tmdb)
    _transfer.set_emby_callback(_emby_preload.rebuild_index)

    # CMS
    _cms = CMSServices()

    # TG 机器人（最后启动）
    await _setup_telegram()

    yield

    # 关闭
    config.guangya_access_token = _client.access_token
    config.guangya_refresh_token = _client.refresh_token
    config.guangya_device_id = _client.device_id
    config.save()

    if _telegram_bot:
        await _telegram_bot.stop()
    if _tg_task:
        _tg_task.cancel()
    _scheduler.shutdown()


app = FastAPI(title="xms", description="光鸭云盘 STRM + 秒级起播 + CMS 管理平台", version="0.3.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.include_router(api.router, prefix="/api")
app.include_router(stream.router)

strm_path = Path(config.strm_output_dir)
strm_path.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(strm_path.parent)), name="media")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent.parent / "frontend")), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=9528, reload=True)
