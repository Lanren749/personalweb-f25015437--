"""业务规则：缺口分解、人员匹配、交接完整性。

本模块不依赖 FastAPI，便于 Harness 直接做单元测试。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable

ROLE_SKILL = {
    "拣货员": "拣货",
    "上架员": "上架",
    "复核员": "复核",
    "叉车司机": "叉车",
}

ROLE_COURSE = {
    "拣货员": "WMS波次拣货",
    "上架员": "高位货架带岗",
    "复核员": "单据核对",
    "叉车司机": None,
}

HANDOVER_FIELDS = {
    "unfinished": "未完成事项",
    "equipment": "设备与工具",
    "risks": "异常与风险",
    "confirm": "确认人",
}

STATUS_LABELS = {
    "draft": "草稿",
    "submitted": "已提交",
    "approved": "已审批",
    "rejected": "已驳回",
    "handed_over": "已交接",
}


def analyze_ticket(ticket: dict) -> dict:
    """把需求拆成覆盖缺口、培训缺口与技能缺口，避免混为一谈。"""
    lines = []
    total_need = total_ready = total_training = total_skill = total_gap = 0
    for row in ticket["demand"]:
        need = max(0, int(row.get("need", 0)))
        ready = max(0, int(row.get("available_ready", 0)))
        training_pool = max(0, int(row.get("available_training", 0)))
        coverage_gap = max(0, need - ready)
        training_gap = min(training_pool, coverage_gap)
        skill_gap = coverage_gap - training_gap
        flags: list[str] = []
        if coverage_gap == 0:
            conclusion = "可满足"
        elif skill_gap == 0:
            conclusion = "需培训补位"
            flags.append("安排培训或带岗后上岗，名单需标注“需培训”")
        else:
            conclusion = "需外部补充"
            flags.append("技能缺口无法当日解决，需招聘或跨仓调岗")
        if row.get("required_cert") not in (None, "", "无") and ready == 0:
            flags.append("证书条件尚未满足，禁止直接派岗")
        if training_gap and row.get("required_training"):
            flags.append(f"培训要求：{row['required_training']}")
        lines.append(
            {
                "line_id": row.get("line_id"),
                "role": row.get("role"),
                "need": need,
                "available_ready": ready,
                "available_training": training_pool,
                "coverage_gap": coverage_gap,
                "training_gap": training_gap,
                "skill_gap": skill_gap,
                "readiness": round(ready / need, 4) if need else 1.0,
                "conclusion": conclusion,
                "flags": flags,
            }
        )
        total_need += need
        total_ready += ready
        total_training += training_gap
        total_skill += skill_gap
        total_gap += coverage_gap

    if total_gap == 0:
        overall = "需求可满足"
    elif total_skill == 0:
        overall = "存在培训缺口，培训或带岗后可在 1-3 个班次补齐"
    else:
        overall = "存在技能缺口，需外部招聘或跨仓调岗，不能当日解决"

    return {
        "ticket_id": ticket["id"],
        "totals": {
            "need": total_need,
            "ready": total_ready,
            "coverage_gap": total_gap,
            "training_gap": total_training,
            "skill_gap": total_skill,
            "readiness": round(total_ready / total_need, 4) if total_need else 1.0,
        },
        "lines": lines,
        "overall": overall,
        "boundary": "结果仅为计算建议，需人工复核；不得据此自动派岗。",
    }


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _certificate_status(candidate: dict, role: str, today: date) -> tuple[bool, str]:
    if role != "叉车司机":
        return True, "该岗位不要求证书"
    for cert in candidate.get("certificates", []):
        if cert.get("name") == "叉车证":
            expires = _parse_date(cert.get("expires"))
            if not expires:
                return False, "叉车证有效期缺失"
            if expires < today:
                return False, f"叉车证已于 {expires.isoformat()} 过期"
            return True, f"叉车证有效期至 {expires.isoformat()}"
    return False, "未登记叉车证"


def match_candidates(ticket: dict, roster: Iterable[dict], today: date | None = None) -> list[dict]:
    """为工单匹配候选人，明确区分可立即上岗与需培训后上岗。"""
    today = today or date.today()
    roles = [row for row in ticket["demand"] if int(row.get("need", 0)) > 0]
    results: list[dict] = []
    for candidate in roster:
        if not candidate.get("available", True):
            continue
        options = []
        for row in roles:
            role = row["role"]
            skill = ROLE_SKILL.get(role)
            if skill and skill in candidate.get("skills", []):
                options.append((int(row.get("need", 0)), row, skill))
        if not options:
            results.append(
                {
                    "candidate_id": candidate["id"],
                    "name": candidate["name"],
                    "role": "无匹配岗位",
                    "score": 0.0,
                    "status": "不匹配",
                    "training_status": "不适用",
                    "cert_status": "不适用",
                    "reasons": ["技能池中没有该岗位所需技能，需培训或另选渠道"],
                }
            )
            continue
        _, row, skill = max(options, key=lambda item: item[0])
        role = row["role"]
        course = ROLE_COURSE.get(role)
        training_status = candidate.get("training", {}).get(course, "未参加") if course else "不适用"
        cert_ok, cert_note = _certificate_status(candidate, role, today)

        score = 0.45
        reasons = [f"具备 {skill} 技能"]
        if cert_ok:
            score += 0.25
        reasons.append(cert_note)
        if training_status == "已完成":
            score += 0.3
            reasons.append(f"已完成《{course}》培训")
        elif "进行中" in str(training_status):
            score += 0.1
            reasons.append(f"培训进行中（{training_status}），尚不能独立上岗")
        else:
            reasons.append(f"未完成《{course}》培训")

        last_worked = _parse_date(candidate.get("last_worked"))
        if last_worked and (today - last_worked).days > 365:
            score -= 0.1
            reasons.append(f"距上次上岗已超过 12 个月（{last_worked.isoformat()}），需复核技能状态")

        if not cert_ok:
            status = "不可上岗（证书问题）"
        elif training_status == "已完成":
            status = "可立即上岗"
        else:
            status = "需培训后上岗"

        results.append(
            {
                "candidate_id": candidate["id"],
                "name": candidate["name"],
                "role": role,
                "score": round(max(0.0, min(score, 1.0)), 3),
                "status": status,
                "training_status": training_status,
                "cert_status": cert_note,
                "reasons": reasons,
            }
        )
    order = {"可立即上岗": 0, "需培训后上岗": 1, "不可上岗（证书问题）": 2, "不匹配": 3}
    results.sort(key=lambda item: (order[item["status"]], -item["score"]))
    return results


def evaluate_handover(handover: dict) -> dict:
    """判定交接单是否可以完成责任转移。"""
    items = handover.get("items", {})
    missing: list[str] = []
    issues: list[str] = []
    for key, label in HANDOVER_FIELDS.items():
        if not str(items.get(key, "")).strip():
            missing.append(label)
    for key in ("outgoing_lead", "incoming_lead"):
        if not str(handover.get(key, "")).strip():
            missing.append("交班组长" if key == "outgoing_lead" else "接班组长")

    unfinished = str(items.get("unfinished", "")).strip()
    if unfinished and not re.search(r"\d{3,}|[A-Za-z]\d|库位|单据", unfinished):
        issues.append("未完成事项需包含库位或单据号，避免“已交接”“没问题”等模糊表述")

    confirm = str(items.get("confirm", "")).strip()
    if confirm and len(confirm) < 2:
        issues.append("确认人必须是可识别的具名人员")

    if handover.get("outgoing_lead") and handover.get("incoming_lead"):
        if handover["outgoing_lead"].strip() == handover["incoming_lead"].strip():
            issues.append("交班人与接班人不能为同一人")

    return {
        "ready": not missing and not issues,
        "missing": missing,
        "issues": issues,
        "message": "交接单完整，可以确认责任转移"
        if not missing and not issues
        else "交接单不完整，禁止完成交接",
    }


def audit_ticket_view(ticket: dict) -> dict:
    return {
        "id": ticket["id"],
        "tenant": ticket.get("tenant"),
        "site": ticket.get("site"),
        "peak": ticket.get("peak"),
        "shift": ticket.get("shift"),
        "status": ticket.get("status"),
        "status_label": STATUS_LABELS.get(ticket.get("status", ""), ticket.get("status")),
        "created_by": ticket.get("created_by"),
        "created_at": ticket.get("created_at"),
        "updated_at": ticket.get("updated_at"),
    }
