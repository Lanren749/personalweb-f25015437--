def test_graph_overview(client):
    headers = {"Authorization": "Bearer " + _token(client, "ye.shu")}
    body = client.get("/api/graph", headers=headers).json()
    assert body["overview"]["nodes"] >= 20
    assert body["overview"]["edges"] >= 20
    assert any(node["type"] == "rule" for node in body["nodes"])


def test_wiki_page_contains_relations_and_sources(client):
    headers = {"Authorization": "Bearer " + _token(client, "lin.yizhou")}
    page = client.get("/api/wiki/rule-gap", headers=headers).json()
    assert page["label"] == "R-03 技能与培训区分"
    assert page["outgoing"]
    assert page["related_kb"]
    assert page["sources"]


def test_wiki_list_search(client):
    headers = {"Authorization": "Bearer " + _token(client, "lin.yizhou")}
    body = client.get("/api/wiki", headers=headers, params={"q": "交接"}).json()
    assert body["items"]
    assert any("交接" in item["label"] for item in body["items"])


def test_unknown_wiki_returns_404(client):
    headers = {"Authorization": "Bearer " + _token(client, "lin.yizhou")}
    assert client.get("/api/wiki/not-exist", headers=headers).status_code == 404


def _token(client, username: str) -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": "demo123"})
    return response.json()["token"]
