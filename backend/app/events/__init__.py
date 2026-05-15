"""事件总线与 SSE 基础设施。"""
from .bus import DomainEvent, EventBus, bus

__all__ = ["DomainEvent", "EventBus", "bus"]
