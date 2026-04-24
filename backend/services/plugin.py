"""
插件系统 - 正则替换/文件名修复/通知
"""
import re
from typing import List, Dict, Callable, Any
from dataclasses import dataclass, field
from enum import Enum


class PluginType(str, Enum):
    RENAME = "rename"       # 文件名重命名
    CONTENT_REPLACE = "content_replace"  # STRM 内容正则替换
    NOTIFY = "notify"       # 通知插件


@dataclass
class Plugin:
    id: str
    name: str
    type: PluginType
    enabled: bool = True
    config: Dict[str, Any] = field(default_factory=dict)
    order: int = 0


class PluginManager:
    """插件管理器"""

    def __init__(self):
        self._plugins: List[Plugin] = []

    def register(self, plugin: Plugin):
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: p.order)

    def unregister(self, plugin_id: str):
        self._plugins = [p for p in self._plugins if p.id != plugin_id]

    def get_plugins(self, plugin_type: PluginType = None) -> List[Plugin]:
        if plugin_type is None:
            return [p for p in self._plugins if p.enabled]
        return [p for p in self._plugins if p.enabled and p.type == plugin_type]

    # === 插件执行 ===

    def apply_rename(self, filename: str) -> str:
        """应用所有重命名插件"""
        for plugin in self.get_plugins(PluginType.RENAME):
            filename = self._run_rename(plugin, filename)
        return filename

    def _run_rename(self, plugin: Plugin, filename: str) -> str:
        pattern = plugin.config.get("pattern", "")
        replacement = plugin.config.get("replacement", "")
        if not pattern:
            return filename
        try:
            return re.sub(pattern, replacement, filename)
        except Exception:
            return filename

    def apply_content_replace(self, content: str) -> str:
        """应用 STRM 内容替换"""
        for plugin in self.get_plugins(PluginType.CONTENT_REPLACE):
            pattern = plugin.config.get("pattern", "")
            replacement = plugin.config.get("replacement", "")
            if not pattern:
                continue
            try:
                content = re.sub(pattern, replacement, content)
            except Exception:
                pass
        return content

    def notify(self, message: str, level: str = "info"):
        """发送通知"""
        for plugin in self.get_plugins(PluginType.NOTIFY):
            self._run_notify(plugin, message, level)

    def _run_notify(self, plugin: Plugin, message: str, level: str):
        # 目前支持 Bark / ServerChan / Telegram
        service = plugin.config.get("service", "")
        if service == "serverchan":
            self._notify_serverchan(plugin, message)
        elif service == "bark":
            self._notify_bark(plugin, message)

    def _notify_serverchan(self, plugin: Plugin, message: str):
        import httpx, asyncio
        sckey = plugin.config.get("sckey", "")
        if not sckey:
            return
        try:
            asyncio.create_task(
                httpx.AsyncClient().post(
                    f"https://sc.ftqq.com/{sckey}.send",
                    json={"text": "xms 通知", "desp": message},
                    timeout=10.0,
                )
            )
        except Exception:
            pass

    def _notify_bark(self, plugin: Plugin, message: str):
        import httpx, asyncio
        bark_url = plugin.config.get("bark_url", "")
        if not bark_url:
            return
        try:
            asyncio.create_task(
                httpx.AsyncClient().get(f"{bark_url}/{message}", timeout=10.0)
            )
        except Exception:
            pass


# === 内置插件工厂 ===

def make_rename_plugin(
    plugin_id: str,
    name: str,
    pattern: str,
    replacement: str,
    order: int = 0,
) -> Plugin:
    return Plugin(
        id=plugin_id,
        name=name,
        type=PluginType.RENAME,
        config={"pattern": pattern, "replacement": replacement},
        order=order,
    )


def make_content_replace_plugin(
    plugin_id: str,
    name: str,
    pattern: str,
    replacement: str,
) -> Plugin:
    return Plugin(
        id=plugin_id,
        name=name,
        type=PluginType.CONTENT_REPLACE,
        config={"pattern": pattern, "replacement": replacement},
    )


def make_notify_plugin(
    plugin_id: str,
    name: str,
    service: str,
    **kwargs,
) -> Plugin:
    return Plugin(
        id=plugin_id,
        name=name,
        type=PluginType.NOTIFY,
        config={"service": service, **kwargs},
    )
