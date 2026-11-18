"""knowledge/retrieve.py
Retrieval module as specified in Prompt 1.5.
Provides retrieve(question: str, k: int = 6) -> list[dict] returning text, type, id, distance.
Includes robust offline keyword fallback when ChromaDB is not installed or indexed.
"""

import os
from pathlib import Path
from typing import Any, Dict, List
import re

try:
    import yaml
except ImportError:
    yaml = None

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
COLLECTION_NAME = "insight_knowledge"


def _fallback_retrieve(question: str, k: int = 6) -> List[Dict[str, Any]]:
    """Local lexical retrieval returning standard Prompt 1.5 shape when ChromaDB is offline."""
    base_dir = Path(__file__).resolve().parent
    raw_tokens = set(re.findall(r"\w+", question.lower()))
    stemmed_tokens = set(
        t[:-3] + "y" if t.endswith("ies") else (t[:-1] if t.endswith("s") and len(t) > 3 else t)
        for t in raw_tokens
    )
    tokens = raw_tokens.union(stemmed_tokens)

    candidates = []

    # 1. Rules from rules.yaml
    rules_path = base_dir / "rules.yaml"
    if rules_path.exists():
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                content = f.read()
            if yaml:
                data = yaml.safe_load(content) or {}
                rules_list = data.get("rules", [])
            else:
                # Basic parser if pyyaml is not installed
                rules_list = []
                current_rule = {}
                for line in content.splitlines():
                    if line.strip().startswith("- id:"):
                        if current_rule:
                            rules_list.append(current_rule)
                        rule_id = line.split(":", 1)[1].strip().strip('"').strip("'")
                        current_rule = {"id": rule_id, "title": "", "text": ""}
                    elif "title:" in line and current_rule:
                        current_rule["title"] = line.split(":", 1)[1].strip().strip('"').strip("'")
                    elif current_rule and not line.strip().startswith("#"):
                        current_rule["text"] += " " + line.strip()
                if current_rule:
                    rules_list.append(current_rule)

            for item in rules_list:
                text = f"Rule: {item.get('title', '')}\n{item.get('text', '').strip()}"
                score = sum(1 for t in tokens if t in text.lower())
                item_id = item.get("id", "")
                if "region" in tokens and "region" in item_id:
                    score += 15
                if ("month" in tokens or "date" in tokens or "time" in tokens) and "time" in item_id:
                    score += 15
                if ("reconciliation" in tokens or "total" in tokens) and "reconciliation" in item_id:
                    score += 15
                if ("freight" in tokens or "formula" in tokens) and "revenue" in item_id:
                    score += 15
                candidates.append({
                    "id": item_id,
                    "text": text,
                    "type": "rule",
                    "score": score
                })
        except Exception:
            pass

    # 2. Metrics from metrics.yaml
    metrics_path = base_dir / "metrics.yaml"
    if metrics_path.exists():
        try:
            with open(metrics_path, "r", encoding="utf-8") as f:
                content = f.read()
            if yaml:
                data = yaml.safe_load(content) or {}
                metrics_dict = data.get("metrics", {})
            else:
                # Basic parser if pyyaml is not installed
                metrics_dict = {}
                current_m = None
                for line in content.splitlines():
                    if re.match(r"^\s{2}[a-zA-Z0-9_]+:\s*$", line):
                        current_m = line.strip().rstrip(":")
                        metrics_dict[current_m] = {"name": current_m, "description": "", "caveats": [], "synonyms": []}
                    elif current_m and ":" in line:
                        key_part, val_part = line.split(":", 1)
                        clean_k = key_part.strip()
                        clean_v = val_part.strip().strip('"').strip("'")
                        if clean_k == "name":
                            metrics_dict[current_m]["name"] = clean_v
                        elif clean_k == "description":
                            metrics_dict[current_m]["description"] = clean_v
                        elif clean_k == "formula_sql":
                            metrics_dict[current_m]["formula_sql"] = clean_v
                    elif current_m and line.strip().startswith("- "):
                        metrics_dict[current_m]["caveats"].append(line.strip().lstrip("- ").strip('"'))

            for key, info in metrics_dict.items():
                synonyms = info.get("synonyms", [])
                text = (
                    f"Metric: {info.get('name', key)}\n"
                    f"Description: {info.get('description', '')}\n"
                    f"Synonyms: {', '.join(synonyms)}\n"
                    f"Formula SQL: {info.get('formula_sql', '')}\n"
                    f"Caveats: {'; '.join(info.get('caveats', []))}"
                )
                score = sum(1 for t in tokens if t in text.lower())
                # Keyword boosts for precision
                if key.lower() in tokens or ("sales" in tokens and key == "revenue"):
                    score += 20
                if "revenue" in tokens and key == "revenue":
                    score += 20
                if ("volume" in tokens or "units" in tokens) and key == "units_sold":
                    score += 20
                if ("orders" in tokens or "order" in tokens) and ("count" in tokens or "many" in tokens or "number" in tokens) and key == "order_count":
                    score += 25
                if ("average" in tokens or "aov" in tokens) and key == "average_order_value":
                    score += 25
                candidates.append({
                    "id": f"metric_{key}",
                    "text": text,
                    "type": "metric",
                    "score": score
                })
        except Exception:
            pass

    # 3. Schema notes
    schema_path = base_dir / "schema_notes.md"
    if schema_path.exists():
        try:
            with open(schema_path, "r", encoding="utf-8") as f:
                content = f.read()
            sections = re.split(r'\n(?=## )', content)
            for sec in sections:
                clean = sec.strip()
                if not clean or clean.startswith("# Northwind"):
                    continue
                first_line = clean.splitlines()[0].replace("#", "").strip().replace("[", "").replace("]", "")
                table_name = first_line.split()[0]
                t_lower = table_name.lower()
                score = sum(1 for t in tokens if t in clean.lower())
                if t_lower in tokens or (t_lower[:-1] in tokens if t_lower.endswith("s") else False) or (t_lower[:-3] + "y" in tokens if t_lower.endswith("ies") else False):
                    score += 15
                candidates.append({
                    "id": f"table_{table_name.lower()}",
                    "text": clean,
                    "type": "table",
                    "score": score
                })
        except Exception:
            pass

    candidates.sort(key=lambda x: x["score"], reverse=True)
    results = []
    for rank, c in enumerate(candidates[:k]):
        # Simulated distance (lower is better, between 0.0 and 1.0)
        simulated_distance = max(0.05, 1.0 - (c["score"] * 0.1))
        results.append({
            "text": c["text"],
            "type": c["type"],
            "id": c["id"],
            "distance": round(simulated_distance, 4)
        })
    return results


def retrieve(question: str, k: int = 6) -> List[Dict[str, Any]]:
    """Retrieves the top-k most relevant knowledge chunks for a business question.

    Args:
        question: The user query in natural language.
        k: Maximum number of chunks to return (default 6).

    Returns:
        List of dicts with keys: 'text', 'type', 'id', 'distance'.
    """
    try:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        collection = client.get_collection(name=COLLECTION_NAME)
        res = collection.query(query_texts=[question], n_results=k)

        if res and "documents" in res and res["documents"]:
            docs = res["documents"][0]
            metas = res["metadatas"][0] if "metadatas" in res else [{}] * len(docs)
            ids = res["ids"][0] if "ids" in res else [""] * len(docs)
            distances = res["distances"][0] if "distances" in res and res["distances"] else [0.0] * len(docs)

            output = []
            for doc_id, doc, meta, dist in zip(ids, docs, metas, distances):
                output.append({
                    "text": doc,
                    "type": meta.get("type", "unknown"),
                    "id": doc_id,
                    "distance": round(dist, 4) if isinstance(dist, float) else 0.0
                })
            return output
    except Exception:
        pass

    return _fallback_retrieve(question, k=k)


if __name__ == "__main__":
    sample = "Why did sales drop in the South region last month?"
    print(f"Retrieving top chunks for: '{sample}'")
    chunks = retrieve(sample, k=6)
    for c in chunks:
        print(f"[{c['type']}] id={c['id']}, dist={c['distance']}")
