"""Deterministic lexical/symbol baseline plus bounded typed graph expansion."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import math
import re
from issueintel.repository.snapshot import source_lines


def tokens(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z_0-9]*", text)
    result = []
    for word in words:
        result.append(word.lower())
        result.extend(part.lower() for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)", word) if part.lower() != word.lower())
    return result


def retrieve_evidence(query: str, snapshot, graph, *, top_k: int = 8, expand: int = 4) -> list[dict]:
    if not 1 <= top_k <= 50 or not 0 <= expand <= 20:
        raise ValueError("Retrieval limits out of range")
    snapshot.verify()
    if graph.snapshot_sha256 != snapshot.snapshot_sha256 or graph.commit_sha != snapshot.commit_sha:
        raise ValueError("Graph belongs to another snapshot")
    documents = []
    for file in snapshot.files:
        lines = source_lines(file.text)
        # Overlapping bounded chunks preserve enough context without giant prompts.
        for start in range(0, len(lines), 15):
            excerpt_lines = []
            characters = 0
            for line in lines[start:start + 40]:
                if excerpt_lines and characters + len(line) + 1 > 1600:
                    break
                excerpt_lines.append(line[:1600])
                characters += len(line[:1600]) + 1
            excerpt = "\n".join(excerpt_lines)
            documents.append({"path": file.path, "start": start + 1, "end": start + len(excerpt_lines),
                              "text": excerpt, "words": Counter(tokens(file.path + " " + excerpt))})
    if not documents:
        return []
    frequencies = Counter(word for doc in documents for word in doc["words"])
    average = sum(sum(doc["words"].values()) for doc in documents) / len(documents) or 1
    query_words = set(tokens(query))
    for doc in documents:
        length, score = sum(doc["words"].values()), 0.0
        for word in query_words:
            frequency = doc["words"][word]
            if frequency:
                idf = math.log(1 + (len(documents) - frequencies[word] + 0.5) / (frequencies[word] + 0.5))
                score += idf * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length / average))
        path_words = set(tokens(doc["path"]))
        doc["score"] = score + 2.0 * len(path_words & query_words)
    ranked = sorted(documents, key=lambda doc: (-doc["score"], doc["path"], doc["start"]))
    seeds = []
    for doc in ranked:
        if doc["score"] <= 0:
            continue
        if any(prior["path"] == doc["path"] and max(prior["start"], doc["start"]) <= min(prior["end"],doc["end"]) for prior in seeds):
            continue
        seeds.append(doc)
        if len(seeds) == top_k:
            break
    selected = list(seeds)
    seed_paths = {doc["path"] for doc in seeds}
    node_map = {node.id: node for node in graph.nodes}
    neighbours = set()
    for edge in graph.edges:
        left, right = node_map.get(edge.source), node_map.get(edge.target)
        if left and right and left.path in seed_paths and right.path not in seed_paths and edge.resolution == "exact":
            neighbours.add(right.path)
        if left and right and right.path in seed_paths and left.path not in seed_paths and edge.resolution == "exact":
            neighbours.add(left.path)
    chosen_paths = set()
    for doc in ranked:
        if doc["path"] in neighbours and doc["path"] not in chosen_paths:
            selected.append({**doc, "via_graph": True})
            chosen_paths.add(doc["path"])
            if len(chosen_paths) >= expand:
                break
    if expand == 0:
        selected = seeds
    evidence = []
    for doc in selected:
        locator = f"{doc['path']}:{doc['start']}-{doc['end']}"
        sha = hashlib.sha256(doc["text"].encode("utf-8")).hexdigest()
        identity = hashlib.sha256(f"{snapshot.snapshot_sha256}:{locator}:{sha}".encode()).hexdigest()[:20]
        evidence.append({"id": "e_" + identity, "kind": "code", "locator": locator,
            "text": doc["text"], "sha256": sha, "revision": snapshot.commit_sha,
            "available_at": snapshot.availability.get("available_at"),
            "provenance": {"snapshot_sha256": snapshot.snapshot_sha256, "repository": snapshot.repository,
                "protocol": snapshot.protocol, "retrieval": "graph_neighbour" if doc.get("via_graph") else "bm25_symbol",
                "excerpt_limit_characters": 1600,
                "score": round(doc["score"], 6), "score_is_probability": False}})
    return evidence
