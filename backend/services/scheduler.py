"""
定时任务调度器 - Crontab 风格
支持增量同步、删除同步、定时刷新直链
"""
import asyncio
import time
import re
from typing import Optional, Callable, List
from dataclasses import dataclass, field
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger


@dataclass
class SyncTask:
    id: str
    name: str
    parent_id: Optional[str]
    folder_path: str
    cron: str  # "0 3 * * *" 或 "interval:6h"
    depth: int = 5
    enabled: bool = True
    last_run: Optional[float] = None
    last_result: dict = field(default_factory=dict)
    delete_sync: bool = True  # 同步删除
    refresh_links: bool = True  # 定期刷新直链


class SchedulerService:
    """定时任务服务"""

    def __init__(self):
        self._scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        self._tasks: List[SyncTask] = []
        self._strm_service = None

    def set_strm_service(self, service):
        self._strm_service = service

    def add_task(self, task: SyncTask) -> str:
        """添加同步任务"""
        self._tasks.append(task)
        self._schedule_task(task)
        return task.id

    def remove_task(self, task_id: str):
        """移除任务"""
        self._scheduler.remove_job(task_id)
        self._tasks = [t for t in self._tasks if t.id != task_id]

    def get_tasks(self) -> List[SyncTask]:
        return self._tasks

    def _parse_cron(self, cron_expr: str) -> dict:
        """解析 cron 表达式或 interval"""
        if cron_expr.startswith("interval:"):
            # interval:6h
            m = re.match(r'interval:(\d+)h', cron_expr)
            if m:
                hours = int(m.group(1))
                return {"type": "interval", "hours": hours}
        
        # 标准 cron: "0 3 * * *"
        parts = cron_expr.split()
        if len(parts) == 5:
            return {"type": "cron", "minute": parts[0], "hour": parts[1], "day": parts[2], "month": parts[3], "day_of_week": parts[4]}
        
        # 默认每天凌晨3点
        return {"type": "cron", "minute": "0", "hour": "3", "day": "*", "month": "*", "day_of_week": "*"}

    def _schedule_task(self, task: SyncTask):
        """调度单个任务"""
        if not task.enabled:
            return

        parsed = self._parse_cron(task.cron)

        async def _run():
            if not self._strm_service:
                return
            task.last_run = time.time()
            try:
                result = self._strm_service.sync_folder(
                    parent_id=task.parent_id,
                    folder_path=task.folder_path,
                    depth=task.depth,
                )
                task.last_result = result
            except Exception as e:
                task.last_result = {"error": str(e)}

        if parsed["type"] == "interval":
            trigger = IntervalTrigger(hours=parsed["hours"])
        else:
            trigger = CronTrigger(
                minute=parsed.get("minute", "0"),
                hour=parsed.get("hour", "3"),
                day=parsed.get("day", "*"),
                month=parsed.get("month", "*"),
                day_of_week=parsed.get("day_of_week", "*"),
                timezone="Asia/Shanghai",
            )

        self._scheduler.add_job(_run, trigger, id=task.id, name=task.name, replace_existing=True)

    def start(self):
        self._scheduler.start()

    def shutdown(self):
        self._scheduler.shutdown()
