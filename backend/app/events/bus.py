"""轻量级内存事件总线，支撑 SSE / 实时面板。

设计要点：
1. ``publish`` 线程安全（来自 FastAPI sync endpoint / Adapter / 测试），订阅消费在
   asyncio loop 里完成。
2. 每个订阅者一个 ``asyncio.Queue`` + 共享 ``deque`` 历史缓冲，迟到的客户端可以
   ``replay`` 最近 N 条。
3. 不依赖第三方 SSE 库；FastAPI 端点用 ``StreamingResponse`` 即可。
"""
from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from ..alerting import alert_router


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class DomainEvent:
    name: str
    workitem_id: str | None = None
    workflow_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "workitem_id": self.workitem_id,
            "workflow_id": self.workflow_id,
            "payload": self.payload,
            "seq": self.seq,
            "timestamp": self.timestamp,
        }


class EventBus:
    def __init__(self, buffer: int = 200) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[asyncio.Queue[DomainEvent]] = []
        self._history: deque[DomainEvent] = deque(maxlen=buffer)
        self._seq = 0

    # ------------------------------------------------------------------
    # publish
    # ------------------------------------------------------------------
    def publish(
        self,
        name: str,
        *,
        workitem_id: str | None = None,
        workflow_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> DomainEvent:
        with self._lock:
            self._seq += 1
            event = DomainEvent(
                name=name,
                workitem_id=workitem_id,
                workflow_id=workflow_id,
                payload=payload or {},
                seq=self._seq,
            )
            self._history.append(event)
            subscribers = list(self._subscribers)
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 订阅者不消费时优雅丢包，避免拖死 publisher
                pass
        alert_router.route_event(event)
        return event

    # ------------------------------------------------------------------
    # subscribe
    # ------------------------------------------------------------------
    async def subscribe(self, replay: int = 0) -> AsyncIterator[DomainEvent]:
        queue: asyncio.Queue[DomainEvent] = asyncio.Queue(maxsize=256)
        with self._lock:
            replay_events = list(self._history)[-replay:] if replay else []
            self._subscribers.append(queue)
        try:
            for event in replay_events:
                yield event
            while True:
                event = await queue.get()
                yield event
        finally:
            with self._lock:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------
    def history(self, limit: int = 50) -> list[DomainEvent]:
        with self._lock:
            return list(self._history)[-limit:]

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def reset(self) -> None:
        """单测用：清空历史与订阅。"""
        with self._lock:
            self._history.clear()
            self._subscribers.clear()
            self._seq = 0


bus = EventBus()
