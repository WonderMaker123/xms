"""
Telegram 机器人服务 - xms
接收用户消息、命令、分享链接，自动触发转存
"""
import asyncio
import re
import httpx
import json
import hashlib
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass


@dataclass
class TGUser:
    user_id: int
    username: str = ""
    first_name: str = ""
    is_admin: bool = False


class TelegramBot:
    """Telegram 机器人 - 支持命令、链接提取、转存触发"""

    def __init__(self, token: str, admin_ids: list = None):
        self.token = token
        self.api_base = f"https://api.telegram.org/bot{token}"
        self.admin_ids = set(admin_ids or [])
        self._offset = 0
        self._running = False
        self._handlers: Dict[str, Callable] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._on_link_callback: Optional[Callable] = None
        self._http: Optional[httpx.AsyncClient] = None
        self._transfer_callback: Optional[Callable] = None
        self._status_callback: Optional[Callable] = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    async def _api(self, method: str, **kwargs) -> dict:
        http = await self._get_http()
        resp = await http.post(f"{self.api_base}/{method}", json=kwargs)
        resp.raise_for_status()
        return resp.json()

    async def _api_get(self, method: str, **kwargs) -> dict:
        http = await self._get_http()
        resp = await http.get(f"{self.api_base}/{method}", params=kwargs)
        resp.raise_for_status()
        return resp.json()

    def set_link_callback(self, cb: Callable):
        """设置链接处理回调 (link_url: str, user: TGUser) -> None"""
        self._on_link_callback = cb

    def set_transfer_callback(self, cb: Callable):
        """设置转存回调 (link_url: str, user: TGUser) -> task_id"""
        self._transfer_callback = cb

    def set_status_callback(self, cb: Callable):
        """设置状态查询回调 () -> dict"""
        self._status_callback = cb

    async def send_message(self, chat_id: int, text: str, parse_mode: str = "Markdown", reply_to: int = None):
        kwargs = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if reply_to:
            kwargs["reply_to_message_id"] = reply_to
        return await self._api("sendMessage", **kwargs)

    async def send_typing(self, chat_id: int):
        await self._api("sendChatAction", chat_id=chat_id, action="typing")

    async def send_document(self, chat_id: int, file_path: str, caption: str = ""):
        """发送文件"""
        with open(file_path, 'rb') as f:
            data = {"chat_id": chat_id, "caption": caption} if caption else {"chat_id": chat_id}
            return await self._api("sendDocument", document=f, **data)

    async def send_keyboard(self, chat_id: int, text: str, buttons: list):
        keyboard = {"inline_keyboard": [[{
            "text": b["text"],
            "url": b["url"]} if "url" in b else {
            "text": b["text"],
            "callback_data": b.get("callback", b["text"])
        } for b in row] for row in buttons]}
        return await self._api("sendMessage", chat_id=chat_id, text=text, parse_mode="Markdown",
                              reply_markup={"inline_keyboard": keyboard})

    def register_handler(self, command: str, handler: Callable):
        self._handlers[command] = handler

    async def process_update(self, update: dict):
        msg = update.get("message") or update.get("callback_query", {}).get("message")
        if not msg:
            return

        chat = msg.get("chat", {})
        from_user = msg.get("from", {})
        user = TGUser(
            user_id=from_user.get("id", 0),
            username=from_user.get("username", ""),
            first_name=from_user.get("first_name", ""),
            is_admin=from_user.get("id") in self.admin_ids,
        )

        text = msg.get("text", "")
        entities = msg.get("entities", []) or []

        # 处理命令
        for entity in entities:
            if entity.get("type") == "bot_command":
                cmd = text[entity["offset"]:entity["offset"] + entity["length"]]
                cmd_name = cmd.lstrip("/").split("@")[0]
                if cmd_name in self._handlers:
                    await self.send_typing(chat["id"])
                    await self._handlers[cmd_name](user, chat["id"], msg.get("message_id", 0), text)
                    return

        # 处理分享链接
        if text:
            links = self._extract_links(text)
            if links:
                await self.send_typing(chat["id"])
                for link in links:
                    await self._handle_link(link, user, chat["id"], msg.get("message_id", 0))
                return

        # 处理普通文本
        if text and not any(e.get("type") == "bot_command" for e in entities):
            if self._handlers.get("_text"):
                await self._handlers["_text"](user, chat["id"], msg.get("message_id", 0), text)

    def _extract_links(self, text: str) -> list:
        patterns = [
            r"https?://app\.guangyapan\.com/pan[^\s<>\"']*",
            r"https?://www\.guangyapan\.com[^\s<>\"']*",
            r"https?://pan\.quark\.cn[^\s<>\"']*",
            r"https?://drive\.quark\.cn[^\s<>\"']*",
            r"https?://115\.com[^\s<>\"']*",
            r"https?://file\.Alist123\.com[^\s<>\"']*",
            r"https?://[^\s<>\"']+\.alist[^\s<>\"']*",
        ]
        links = []
        for pat in patterns:
            links.extend(re.findall(pat, text))
        return list(set(links))

    async def _handle_link(self, link: str, user: TGUser, chat_id: int, reply_to: int):
        """处理收到的分享链接"""
        # 识别链接类型
        link_type = self._detect_link_type(link)
        await self.send_message(chat_id,
            f"🔗 检测到链接：{link_type}\n"
            f"⏳ 开始转存...",
            reply_to=reply_to
        )

        if self._transfer_callback:
            try:
                task_id = await self._transfer_callback(link, user)
                await self.send_message(chat_id,
                    f"✅ 转存任务已创建！\n"
                    f"任务ID：`{task_id}`\n"
                    f"输入 /tasks 查看进度",
                    reply_to=reply_to
                )
            except Exception as e:
                await self.send_message(chat_id, f"❌ 转存失败：{e}", reply_to=reply_to)
        else:
            await self.send_message(chat_id, "⚠️ 转存服务未配置，请在后台设置", reply_to=reply_to)

    def _detect_link_type(self, link: str) -> str:
        if "guangyapan" in link or "guangya" in link:
            return "光鸭云盘"
        elif "quark" in link:
            return "夸克网盘"
        elif "115.com" in link:
            return "115网盘"
        else:
            return "未知网盘"

    async def _poll(self):
        while self._running:
            try:
                params = {"timeout": 30, "offset": self._offset}
                http = await self._get_http()
                resp = await http.get(f"{self.api_base}/getUpdates", params=params, timeout=35)
                data = resp.json()
                if not data.get("ok"):
                    await asyncio.sleep(3)
                    continue
                updates = data.get("result", [])
                for update in updates:
                    self._offset = max(self._offset, update["update_id"] + 1)
                    asyncio.create_task(self.process_update(update))
            except Exception as e:
                await asyncio.sleep(5)

    async def start(self):
        self._running = True
        self._register_default_handlers()
        asyncio.create_task(self._poll())

    async def stop(self):
        self._running = False

    def _register_default_handlers(self):

        async def cmd_start(user: TGUser, chat_id: int, msg_id: int, text: str):
            await self.send_message(chat_id,
                "🦆 *xms 机器人*\n\n"
                "发送分享链接自动转存到光鸭云盘\n"
                "支持的链接：\n"
                "• 光鸭云盘\n"
                "• 夸克网盘\n"
                "• 115网盘\n\n"
                "*命令：*\n"
                "/status - 系统状态\n"
                "/tasks - 转存任务列表\n"
                "/sync - 触发全量同步\n"
                "/preload - 预加载媒体库\n"
                "/help - 帮助",
                reply_to=msg_id
            )

        async def cmd_help(user: TGUser, chat_id: int, msg_id: int, text: str):
            await cmd_start(user, chat_id, msg_id, text)

        async def cmd_status(user: TGUser, chat_id: int, msg_id: int, text: str):
            if self._status_callback:
                try:
                    stat = await self._status_callback()
                    await self.send_message(chat_id,
                        f"📊 *系统状态*\n\n"
                        f"🎬 STRM：{stat.get('strm_count', 0)}\n"
                        f"💾 直链缓存：{stat.get('cache_count', 0)}\n"
                        f"📋 转存任务：{stat.get('task_count', 0)}\n"
                        f"⏱️ 预加载：{stat.get('preload_count', 0)}\n"
                        f"✅ 状态：运行中",
                        reply_to=msg_id
                    )
                except Exception as e:
                    await self.send_message(chat_id, f"状态查询失败：{e}", reply_to=msg_id)
            else:
                await self.send_message(chat_id, "✅ 运行正常", reply_to=msg_id)

        async def cmd_sync(user: TGUser, chat_id: int, msg_id: int, text: str):
            await self.send_message(chat_id, "🔄 开始全量同步 STRM...", reply_to=msg_id)
            asyncio.create_task(self._trigger_sync(chat_id, msg_id))

        async def cmd_tasks(user: TGUser, chat_id: int, msg_id: int, text: str):
            await self.send_message(chat_id, "📋 查看任务请访问后台管理界面", reply_to=msg_id)

        async def cmd_preload(user: TGUser, chat_id: int, msg_id: int, text: str):
            await self.send_message(chat_id, "🚀 开始预加载媒体库直链...", reply_to=msg_id)
            asyncio.create_task(self._trigger_preload(chat_id, msg_id))

        async def cmd_unknown(user: TGUser, chat_id: int, msg_id: int, text: str):
            await self.send_message(chat_id,
                "💡 发送分享链接即可自动转存\n"
                "使用 /help 查看所有命令",
                reply_to=msg_id
            )

        self.register_handler("start", cmd_start)
        self.register_handler("help", cmd_help)
        self.register_handler("status", cmd_status)
        self.register_handler("sync", cmd_sync)
        self.register_handler("tasks", cmd_tasks)
        self.register_handler("preload", cmd_preload)
        self.register_handler("_text", cmd_unknown)

    async def _trigger_sync(self, chat_id: int, reply_to: int):
        try:
            from ..main import get_strm_service
            service = get_strm_service()
            result = service.sync_folder(depth=5)
            await self.send_message(chat_id,
                f"✅ 同步完成！\n"
                f"成功：{result.get('success', 0)}\n"
                f"跳过：{result.get('skipped', 0)}\n"
                f"失败：{result.get('errors', 0)}",
                reply_to=reply_to
            )
        except Exception as e:
            await self.send_message(chat_id, f"❌ 同步失败：{e}", reply_to=reply_to)

    async def _trigger_preload(self, chat_id: int, reply_to: int):
        try:
            from ..main import get_emby_preload
            preload = get_emby_preload()
            if preload:
                preload.rebuild_index(preload.strm_dir)
                await self.send_message(chat_id, "✅ 预加载索引重建完成", reply_to=reply_to)
            else:
                await self.send_message(chat_id, "⚠️ 预加载服务未启用", reply_to=reply_to)
        except Exception as e:
            await self.send_message(chat_id, f"❌ 预加载失败：{e}", reply_to=reply_to)
