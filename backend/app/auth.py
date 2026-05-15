"""AUTH-01 身份鉴权 + 三角色 RBAC。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from fastapi import Depends, Header, HTTPException, Query


@dataclass(frozen=True)
class User:
    id: str
    name: str
    role: str  # operator / owner / admin

    def has_role(self, *roles: str) -> bool:
        return self.role in roles


# 静态 token 表（实际 demo 中用 .env 注入 / Redis；当前内存即可）
TOKEN_TABLE: dict[str, User] = {
    "demo-operator": User(id="u_op_demo", name="Operator", role="operator"),
    "demo-owner": User(id="u_yang", name="阳哥", role="owner"),
    "demo-admin": User(id="u_admin", name="Root", role="admin"),
}

# 角色 → 允许的 trigger
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "operator": {"read", "start", "pause", "resume", "submit", "run_agent"},
    "owner": {
        "read",
        "assign", "start", "pause", "resume", "takeover",
        "request_decision", "decide", "submit", "approve", "reject",
        "escalate",
        "run_agent",
        "workflow.start", "workflow.pause", "workflow.resume",
        "workflow.complete", "workflow.cancel",
    },
    "admin": {  # admin 全开
        "read",
        "assign", "start", "pause", "resume", "takeover",
        "request_decision", "decide", "submit", "approve", "reject",
        "escalate", "cancel", "run_agent",
        "template.create", "template.update", "template.delete",
        "workflow.cancel", "workflow.start", "workflow.pause", "workflow.resume",
        "workflow.complete",
    },
}


def get_current_user(
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(default=None),
) -> User:
    """从 Bearer header 或 SSE query token 解析当前用户。"""
    token: str | None = None
    if authorization:
        if not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Authorization must be Bearer token")
        token = authorization.split(" ", 1)[1].strip()
    elif access_token:
        token = access_token.strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing access token")
    user = TOKEN_TABLE.get(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


def require_permission(*triggers: str):
    """装饰器返回一个 Depends，校验当前用户拥有指定 trigger 中至少一个。"""

    def _check(user: User = Depends(get_current_user)) -> User:
        allowed = ROLE_PERMISSIONS.get(user.role, set())
        if not any(t in allowed for t in triggers):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "forbidden",
                    "message": f"role '{user.role}' lacks permission for {list(triggers)}",
                    "required_any": list(triggers),
                },
            )
        return user

    return _check


def can(user: User, *triggers: Iterable[str]) -> bool:
    """便捷判断：用户是否拥有任意一个 trigger 权限。"""
    allowed = ROLE_PERMISSIONS.get(user.role, set())
    return any(t in allowed for t in triggers)
