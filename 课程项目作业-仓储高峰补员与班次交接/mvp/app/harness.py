"""Harness：把业务规则、RAG 质量与权限矩阵跑成可复现的检查清单。

运行：python harness.py
也可以通过 GET /api/harness/run 在服务内执行。
"""

from __future__ import annotations

import json
from pathlib import Path

from app import auth
from app.domain import analyze_ticket, evaluate_handover, match_candidates
from app.graph import KnowledgeGraph
from app.kb import KnowledgeBase
from app.store import BASE_DIR, Store, verify_audit

CASES_PATH = BASE_DIR / "tests" / "eval_cases.json"


def _check(name: str, passed: bool, detail: str, weight: int = 1) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail, "weight": weight}


def run_harness(kb: KnowledgeBase, graph: KnowledgeGraph, store: Store) -> dict:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    checks: list[dict] = []
    metrics: dict = {}

    # 1. 检索覆盖与答案可追溯
    retrieval_hits = 0
    citation_ok = 0
    contains_ok = 0
    for case in cases["retrieval"]:
        hits = kb.search(case["query"], top_k=4)
        result = kb.ask(case["query"], top_k=4)
        citations = result.get("citations", [])
        allowed_docs = set(case.get("acceptable", [case["expected_doc"]]))
        hit = bool(hits) and hits[0]["doc_id"] in allowed_docs
        doc_cited = any(item["doc_id"] in allowed_docs for item in citations)
        retrieval_hits += 1 if hit else 0
        cited = bool(citations) and result.get("answered")
        citation_ok += 1 if cited else 0
        missing = [word for word in case.get("must_contain", []) if word not in result.get("answer", "")]
        contains_ok += 1 if not missing else 0
        checks.append(
            _check(
                f"检索：{case['query']}",
                hit and doc_cited and not missing,
                f"首位命中 {hits[0]['id'] if hits else '无'}，引用 {citations[0]['id'] if citations else '无'}，缺少关键词 {missing or '无'}",
            )
        )
    total = len(cases["retrieval"])
    metrics["retrieval_top1_accuracy"] = round(retrieval_hits / total, 3)
    metrics["answer_citation_coverage"] = round(citation_ok / total, 3)
    metrics["answer_keyword_coverage"] = round(contains_ok / total, 3)

    # 2. 无依据拒答
    refusal_ok = 0
    for case in cases["refusal"]:
        result = kb.ask(case["query"], top_k=4)
        passed = result.get("answered") is False
        refusal_ok += 1 if passed else 0
        checks.append(_check(f"拒答：{case['query']}", passed, "无依据时停止作答" if passed else "错误作答"))
    metrics["refusal_accuracy"] = round(refusal_ok / len(cases["refusal"]), 3)

    # 3. 引用句子必须来自原文（防编造）
    corpus = "".join(kb.corpus_sentences())
    extractive_ok = 0
    sample_questions = [case["query"] for case in cases["retrieval"]]
    for query in sample_questions:
        result = kb.ask(query)
        if not result.get("answered"):
            continue
        if all(item["text"] in corpus for item in result["sentences"]):
            extractive_ok += 1
    metrics["extractive_answer_rate"] = round(extractive_ok / len(sample_questions), 3)
    checks.append(
        _check(
            "回答为抽取式，不生成原文以外的句子",
            extractive_ok == len(sample_questions),
            f"{extractive_ok}/{len(sample_questions)} 个回答的句子全部来自知识库",
        )
    )

    # 4. 缺口分解
    state = store.read()
    ticket = next((item for item in state["tickets"] if item["id"] == cases["business"]["seed_ticket"]), None)
    if ticket:
        analysis = analyze_ticket(ticket)
        expected = cases["business"]["expected_totals"]
        totals = analysis["totals"]
        gap_ok = all(totals[key] == expected[key] for key in expected)
        checks.append(
            _check(
                "缺口分解：需求/可用/培训/技能四值一致",
                gap_ok,
                f"实际 {totals}",
                weight=2,
            )
        )
        line_sum = sum(line["coverage_gap"] for line in analysis["lines"])
        checks.append(
            _check("缺口守恒：各行覆盖缺口之和等于总缺口", line_sum == totals["coverage_gap"], f"{line_sum}")
        )
        checks.append(
            _check(
                "技能与培训不混算",
                totals["training_gap"] + totals["skill_gap"] == totals["coverage_gap"],
                f"培训 {totals['training_gap']} + 技能 {totals['skill_gap']} = {totals['coverage_gap']}",
            )
        )
        candidates = match_candidates(ticket, state["roster"])
        blocked = [item for item in candidates if item["status"] == "不可上岗（证书问题）"]
        checks.append(
            _check(
                "证书过期人员被拦截",
                any(item["candidate_id"] == "C005" for item in blocked),
                "周涛的叉车证已过期",
            )
        )
        checks.append(
            _check(
                "缺口人员不会被标为可立即上岗",
                all(item["status"] != "可立即上岗" for item in candidates if item["candidate_id"] in {"C003", "C009", "C010"}),
                "培训未完成人员保持需培训状态",
            )
        )
    else:
        checks.append(_check("读取种子工单", False, "未找到种子工单"))

    # 5. 交接完整性
    incomplete = evaluate_handover({"outgoing_lead": "A", "incoming_lead": "B", "items": {}})
    complete = evaluate_handover(
        {
            "outgoing_lead": "王组长",
            "incoming_lead": "李组长",
            "items": {
                "unfinished": "A区库位 A-102 剩余 2 托未上架，单据号 D-20261119-08",
                "equipment": "PDA 6 台、对讲机 3 台已归还",
                "risks": "3 号货架照明故障，已报修",
                "confirm": "李组长",
            },
        }
    )
    checks.append(_check("空交接单被拦截", incomplete["ready"] is False, incomplete["message"]))
    checks.append(_check("完整交接单可确认", complete["ready"] is True, complete["message"]))

    # 6. 审计链
    audit = verify_audit(state)
    checks.append(_check("审计哈希链完整", audit["valid"], f"{audit['records']} 条记录"))

    # 7. 权限矩阵
    matrix = {
        "viewer": ("ticket:review", False),
        "dispatcher": ("ticket:review", False),
        "reviewer": ("ticket:review", True),
        "operator": ("ticket:read_all", False),
        "tenant_admin": ("audit:read", True),
        "saas_admin": ("user:manage", True),
    }
    for role, (permission, expected) in matrix.items():
        actual = auth.has_perm({"role": role}, permission)
        checks.append(
            _check(f"权限：{role} / {permission}", actual == expected, f"期望 {expected}，实际 {actual}")
        )

    # 8. 图谱完整性
    overview = graph.overview()
    checks.append(_check("知识图谱节点与关系可用", overview["nodes"] >= 20 and overview["edges"] >= 20, str(overview["types"])))
    wiki = graph.wiki("rule-gap")
    checks.append(
        _check(
            "Wiki 词条包含关系与待核实字段",
            bool(wiki and wiki["outgoing"] and wiki["related_kb"]),
            f"关系 {len(wiki['outgoing']) if wiki else 0} 条，关联片段 {len(wiki['related_kb']) if wiki else 0} 条",
        )
    )

    passed = sum(1 for item in checks if item["passed"])
    total_checks = len(checks)
    score = round(100 * sum(item["weight"] for item in checks if item["passed"]) / sum(item["weight"] for item in checks), 1)
    thresholds = {
        "retrieval_top1_accuracy": 0.8,
        "answer_citation_coverage": 1.0,
        "refusal_accuracy": 1.0,
        "extractive_answer_rate": 1.0,
    }
    threshold_ok = all(metrics.get(key, 0) >= value for key, value in thresholds.items())
    return {
        "passed": passed == total_checks and threshold_ok,
        "score": score,
        "summary": f"{passed}/{total_checks} 项检查通过，阈值判定{'通过' if threshold_ok else '未通过'}",
        "metrics": metrics,
        "thresholds": thresholds,
        "checks": checks,
        "environment": {
            "kb_chunks": len(kb.chunks),
            "graph_nodes": overview["nodes"],
            "graph_edges": overview["edges"],
            "data_dir": str(BASE_DIR),
        },
    }


def main() -> None:
    store = Store()
    kb = KnowledgeBase(BASE_DIR / "data" / "kb")
    graph = KnowledgeGraph(BASE_DIR / "data" / "graph.json", kb)
    result = run_harness(kb, graph, store)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
