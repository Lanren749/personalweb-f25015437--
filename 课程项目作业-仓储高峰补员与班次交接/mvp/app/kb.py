"""轻量中文 RAG：BM25 检索 + 抽取式回答 + 无依据拒答。

设计取舍：
- 不依赖向量模型与外部网络，保证课程环境可复现；
- 中文采用“单字 + 二元组”分词，成本低且对短查询稳定；
- 回答只拼接原文句子，不做生成，从机制上避免编造数字；
- 检索覆盖不足时拒绝回答，并给出需要补充的资料。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

STOPWORDS = {
    "的", "了", "和", "与", "及", "或", "在", "是", "有", "为", "对", "从", "到", "中",
    "上", "下", "个", "人", "这", "那", "什么", "如何", "怎么", "请问", "一下", "吗",
    "呢", "啊", "请", "我们", "你们", "他们", "以及", "如果", "可以", "需要", "应该",
}

LATIN_RE = re.compile(r"[a-z0-9]+")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]+")
SENTENCE_RE = re.compile(r"[^。！？；\n]+[。！？；]?")


def tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens: list[str] = []
    for word in LATIN_RE.findall(text):
        if len(word) > 1 or word.isdigit():
            tokens.append(word)
    for run in CHINESE_RE.findall(text):
        tokens.extend(list(run))
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return [token for token in tokens if token and token not in STOPWORDS]


@dataclass
class Chunk:
    id: str
    doc_id: str
    doc_title: str
    section: str
    text: str
    source: str
    kind: str
    tokens: list[str] = field(default_factory=list)
    term_freq: Counter = field(default_factory=Counter)


class KnowledgeBase:
    def __init__(self, kb_dir: str | Path) -> None:
        self.kb_dir = Path(kb_dir)
        self.chunks: list[Chunk] = []
        self._load()
        self._build_index()

    # ---------- 索引 ----------
    def _load(self) -> None:
        for path in sorted(self.kb_dir.glob("*.md")):
            raw = path.read_text(encoding="utf-8")
            doc_id = self._doc_id(path)
            title_match = re.search(r"^#\s+(.+)$", raw, re.MULTILINE)
            doc_title = title_match.group(1).strip() if title_match else path.stem
            kind = "公开资料" if doc_id == "KB-05" else "内部规则（合成）"
            parts = re.split(r"\n##\s+", raw)
            for index, part in enumerate(parts[1:], start=1):
                lines = part.strip().splitlines()
                if not lines:
                    continue
                section = lines[0].strip()
                body = "\n".join(lines[1:]).strip()
                if len(body) < 20:
                    continue
                chunk = Chunk(
                    id=f"{doc_id}-{index:02d}",
                    doc_id=doc_id,
                    doc_title=doc_title,
                    section=section,
                    text=f"{section}\n{body}",
                    source=path.name,
                    kind=kind,
                )
                chunk.tokens = tokenize(chunk.text)
                chunk.term_freq = Counter(chunk.tokens)
                self.chunks.append(chunk)

    @staticmethod
    def _doc_id(path: Path) -> str:
        match = re.match(r"(kb-\d+)", path.stem.lower())
        return match.group(1).upper() if match else path.stem.upper()

    def _build_index(self) -> None:
        self.doc_freq: Counter = Counter()
        for chunk in self.chunks:
            self.doc_freq.update(set(chunk.tokens))
        self.avg_len = (
            sum(len(chunk.tokens) for chunk in self.chunks) / len(self.chunks) if self.chunks else 0.0
        )
        self.total = len(self.chunks)

    # ---------- 检索 ----------
    def _bm25(self, chunk: Chunk, terms: set[str], k1: float = 1.5, b: float = 0.75) -> float:
        score = 0.0
        length = len(chunk.tokens) or 1
        for term in terms:
            freq = chunk.term_freq.get(term, 0)
            if not freq:
                continue
            df = self.doc_freq.get(term, 0)
            idf = math.log(1 + (self.total - df + 0.5) / (df + 0.5))
            denominator = freq + k1 * (1 - b + b * length / (self.avg_len or 1))
            score += idf * (freq * (k1 + 1)) / denominator
        return score

    def search(self, query: str, top_k: int = 4) -> list[dict]:
        terms = set(tokenize(query))
        if not terms:
            return []
        results = []
        for chunk in self.chunks:
            overlap = len(terms & set(chunk.tokens)) / len(terms)
            score = self._bm25(chunk, terms)
            if score <= 0:
                continue
            results.append(
                {
                    "id": chunk.id,
                    "doc_id": chunk.doc_id,
                    "doc_title": chunk.doc_title,
                    "section": chunk.section,
                    "source": chunk.source,
                    "kind": chunk.kind,
                    "score": round(score, 4),
                    "overlap": round(overlap, 4),
                    "snippet": chunk.text.replace("\n", " ")[:140],
                }
            )
        results.sort(key=lambda item: (item["score"], item["overlap"]), reverse=True)
        return results[:top_k]

    # ---------- 问答 ----------
    def ask(self, query: str, top_k: int = 4) -> dict:
        terms = set(tokenize(query))
        hits = self.search(query, top_k=top_k)
        base = {
            "query": query,
            "answered": False,
            "answer": "",
            "sentences": [],
            "citations": [],
            "confidence": "none",
            "query_terms": sorted(terms),
            "limitations": [],
        }
        if not terms:
            base["answer"] = "问题中没有可检索的关键词，请补充岗位、规则或指标名称。"
            base["missing"] = ["有效关键词"]
            return base
        if not hits or hits[0]["overlap"] < 0.3 or hits[0]["score"] < 0.8:
            base["answer"] = "知识库中没有找到足够依据，已停止作答。请补充相关资料或转人工核实。"
            base["missing"] = sorted(terms)[:6]
            base["limitations"] = ["检索覆盖不足，无法给出可追溯回答"]
            return base

        best = hits[0]
        top_chunk = next(chunk for chunk in self.chunks if chunk.id == best["id"])

        # 短规则小节整段引用，避免列表类答案漏项（例如四类交接内容只答出三类）。
        if len(top_chunk.text) <= 520:
            text = " ".join(line.strip() for line in top_chunk.text.splitlines() if line.strip())
            sentences = [
                sentence.strip()
                for sentence in SENTENCE_RE.findall(top_chunk.text)
                if len(sentence.strip()) >= 8
            ]
            confidence = "high" if best["overlap"] >= 0.55 and best["score"] >= 5 else "medium"
            base.update(
                {
                    "answered": True,
                    "answer": f"{text}（{top_chunk.id}）",
                    "sentences": [{"text": sentence, "chunk_id": top_chunk.id} for sentence in sentences],
                    "citations": [
                        {
                            "id": top_chunk.id,
                            "doc_id": top_chunk.doc_id,
                            "doc_title": top_chunk.doc_title,
                            "section": top_chunk.section,
                            "source": top_chunk.source,
                            "kind": top_chunk.kind,
                        }
                    ],
                    "confidence": confidence,
                    "limitations": [
                        "回答为知识库原文整段引用，未做生成式扩写",
                        f"检索覆盖度 {best['overlap']:.0%}，仍建议人工核对标 [待核实] 的条目",
                    ],
                }
            )
            return base

        chunks = [chunk for chunk in self.chunks if chunk.id in {hit["id"] for hit in hits[:2]}]
        ranked: list[tuple[float, str, Chunk]] = []
        for chunk in chunks:
            for sentence in SENTENCE_RE.findall(chunk.text):
                sentence = sentence.strip().lstrip("-*| ").strip()
                if len(sentence) < 12 or set(tokenize(sentence)).isdisjoint(terms):
                    continue
                sentence_terms = set(tokenize(sentence))
                coverage = len(sentence_terms & terms) / max(1, len(terms))
                density = len(sentence_terms & terms) / max(1, len(sentence_terms))
                ranked.append((coverage * 0.7 + density * 0.3, sentence, chunk))
        ranked.sort(key=lambda item: item[0], reverse=True)

        picked: list[tuple[str, Chunk]] = []
        used: list[str] = []
        for _, sentence, chunk in ranked:
            if sentence in used:
                continue
            if sum(len(text) for text, _ in picked) + len(sentence) > 460 and picked:
                break
            picked.append((sentence, chunk))
            used.append(sentence)
            if len(picked) >= 4:
                break

        if not picked:
            base["answer"] = "知识库命中段落中没有可直接引用的句子，已停止作答。"
            base["limitations"] = ["命中内容不可抽取"]
            return base

        citations = []
        seen = set()
        for _, chunk in picked:
            if chunk.id in seen:
                continue
            seen.add(chunk.id)
            citations.append(
                {
                    "id": chunk.id,
                    "doc_id": chunk.doc_id,
                    "doc_title": chunk.doc_title,
                    "section": chunk.section,
                    "source": chunk.source,
                    "kind": chunk.kind,
                }
            )

        confidence = "high" if best["overlap"] >= 0.55 and best["score"] >= 5 else "medium"
        base.update(
            {
                "answered": True,
                "answer": "".join(f"{sentence}（{chunk.id}）" for sentence, chunk in picked),
                "sentences": [{"text": sentence, "chunk_id": chunk.id} for sentence, chunk in picked],
                "citations": citations,
                "confidence": confidence,
                "limitations": [
                    "回答由知识库原文句子拼接，未做生成式扩写",
                    f"检索覆盖度 {best['overlap']:.0%}，仍建议人工核对标 [待核实] 的条目",
                ]
                if best["kind"] != "公开资料"
                else ["公开资料为官网宣传口径，不能作为客户实测数据"],
            }
        )
        return base

    def documents(self) -> list[dict]:
        seen: dict[str, dict] = {}
        for chunk in self.chunks:
            item = seen.setdefault(
                chunk.doc_id,
                {
                    "doc_id": chunk.doc_id,
                    "title": chunk.doc_title,
                    "source": chunk.source,
                    "kind": chunk.kind,
                    "sections": [],
                },
            )
            item["sections"].append({"id": chunk.id, "title": chunk.section})
        return list(seen.values())

    def corpus_sentences(self) -> Iterable[str]:
        for chunk in self.chunks:
            yield from [sentence.strip() for sentence in SENTENCE_RE.findall(chunk.text) if sentence.strip()]
