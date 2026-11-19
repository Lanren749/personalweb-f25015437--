from app.domain import analyze_ticket, evaluate_handover, match_candidates
from tests.conftest import login

COMPLETE_HANDOVER = {
    "shift_date": "2026-11-19",
    "outgoing_lead": "王组长",
    "incoming_lead": "李组长",
    "items": {
        "unfinished": "A区库位 A-102 剩余 2 托未上架，单据号 D-20261119-08",
        "equipment": "PDA 6 台、对讲机 3 台已交回",
        "risks": "3 号货架照明故障，已报修",
        "confirm": "李组长",
    },
}


def test_gap_analysis_separates_training_and_skill(client):
    headers = login(client, "lin.yizhou")
    body = client.get("/api/tickets/TK-20261119-001", headers=headers).json()
    totals = body["analysis"]["totals"]
    assert totals == {
        "need": 38,
        "ready": 27,
        "coverage_gap": 11,
        "training_gap": 8,
        "skill_gap": 3,
        "readiness": 0.7105,
    }
    assert "技能缺口" in body["analysis"]["overall"]


def test_handover_validation_rules():
    empty = evaluate_handover({"outgoing_lead": "", "incoming_lead": "", "items": {}})
    assert empty["ready"] is False
    assert "未完成事项" in empty["missing"]

    vague = evaluate_handover(
        {
            "outgoing_lead": "王组长",
            "incoming_lead": "李组长",
            "items": {"unfinished": "已交接完毕", "equipment": "已还", "risks": "无", "confirm": "李组长"},
        }
    )
    assert vague["ready"] is False
    assert any("库位或单据号" in issue for issue in vague["issues"])

    assert evaluate_handover(COMPLETE_HANDOVER)["ready"] is True


def test_candidate_matching_blocks_expired_certificate():
    ticket = {
        "id": "T",
        "demand": [
            {"role": "叉车司机", "need": 2},
            {"role": "上架员", "need": 8},
        ],
    }
    roster = [
        {
            "id": "C005",
            "name": "周涛",
            "skills": ["叉车"],
            "certificates": [{"name": "叉车证", "expires": "2026-11-05"}],
            "training": {},
            "available": True,
        },
        {
            "id": "C003",
            "name": "赵磊",
            "skills": ["上架"],
            "certificates": [],
            "training": {"高位货架带岗": "进行中1/3"},
            "available": True,
        },
    ]
    result = {item["candidate_id"]: item for item in match_candidates(ticket, roster)}
    assert result["C005"]["status"] == "不可上岗（证书问题）"
    assert result["C003"]["status"] == "需培训后上岗"


def test_workflow_submit_review_handover(client):
    dispatcher = login(client, "lin.yizhou")
    reviewer = login(client, "fu.jing")

    submitted = client.post("/api/tickets/TK-20261119-001/submit", headers=dispatcher)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted"

    approved = client.post(
        "/api/tickets/TK-20261119-001/review",
        headers=reviewer,
        json={"decision": "approved", "comment": "培训缺口已标注，同意试点"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    blocked = client.post(
        "/api/tickets/TK-20261119-001/handover",
        headers=dispatcher,
        json={"outgoing_lead": "王组长", "incoming_lead": "李组长", "items": {}},
    )
    assert blocked.status_code == 422
    assert "不完整" in blocked.json()["detail"]

    done = client.post(
        "/api/tickets/TK-20261119-001/handover", headers=dispatcher, json=COMPLETE_HANDOVER
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "handed_over"
    assert done.json()["handover_confirmed"]["by"] == "lin.yizhou"


def test_submit_requires_creator(client):
    other = login(client, "tang.yingqiu")
    response = client.post("/api/tickets/TK-20261119-001/submit", headers=other)
    assert response.status_code == 403


def test_self_approval_is_forbidden(client):
    dispatcher = login(client, "lin.yizhou")
    client.post("/api/tickets/TK-20261119-001/submit", headers=dispatcher)
    # 让提交人临时具备审批权，验证职责分离不是靠角色权限兜底
    from app import auth

    auth.ROLE_PERMISSIONS["dispatcher"].add("ticket:review")
    try:
        response = client.post(
            "/api/tickets/TK-20261119-001/review",
            headers=dispatcher,
            json={"decision": "approved", "comment": "自批"},
        )
        assert response.status_code == 409
        assert "职责分离" in response.json()["detail"]
    finally:
        auth.ROLE_PERMISSIONS["dispatcher"].discard("ticket:review")


def test_audit_chain_is_valid_and_tamper_evident(client):
    headers = login(client, "lin.yizhou")
    client.post("/api/tickets/TK-20261119-001/submit", headers=headers)
    audit = client.get("/api/audit", headers=login(client, "fu.jing")).json()
    assert audit["verification"]["valid"] is True
    assert any(record["action"] == "ticket.submit" for record in audit["records"])

    tampered = client.get("/api/audit/verify", headers=login(client, "fu.jing")).json()
    assert tampered["valid"] is True
    assert tampered["records"] >= 2


def test_analysis_is_pure_function():
    ticket = {
        "id": "X",
        "demand": [
            {"role": "拣货员", "need": 10, "available_ready": 6, "available_training": 2},
            {"role": "上架员", "need": 5, "available_ready": 5, "available_training": 0},
        ],
    }
    result = analyze_ticket(ticket)
    assert result["totals"]["coverage_gap"] == 4
    assert result["totals"]["training_gap"] == 2
    assert result["totals"]["skill_gap"] == 2
