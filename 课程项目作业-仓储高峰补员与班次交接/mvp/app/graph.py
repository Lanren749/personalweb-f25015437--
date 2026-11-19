"""知识图谱 / LLM Wiki：把角色、对象、规则、事件、指标、阶段连接起来。

- 图谱数据来自 data/graph.json，可人工维护；
- Wiki 词条由图谱节点 + 关系 + 知识库片段组合而成，不做生成式扩写；
- 每个节点标注状态与来源，pending 字段在词条中单列为“待核实”。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

TYPE_LABELS = {
    "role": "角色",
    "object": "业务对象",
    "rule": "规则",
    "event": "事件",
    "metric": "指标",
    "stage": "阶段",
}

STATUS_LABELS = {"confirmed": "训练环境已确认", "pending": "待核实"}


class KnowledgeGraph:
    def __init__(self, path: str | Path, kb=None) -> None:
        self.path = Path(path)
        self.kb = kb
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.project = data.get("project", "")
        self.disclaimer = data.get("disclaimer", "")
        self.nodes = data.get("nodes", [])
        self.edges = data.get("edges", [])
        self.by_id = {node["id"]: node for node in self.nodes}

    def overview(self) -> dict:
        counts = Counter(node["type"] for node in self.nodes)
        return {
            "project": self.project,
            "disclaimer": self.disclaimer,
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "types": [
                {"type": key, "label": TYPE_LABELS.get(key, key), "count": value}
                for key, value in counts.items()
            ],
        }

    def graph(self) -> dict:
        return {
            "project": self.project,
            "disclaimer": self.disclaimer,
            "nodes": [
                {
                    "id": node["id"],
                    "label": node["label"],
                    "type": node["type"],
                    "type_label": TYPE_LABELS.get(node["type"], node["type"]),
                    "status": node.get("status", "pending"),
                    "status_label": STATUS_LABELS.get(node.get("status", "pending"), "待核实"),
                    "x": node.get("x", 0),
                    "y": node.get("y", 0),
                    "summary": node.get("summary", ""),
                }
                for node in self.nodes
            ],
            "edges": self.edges,
        }

    def list_wiki(self, query: str = "") -> list[dict]:
        query = query.strip().lower()
        items = []
        for node in self.nodes:
            haystack = f"{node['label']} {node.get('summary', '')}".lower()
            if query and query not in haystack:
                continue
            items.append(
                {
                    "id": node["id"],
                    "label": node["label"],
                    "type": node["type"],
                    "type_label": TYPE_LABELS.get(node["type"], node["type"]),
                    "summary": node.get("summary", ""),
                    "status": node.get("status", "pending"),
                    "status_label": STATUS_LABELS.get(node.get("status", "pending"), "待核实"),
                }
            )
        return items

    def wiki(self, node_id: str) -> dict | None:
        node = self.by_id.get(node_id)
        if not node:
            return None
        outgoing = [
            {
                "source": edge["source"],
                "target": edge["target"],
                "target_label": self.by_id.get(edge["target"], {}).get("label", edge["target"]),
                "relation": edge["relation"],
                "status": edge.get("status", "pending"),
            }
            for edge in self.edges
            if edge["source"] == node_id
        ]
        incoming = [
            {
                "source": edge["source"],
                "source_label": self.by_id.get(edge["source"], {}).get("label", edge["source"]),
                "target": edge["target"],
                "relation": edge["relation"],
                "status": edge.get("status", "pending"),
            }
            for edge in self.edges
            if edge["target"] == node_id
        ]
        attributes = node.get("attributes", {})
        unresolved = [
            f"{key}：{value}"
            for key, value in attributes.items()
            if any(marker in str(value) for marker in ("待", "缺失", "未", "不明确"))
        ]
        related = []
        if self.kb is not None:
            query = f"{node['label']} {node.get('summary', '')}"
            related = [
                {
                    "id": hit["id"],
                    "doc_title": hit["doc_title"],
                    "section": hit["section"],
                    "snippet": hit["snippet"],
                    "score": hit["score"],
                }
                for hit in self.kb.search(query, top_k=3)
            ]
        return {
            "id": node["id"],
            "label": node["label"],
            "type": node["type"],
            "type_label": TYPE_LABELS.get(node["type"], node["type"]),
            "summary": node.get("summary", ""),
            "attributes": attributes,
            "status": node.get("status", "pending"),
            "status_label": STATUS_LABELS.get(node.get("status", "pending"), "待核实"),
            "sources": node.get("sources", []),
            "outgoing": outgoing,
            "incoming": incoming,
            "unresolved": unresolved,
            "related_kb": related,
            "disclaimer": self.disclaimer,
        }
