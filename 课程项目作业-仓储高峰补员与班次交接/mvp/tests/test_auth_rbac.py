from tests.conftest import login


def test_health_is_public(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["kb_chunks"] > 0
    assert body["graph_nodes"] >= 20


def test_login_success_and_wrong_password(client):
    ok = client.post("/api/auth/login", json={"username": "lin.yizhou", "password": "demo123"})
    assert ok.status_code == 200
    assert ok.json()["user"]["role"] == "dispatcher"
    assert ok.json()["user"]["permissions"]

    bad = client.post("/api/auth/login", json={"username": "lin.yizhou", "password": "wrong"})
    assert bad.status_code == 401


def test_token_required(client):
    assert client.get("/api/tickets").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_viewer_cannot_create_ticket(client):
    headers = login(client, "ye.shu")
    response = client.post(
        "/api/tickets",
        headers=headers,
        json={"site": "华东-苏州仓", "peak": "双11", "shift": "夜班", "demand": []},
    )
    assert response.status_code == 403
    assert "ticket:create" in response.json()["detail"]


def test_operator_cannot_read_others_ticket(client):
    headers = login(client, "qin.rui")
    response = client.get("/api/tickets/TK-20261119-001", headers=headers)
    assert response.status_code == 403
    assert "范围" in response.json()["detail"]


def test_tenant_isolation(client):
    admin = login(client, "admin", "admin123")
    created = client.post(
        "/api/tickets",
        headers=admin,
        json={
            "site": "华北-天津仓",
            "peak": "双11",
            "shift": "白班",
            "tenant": "fsg-north",
            "demand": [
                {
                    "role": "拣货员",
                    "need": 5,
                    "available_ready": 5,
                    "required_cert": "无",
                    "required_training": "WMS 2 小时",
                }
            ],
        },
    )
    assert created.status_code == 201
    ticket_id = created.json()["id"]

    tenant_admin = login(client, "gu.minglan")
    assert client.get(f"/api/tickets/{ticket_id}", headers=tenant_admin).status_code == 403


def test_user_management_is_platform_admin_only(client):
    assert client.get("/api/users", headers=login(client, "gu.minglan")).status_code == 403
    response = client.get("/api/users", headers=login(client, "admin", "admin123"))
    assert response.status_code == 200
    assert len(response.json()["users"]) >= 7


def test_reviewer_can_read_audit_but_dispatcher_cannot(client):
    assert client.get("/api/audit", headers=login(client, "fu.jing")).status_code == 200
    assert client.get("/api/audit", headers=login(client, "lin.yizhou")).status_code == 403
