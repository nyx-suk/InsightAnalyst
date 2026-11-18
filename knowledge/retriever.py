"""knowledge/retriever.py
Knowledge retriever module. Retrieves grounded schema notes, metric definitions,
and business rules relevant to the user's business question.
"""

import os
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml
except ImportError:
    yaml = None

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
COLLECTION_NAME = "insight_knowledge"


def _fallback_keyword_retrieve(question: str, k: int = 4) -> List[Dict[str, Any]]:
    """Graceful fallback retrieval using token matching if ChromaDB is not yet initialized."""
    base_dir = Path(__file__).resolve().parent
    tokens = set(re_word.lower() for re_word in question.replace("?", "").replace(",", "").split() if len(re_word) > 2)
    
    candidates = []

    # 1. Check metrics
    metrics_path = base_dir / "metrics.yaml"
    if metrics_path.exists():
        try:
            with open(metrics_path, "r", encoding="utf-8") as f:
                content = f.read()
            if yaml:
                data = yaml.safe_load(content) or {}
                for name, info in data.get("metrics", {}).items():
                    text = (
                        f"Metric: {info.get('name', name)}\n"
                        f"Formula: {info.get('formula')}\n"
                        f"Table: {info.get('source_table')}\n"
                        f"Caveats: {'; '.join(info.get('caveats', []))}\n"
                        f"Example SQL:\n{info.get('example_sql', '').strip()}"
                    )
                    score = sum(1 for t in tokens if t in text.lower())
                    if name.lower() in question.lower() or ("sales" in question.lower() and name == "revenue"):
                        score += 5
                    candidates.append({
                        "id": f"metric_{name}",
                        "document": text,
                        "metadata": {"type": "metric", "name": name, "source": "metrics.yaml"},
                        "score": score
                    })
            else:
                # Basic text parsing when yaml is not installed yet
                score = sum(1 for t in tokens if t in content.lower())
                if "sales" in question.lower() or "revenue" in question.lower():
                    score += 5
                candidates.append({
                    "id": "metrics_yaml",
                    "document": content,
                    "metadata": {"type": "metric", "name": "metrics_definition", "source": "metrics.yaml"},
                    "score": score
                })
        except Exception:
            pass

    # 2. Check schema and rules
    for md_file, doc_type in [("schema_notes.md", "schema"), ("business_rules.md", "rule")]:
        fpath = base_dir / md_file
        if fpath.exists():
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    sections = f.read().split("\n## ")
                for idx, sec in enumerate(sections):
                    clean_sec = ("## " + sec).strip() if idx > 0 else sec.strip()
                    if not clean_sec:
                        continue
                    first_line = clean_sec.splitlines()[0].replace("#", "").strip()
                    score = sum(1 for t in tokens if t in clean_sec.lower())
                    if "region" in tokens and "region" in clean_sec.lower():
                        score += 3
                    if ("month" in tokens or "time" in tokens) and "month" in clean_sec.lower():
                        score += 3
                    candidates.append({
                        "id": f"{fpath.stem}_{idx}",
                        "document": clean_sec,
                        "metadata": {"type": doc_type, "name": first_line, "source": md_file},
                        "score": score
                    })
            except Exception:
                pass

    candidates.sort(key=lambda x: x["score"], reverse=True)
    results = []
    for c in candidates[:k]:
        results.append({
            "id": c["id"],
            "document": c["document"],
            "metadata": c["metadata"]
        })
    return results


def retrieve(question: str, k: int = 4) -> List[Dict[str, Any]]:
    """Retrieves top-k relevant knowledge chunks for a given natural-language question.

    Args:
        question: The user's query in natural English.
        k: Maximum number of knowledge snippets to return.

    Returns:
        List of dicts containing "id", "document", and "metadata".
    """
    try:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        collection = client.get_collection(name=COLLECTION_NAME)
        results = collection.query(
            query_texts=[question],
            n_results=k
        )
        retrieved: List[Dict[str, Any]] = []
        if results and "documents" in results and results["documents"]:
            docs = results["documents"][0]
            metas = results["metadatas"][0] if "metadatas" in results else [{}] * len(docs)
            ids = results["ids"][0] if "ids" in results else [""] * len(docs)
            for doc_id, doc, meta in zip(ids, docs, metas):
                retrieved.append({
                    "id": doc_id,
                    "document": doc,
                    "metadata": meta
                })
            return retrieved
    except Exception:
        # ChromaDB not installed or index not built yet; fall back gracefully
        pass

    return _fallback_keyword_retrieve(question, k=k)


if __name__ == "__main__":
    sample_q = "Why did sales drop in the South region last month?"
    print(f"Testing retrieval for: '{sample_q}'")
    chunks = retrieve(sample_q, k=3)
    for i, chunk in enumerate(chunks, 1):
        print(f"\n--- Chunk {i} [{chunk['metadata'].get('type')} : {chunk['metadata'].get('name')}] ---")
        print(chunk["document"][:200] + "...")
