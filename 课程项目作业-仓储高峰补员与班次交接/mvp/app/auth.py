"""认证与分层权限（RBAC）。

设计要点：
- 平台管理员（saas_admin）跨租户；
- 业务管理员、排期协调员、质量复核、一线经办人、只读用户逐级收敛；
- 权限判断集中在 has_perm，接口层只声明需要的权限。
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict

TOKEN_TTL_HOURS = 8
_TZ = timezone(timedelta(hours=8))
_sessions: Dict[str, dict] = {}
_session_lock = threading.Lock()

ROLE_LABELS = {
    "saas_admin": "管理员（SaaS）",
    "tenant_admin": "业务管理员",
    "dispatcher": "排期协调员",
    "reviewer": "质量复核",
    "operator": "一线经办人",
    "viewer": "只读用户",
}

ROLE_PERMISSIONS = {
    "saas_admin": {"*"},
    "tenant_admin": {
        "ticket:read",
        "ticket:read_all",
        "ticket:create",
        "ticket:submit",
        "ticket:review",
        "ticket:handover",
        "kb:read",
        "kb:write",
        "graph:read",
        "graph:write",
        "audit:read",
    },
    "dispatcher": {
        "ticket:read",
        "ticket:read_all",
        "ticket:create",
        "ticket:submit",
        "ticket:handover",
        "kb:read",
        "graph:read",
    },
    "reviewer": {
        "ticket:read",
        "ticket:read_all",
        "ticket:review",
        "kb:read",
        "graph:read",
        "audit:read",
    },
    "operator": {
        "ticket:read",
        "ticket:create",
        "ticket:submit",
        "ticket:handover",
        "kb:read",
        "graph:read",
    },
    "viewer": {"ticket:read", "ticket:read_all", "kb:read", "graph:read"},
}

PERMISSION_LABELS = {
    "ticket:read": "查看工单（本人/授权范围）",
    "ticket:read_all": "查看本租户全部工单",
    "ticket:create": "创建补员工单",
    "ticket:submit": "提交工单",
    "ticket:review": "审批或驳回工单",
    "ticket:handover": "发起/确认交接",
    "kb:read": "检索知识库",
    "kb:write": "维护知识库",
    "graph:read": "查看知识图谱",
    "graph:write": "维护知识图谱",
    "audit:read": "查看审计日志",
    "user:manage": "管理用户与租户",
}


def hash_password(password: str) -> str:
    return hashlib.sha256(("fsg::" + password).encode("utf-8")).hexdigest()


def verify_password(password: str, digest: str) -> bool:
    return secrets.compare_digest(hash_password(password), digest)


def permissions_for(role: str) -> set[str]:
    return set(ROLE_PERMISSIONS.get(role, set()))


def has_perm(user: dict, permission: str) -> bool:
    perms = permissions_for(user["role"])
    return "*" in perms or permission in perms


def public_user(user: dict) -> dict:
    return {
        "username": user["username"],
        "name": user["name"],
        "role": user["role"],
        "role_label": ROLE_LABELS.get(user["role"], user["role"]),
        "tenant": user["tenant"],
        "email": user.get("email", ""),
        "permissions": sorted(permissions_for(user["role"])),
    }


def authenticate(store, username: str, password: str) -> dict | None:
    state = store.read()
    for user in state["users"]:
        if user["username"] == username and user.get("active", True):
            if verify_password(password, user["password_sha256"]):
                return user
    return None


def create_session(user: dict) -> str:
    token = secrets.token_urlsafe(24)
    expires = datetime.now(_TZ) + timedelta(hours=TOKEN_TTL_HOURS)
    with _session_lock:
        _sessions[token] = {"username": user["username"], "expires": expires}
    return token


def resolve_session(store, token: str) -> dict | None:
    with _session_lock:
        session = _sessions.get(token)
        if not session:
            return None
        if session["expires"] < datetime.now(_TZ):
            _sessions.pop(token, None)
            return None
        username = session["username"]
    state = store.read()
    for user in state["users"]:
        if user["username"] == username and user.get("active", True):
            return user
    return None


def drop_session(token: str) -> None:
    with _session_lock:
        _sessions.pop(token, None)


def clear_sessions() -> None:
    """仅用于测试隔离。"""
    with _session_lock:
        _sessions.clear()
