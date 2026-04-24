"""
Webhook 服务 - 联动触发
支持：转存即触发、同步删除触发、自定义回调
"""
import asyncio
import httpx
from typing import Optional, Dict, List
from dataclasses import dataclass, field
from enum import Enum


class EventType(str):
    FILE_CREATED = "file.created"
    FILE_DELETED = "file.deleted"
    SYNC_COMPLETED = "sync.completed"
    SYNC_FAILED = "sync.failed"


@dataclass
class Webhook:
    id: str
    name: str
    url: str
    events: List[str]
    secret: str = ""
    enabled: bool = True
    retry: int = 3
    timeout: int = 10


class WebhookService:
    """Webhook 触发服务"""

    def __init__(self):
        self._hooks: Dict[str, Webhook] = {}
        self._http: Optional[httpx.AsyncClient] = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    def add_webhook(self, webhook: Webhook):
        self._hooks[webhook.id] = webhook

    def remove_webhook(self, hook_id: str):
        self._hooks.pop(hook_id, None)

    def get_webhooks(self) -> List[Webhook]:
        return list(self._hooks.values())

    async def trigger(self, event: str, data: dict):
        """触发 webhook"""
        for hook in self._hooks.values():
            if not hook.enabled:
                continue
            if event not in hook.events:
                continue
            asyncio.create_task(self._send(hook, event, data))

    async def _send(self, hook: Webhook, event: str, data: dict):
        """发送 webhook 请求"""
        payload = {
            "event": event,
            "time": int(asyncio.get_event_loop().time()),
            "data": data,
        }
        headers = {"Content-Type": "application/json"}
        if hook.secret:
            import hmac, hashlib
            import json as json_lib
            body = json_lib.dumps(payload)
            sig = hmac.new(hook.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            headers["X-Webhook-Signature"] = sig

        http = await self._get_http()
        for attempt in range(hook.retry):
            try:
                resp = await http.post(hook.url, json=payload, headers=headers, timeout=hook.timeout)
                if resp.status_code < 400:
                    return
            except Exception:
                if attempt == hook.retry - 1:
                    pass  # 静默失败，不阻塞主流程

    # === 便捷触发方法 ===
    async def on_file_created(self, file_id: str, file_name: str, path: str):
        await self.trigger(EventType.FILE_CREATED, {"file_id": file_id, "name": file_name, "path": path})

    async def on_file_deleted(self, file_id: str, file_name: str, path: str):
        await self.trigger(EventType.FILE_DELETED, {"file_id": file_id, "name": file_name, "path": path})

    async def on_sync_completed(self, task_name: str, result: dict):
        await self.trigger(EventType.SYNC_COMPLETED, {"task": task_name, "result": result})
