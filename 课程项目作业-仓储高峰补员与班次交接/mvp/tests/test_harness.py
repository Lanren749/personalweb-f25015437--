from tests.conftest import login


def test_harness_passes(client):
    headers = login(client, "gu.minglan")
    response = client.get("/api/harness/run", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["passed"] is True, body["summary"]
    metrics = body["metrics"]
    assert metrics["retrieval_top1_accuracy"] >= 0.8
    assert metrics["answer_citation_coverage"] == 1.0
    assert metrics["refusal_accuracy"] == 1.0
    assert metrics["extractive_answer_rate"] == 1.0
    assert body["score"] >= 90


def test_harness_requires_login(client):
    assert client.get("/api/harness/run").status_code == 401
