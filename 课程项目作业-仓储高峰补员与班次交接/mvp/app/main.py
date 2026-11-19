"""FastAPI 应用：补员工单 + 知识库 RAG + 知识图谱 / Wiki + 分层权限 + 审计。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import auth
from app.domain import (
    STATUS_LABELS,
    analyze_ticket,
    audit_ticket_view,
    evaluate_handover,
    match_candidates,
)
from app.graph import KnowledgeGraph
from app.harness import run_harness
from app.kb import KnowledgeBase
from app.store import BASE_DIR, Store, append_audit, now_iso, verify_audit

APP_VERSION = "1.0.0"
store = Store()
kb = KnowledgeBase(BASE_DIR / "data" / "kb")
graph = KnowledgeGraph(BASE_DIR / "data" / "graph.json", kb)


# --------------------------------------------------------------------------
# 依赖：认证与权限
# --------------------------------------------------------------------------
def current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少访问令牌，请先登录")
    user = auth.resolve_session(store, authorization.removeprefix("Bearer ").strip())
    if not user:
        raise HTTPException(status_code=401, detail="令牌无效或已过期，请重新登录")
    return user


def require(permission: str):
    def dependency(user: dict = Depends(current_user)) -> dict:
        if not auth.has_perm(user, permission):
            raise HTTPException(status_code=403, detail=f"缺少权限：{permission}")
        return user

    return dependency


def load_ticket(ticket_id: str) -> dict:
    for ticket in store.read()["tickets"]:
        if ticket["id"] == ticket_id:
            return ticket
    raise HTTPException(status_code=404, detail=f"工单 {ticket_id} 不存在")


def can_access(user: dict, ticket: dict) -> bool:
    if user["role"] == "saas_admin":
        return True
    if ticket.get("tenant") != user["tenant"]:
        return False
    return auth.has_perm(user, "ticket:read_all") or ticket.get("created_by") == user["username"]


def get_visible_ticket(user: dict, ticket_id: str) -> dict:
    ticket = load_ticket(ticket_id)
    if not can_access(user, ticket):
        raise HTTPException(status_code=403, detail="无权访问该工单（租户或范围不匹配）")
    return ticket


# --------------------------------------------------------------------------
# 请求体
# --------------------------------------------------------------------------
class LoginPayload(BaseModel):
    username: str
    password: str


class DemandLine(BaseModel):
    line_id: str | None = None
    role: str
    need: int = Field(ge=0, le=500)
    available_ready: int = Field(default=0, ge=0, le=500)
    available_training: int = Field(default=0, ge=0, le=500)
    available_no_skill: int = Field(default=0, ge=0, le=500)
    required_cert: str = "无"
    required_training: str = ""


class TicketCreate(BaseModel):
    site: str
    peak: str
    shift: str
    tenant: str | None = None
    demand: list[DemandLine]
    outgoing_lead: str = ""
    incoming_lead: str = ""


class ReviewPayload(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str = ""


class HandoverPayload(BaseModel):
    shift_date: str = ""
    outgoing_lead: str = ""
    incoming_lead: str = ""
    items: dict[str, str]


class AskPayload(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    top_k: int = Field(default=4, ge=1, le=6)


def next_ticket_id(state: dict) -> str:
    prefix = datetime.now().strftime("TK-%Y%m%d-")
    existing = [ticket["id"] for ticket in state["tickets"] if ticket["id"].startswith(prefix)]
    return f"{prefix}{len(existing) + 1:03d}"


def ticket_payload(ticket: dict, user: dict) -> dict:
    analysis = analyze_ticket(ticket)
    handover_check = evaluate_handover(ticket.get("handover", {}))
    is_creator = ticket.get("created_by") == user["username"]
    actions = []
    if ticket["status"] in {"draft", "rejected"} and auth.has_perm(user, "ticket:submit") and (
        is_creator or user["role"] in {"saas_admin", "tenant_admin"}
    ):
        actions.append("submit")
    if (
        ticket["status"] == "submitted"
        and auth.has_perm(user, "ticket:review")
        and not is_creator
    ):
        actions.append("review")
    if ticket["status"] == "approved" and auth.has_perm(user, "ticket:handover") and handover_check["ready"]:
        actions.append("handover")
    return {
        **ticket,
        "status_label": STATUS_LABELS.get(ticket["status"], ticket["status"]),
        "analysis": analysis,
        "handover_check": handover_check,
        "allowed_actions": actions,
    }


app = FastAPI(
    title="仓储高峰补员与班次交接 · 业务核对助手",
    version=APP_VERSION,
    description="课程 MVP：FastAPI + Harness + 知识库 RAG + 知识图谱 + 分层权限",
)

STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "kb_chunks": len(kb.chunks),
        "graph_nodes": len(graph.nodes),
        "kb_dir": str(kb.kb_dir),
    }


# --------------------------------------------------------------------------
# 认证
# --------------------------------------------------------------------------
@app.post("/api/auth/login")
def login(payload: LoginPayload):
    user = auth.authenticate(store, payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = auth.create_session(user)
    store.mutate(
        lambda state: append_audit(
            state, user["username"], user["role"], "auth.login", user["username"], {"result": "success"}
        )
    )
    return {"token": token, "user": auth.public_user(user)}


@app.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None), user: dict = Depends(current_user)):
    if authorization:
        auth.drop_session(authorization.removeprefix("Bearer ").strip())
    store.mutate(
        lambda state: append_audit(state, user["username"], user["role"], "auth.logout", user["username"])
    )
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return {
        "user": auth.public_user(user),
        "permission_labels": auth.PERMISSION_LABELS,
        "role_labels": auth.ROLE_LABELS,
    }


@app.get("/api/users")
def list_users(user: dict = Depends(require("user:manage"))):
    state = store.read()
    return {
        "users": [auth.public_user(item) for item in state["users"]],
        "tenants": sorted({item["tenant"] for item in state["users"]}),
    }


# --------------------------------------------------------------------------
# 工单
# --------------------------------------------------------------------------
@app.get("/api/tickets")
def list_tickets(user: dict = Depends(require("ticket:read"))):
    state = store.read()
    visible = [ticket for ticket in state["tickets"] if can_access(user, ticket)]
    items = []
    for ticket in visible:
        analysis = analyze_ticket(ticket)
        items.append(
            {
                **audit_ticket_view(ticket),
                "readiness": analysis["totals"]["readiness"],
                "coverage_gap": analysis["totals"]["coverage_gap"],
                "overall": analysis["overall"],
            }
        )
    return {"tickets": items, "count": len(items)}


@app.post("/api/tickets", status_code=201)
def create_ticket(payload: TicketCreate, user: dict = Depends(require("ticket:create"))):
    if not payload.demand:
        raise HTTPException(status_code=422, detail="需求单至少需要一行岗位需求")
    tenant = payload.tenant if user["role"] == "saas_admin" and payload.tenant else user["tenant"]
    created = store.mutate(lambda state: _create_ticket(state, payload, user, tenant))
    return ticket_payload(created, user)


def _create_ticket(state: dict, payload: TicketCreate, user: dict, tenant: str) -> dict:
    ticket_id = next_ticket_id(state)
    demand = []
    for index, line in enumerate(payload.demand, start=1):
        demand.append({**line.model_dump(), "line_id": line.line_id or f"L{index}"})
    ticket = {
        "id": ticket_id,
        "tenant": tenant,
        "site": payload.site,
        "peak": payload.peak,
        "shift": payload.shift,
        "status": "draft",
        "created_by": user["username"],
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "demand": demand,
        "handover": {
            "shift_date": "",
            "outgoing_lead": payload.outgoing_lead,
            "incoming_lead": payload.incoming_lead,
            "items": {"unfinished": "", "equipment": "", "risks": "", "confirm": ""},
        },
        "review": {"decision": None, "by": None, "comment": None, "at": None},
        "handover_confirmed": {"by": None, "at": None},
    }
    state["tickets"].append(ticket)
    append_audit(
        state,
        user["username"],
        user["role"],
        "ticket.create",
        ticket_id,
        {"site": ticket["site"], "shift": ticket["shift"], "lines": len(demand)},
    )
    return ticket


@app.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: str, user: dict = Depends(require("ticket:read"))):
    return ticket_payload(get_visible_ticket(user, ticket_id), user)


@app.post("/api/tickets/{ticket_id}/analyze")
def analyze(ticket_id: str, user: dict = Depends(require("ticket:read"))):
    ticket = get_visible_ticket(user, ticket_id)
    return {
        "analysis": analyze_ticket(ticket),
        "handover_check": evaluate_handover(ticket.get("handover", {})),
    }


@app.get("/api/tickets/{ticket_id}/candidates")
def candidates(ticket_id: str, user: dict = Depends(require("ticket:read"))):
    ticket = get_visible_ticket(user, ticket_id)
    roster = store.read()["roster"]
    return {"candidates": match_candidates(ticket, roster)}


@app.post("/api/tickets/{ticket_id}/submit")
def submit_ticket(ticket_id: str, user: dict = Depends(require("ticket:submit"))):
    ticket = get_visible_ticket(user, ticket_id)
    if ticket["status"] not in {"draft", "rejected"}:
        raise HTTPException(status_code=409, detail=f"当前状态为 {ticket['status']}，不能提交")
    if ticket["created_by"] != user["username"] and user["role"] not in {"saas_admin", "tenant_admin"}:
        raise HTTPException(status_code=403, detail="只有创建人可以提交该工单")
    missing = []
    for field, label in (("site", "仓库"), ("peak", "高峰批次"), ("shift", "班次")):
        if not str(ticket.get(field, "")).strip():
            missing.append(label)
    for line in ticket["demand"]:
        if not line.get("role"):
            missing.append("岗位名称")
        if line.get("need", 0) <= 0:
            missing.append(f"{line.get('role', '未知岗位')} 的需求人数")
        if not str(line.get("required_cert", "")).strip():
            missing.append(f"{line.get('role', '未知岗位')} 的证书条件（没有请写“无”）")
        if not str(line.get("required_training", "")).strip():
            missing.append(f"{line.get('role', '未知岗位')} 的培训条件（没有请写“无”）")
    if missing:
        raise HTTPException(status_code=422, detail="需求单不完整：" + "、".join(dict.fromkeys(missing)))

    def mutate(state: dict) -> dict:
        for item in state["tickets"]:
            if item["id"] == ticket_id:
                before = item["status"]
                item["status"] = "submitted"
                item["updated_at"] = now_iso()
                append_audit(
                    state,
                    user["username"],
                    user["role"],
                    "ticket.submit",
                    ticket_id,
                    {"from": before, "to": "submitted"},
                )
                return item
        raise HTTPException(status_code=404, detail="工单不存在")

    updated = store.mutate(mutate)
    return ticket_payload(updated, user)


@app.post("/api/tickets/{ticket_id}/review")
def review_ticket(ticket_id: str, payload: ReviewPayload, user: dict = Depends(require("ticket:review"))):
    ticket = get_visible_ticket(user, ticket_id)
    if ticket["status"] != "submitted":
        raise HTTPException(status_code=409, detail="只有已提交工单可以审批")
    if ticket["created_by"] == user["username"]:
        raise HTTPException(status_code=409, detail="职责分离：提交人不能审批自己的工单")
    if payload.decision == "rejected" and not payload.comment.strip():
        raise HTTPException(status_code=422, detail="驳回必须填写原因")

    def mutate(state: dict) -> dict:
        for item in state["tickets"]:
            if item["id"] == ticket_id:
                item["status"] = payload.decision
                item["review"] = {
                    "decision": payload.decision,
                    "by": user["username"],
                    "comment": payload.comment,
                    "at": now_iso(),
                }
                item["updated_at"] = now_iso()
                append_audit(
                    state,
                    user["username"],
                    user["role"],
                    "ticket.review",
                    ticket_id,
                    {"decision": payload.decision, "comment": payload.comment},
                )
                return item
        raise HTTPException(status_code=404, detail="工单不存在")

    updated = store.mutate(mutate)
    return ticket_payload(updated, user)


@app.post("/api/tickets/{ticket_id}/handover")
def handover_ticket(ticket_id: str, payload: HandoverPayload, user: dict = Depends(require("ticket:handover"))):
    ticket = get_visible_ticket(user, ticket_id)
    if ticket["status"] != "approved":
        raise HTTPException(status_code=409, detail="只有已审批工单可以发起交接")
    check = evaluate_handover(payload.model_dump())
    if not check["ready"]:
        raise HTTPException(status_code=422, detail=check["message"] + "；缺少：" + "、".join(check["missing"] + check["issues"]))

    def mutate(state: dict) -> dict:
        for item in state["tickets"]:
            if item["id"] == ticket_id:
                item["handover"] = payload.model_dump()
                item["status"] = "handed_over"
                item["handover_confirmed"] = {"by": user["username"], "at": now_iso()}
                item["updated_at"] = now_iso()
                append_audit(
                    state,
                    user["username"],
                    user["role"],
                    "ticket.handover",
                    ticket_id,
                    {"incoming_lead": payload.incoming_lead, "confirm": payload.items.get("confirm")},
                )
                return item
        raise HTTPException(status_code=404, detail="工单不存在")

    updated = store.mutate(mutate)
    return ticket_payload(updated, user)


# --------------------------------------------------------------------------
# 知识库
# --------------------------------------------------------------------------
@app.get("/api/kb/docs")
def kb_docs(user: dict = Depends(require("kb:read"))):
    return {"documents": kb.documents(), "chunks": len(kb.chunks)}


@app.get("/api/kb/search")
def kb_search(q: str = Query(min_length=1, max_length=200), top_k: int = Query(4, ge=1, le=6), user: dict = Depends(require("kb:read"))):
    return {"query": q, "results": kb.search(q, top_k=top_k)}


@app.post("/api/kb/ask")
def kb_ask(payload: AskPayload, user: dict = Depends(require("kb:read"))):
    result = kb.ask(payload.query, top_k=payload.top_k)
    store.mutate(
        lambda state: append_audit(
            state,
            user["username"],
            user["role"],
            "kb.ask",
            "knowledge-base",
            {"query": payload.query, "answered": result["answered"], "citations": [item["id"] for item in result["citations"]]},
        )
    )
    return result


# --------------------------------------------------------------------------
# 知识图谱 / Wiki
# --------------------------------------------------------------------------
@app.get("/api/graph")
def graph_data(user: dict = Depends(require("graph:read"))):
    return {"overview": graph.overview(), **graph.graph()}


@app.get("/api/wiki")
def wiki_list(q: str = "", user: dict = Depends(require("graph:read"))):
    return {"items": graph.list_wiki(q)}


@app.get("/api/wiki/{node_id}")
def wiki_page(node_id: str, user: dict = Depends(require("graph:read"))):
    page = graph.wiki(node_id)
    if not page:
        raise HTTPException(status_code=404, detail=f"词条 {node_id} 不存在")
    return page


# --------------------------------------------------------------------------
# 审计与 Harness
# --------------------------------------------------------------------------
@app.get("/api/audit")
def audit_log(limit: int = Query(50, ge=1, le=500), user: dict = Depends(require("audit:read"))):
    state = store.read()
    records = list(reversed(state["audit"]))[:limit]
    return {
        "records": records,
        "verification": verify_audit(state),
        "total": len(state["audit"]),
    }


@app.get("/api/audit/verify")
def audit_verify(user: dict = Depends(require("audit:read"))):
    return verify_audit(store.read())


@app.get("/api/harness/run")
def harness_run(user: dict = Depends(current_user)):
    result = run_harness(kb, graph, store)
    store.mutate(
        lambda state: append_audit(
            state,
            user["username"],
            user["role"],
            "harness.run",
            "harness",
            {"passed": result["passed"], "score": result["score"], "summary": result["summary"]},
        )
    )
    return result


@app.post("/api/admin/reset")
def reset_demo(user: dict = Depends(require("user:manage"))):
    store.reset()
    return {"ok": True, "message": "演示数据已重置"}
