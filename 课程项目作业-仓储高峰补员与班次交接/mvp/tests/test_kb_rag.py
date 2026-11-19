from app.main import kb
from tests.conftest import login


def test_ask_returns_citation_and_answer(client):
    headers = login(client, "lin.yizhou")
    response = client.post("/api/kb/ask", headers=headers, json={"query": "叉车司机需要什么证书"})
    assert response.status_code == 200
    body = response.json()
    assert body["answered"] is True
    assert body["citations"]
    assert body["citations"][0]["doc_id"] == "KB-01"
    assert "叉车证" in body["answer"]


def test_refusal_when_no_evidence(client):
    headers = login(client, "lin.yizhou")
    response = client.post("/api/kb/ask", headers=headers, json={"query": "今天苏州的天气怎么样"})
    body = response.json()
    assert body["answered"] is False
    assert body["citations"] == []
    assert "没有找到足够依据" in body["answer"]


def test_answer_is_extractive_only(client):
    headers = login(client, "fu.jing")
    corpus = "".join(kb.corpus_sentences())
    for query in ["交接单必须包含哪些内容", "到岗即可独立上岗率怎么计算", "审计记录需要包含哪些要素"]:
        body = client.post("/api/kb/ask", headers=headers, json={"query": query}).json()
        assert body["answered"], query
        for sentence in body["sentences"]:
            assert sentence["text"] in corpus, sentence["text"]


def test_search_returns_snippet(client):
    headers = login(client, "ye.shu")
    body = client.get("/api/kb/search", headers=headers, params={"q": "培训缺口"}).json()
    assert body["results"]
    assert body["results"][0]["snippet"]


def test_documents_listed(client):
    headers = login(client, "gu.minglan")
    body = client.get("/api/kb/docs", headers=headers).json()
    assert len(body["documents"]) == 5
    assert body["chunks"] == len(kb.chunks)
