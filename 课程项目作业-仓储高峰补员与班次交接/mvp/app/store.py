"""JSON 持久化与哈希链审计。

课程 MVP 不引入数据库，运行时数据保存在 data/runtime/state.json。
可用环境变量 FSG_STATE_FILE 覆盖路径，便于测试隔离。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RUNTIME_DIR = DATA_DIR / "runtime"
TZ = timezone(timedelta(hours=8))

_lock = threading.RLock()


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_state() -> dict:
    users = _read_json(DATA_DIR / "users.json")["users"]
    tickets = _read_json(DATA_DIR / "tickets.json")["tickets"]
    roster = _read_json(DATA_DIR / "roster.json")["candidates"]
    return {"version": 1, "users": users, "tickets": tickets, "roster": roster, "audit": []}


def append_audit(
    state: dict,
    actor: str,
    role: str,
    action: str,
    target: str,
    detail: dict | None = None,
    reason: str = "",
) -> dict:
    """追加一条哈希链审计记录，任何历史记录的改动都会破坏后续校验。"""
    previous = state["audit"][-1]["hash"] if state["audit"] else "GENESIS"
    record = {
        "seq": len(state["audit"]) + 1,
        "at": now_iso(),
        "actor": actor,
        "role": role,
        "action": action,
        "target": target,
        "detail": detail or {},
        "reason": reason,
        "previous": previous,
    }
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True)
    record["hash"] = hashlib.sha256((previous + payload).encode("utf-8")).hexdigest()
    state["audit"].append(record)
    return record


def verify_audit(state: dict) -> dict:
    previous = "GENESIS"
    for record in state["audit"]:
        body = {key: value for key, value in record.items() if key != "hash"}
        payload = json.dumps(body, ensure_ascii=False, sort_keys=True)
        expected = hashlib.sha256((previous + payload).encode("utf-8")).hexdigest()
        if expected != record.get("hash"):
            return {"valid": False, "broken_at": record.get("seq"), "records": len(state["audit"])}
        previous = record["hash"]
    return {"valid": True, "broken_at": None, "records": len(state["audit"])}


class Store:
    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.environ.get("FSG_STATE_FILE")
        self.path = Path(configured) if configured else RUNTIME_DIR / "state.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.write(_seed_state())

    def read(self) -> dict:
        with _lock:
            return _read_json(self.path)

    def write(self, state: dict) -> None:
        with _lock:
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.path)

    def mutate(self, func: Callable[[dict], Any]) -> Any:
        with _lock:
            state = self.read()
            result = func(state)
            self.write(state)
            return result

    def reset(self) -> dict:
        state = _seed_state()
        self.write(state)
        return state
