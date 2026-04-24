"""
xms - 光鸭云盘 STRM + 302 播放服务
FastAPI 主程序
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
from .services.scheduler import SchedulerService
from .services.webhook import WebhookService
from .services.plugin import PluginManager
from .services.metadata import TMDBService
from .stream_cache import stream_cache
from .routers import api, stream


# 全局服务实例
_client: GuangyaClient = None
_strm_service: STRMService = None
_scheduler: SchedulerService = None
_webhook: WebhookService = None
_plugin_mgr: PluginManager = None
_tmdb: TMDBService = None


def get_client() -> GuangyaClient: return _client
def get_strm_service() -> STRMService: return _strm_service
def get_scheduler() -> SchedulerService: return _scheduler
def get_webhook() -> WebhookService: return _webhook
def get_plugin_manager() -> PluginManager: return _plugin_mgr
def get_tmdb() -> TMDBService: return _tmdb


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client, _strm_service, _scheduler, _webhook, _plugin_mgr, _tmdb

    _client = GuangyaClient(
        access_token=config.guangya_access_token,
        refresh_token=config.guangya_refresh_token,
        device_id=config.guangya_device_id,
    )
    _strm_service = STRMService(
        client=_client,
        output_dir=config.strm_output_dir,
        base_url=getattr(config, 'base_url', '') or "http://localhost:9528",
    )
    _scheduler = SchedulerService()
    _scheduler.set_strm_service(_strm_service)
    _scheduler.start()

    _webhook = WebhookService()
    _plugin_mgr = PluginManager()
    _tmdb = TMDBService(api_key=getattr(config, 'tmdb_key', '') or "")

    yield

    config.guangya_access_token = _client.access_token
    config.guangya_refresh_token = _client.refresh_token
    config.save()
    _scheduler.shutdown()


app = FastAPI(title="xms", description="光鸭云盘 STRM + 302 播放服务", version="0.2.0", lifespan=lifespan)

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
