"""knowledge/build_index.py
Builds and persists the ChromaDB vector index from schema_notes.md, metrics.yaml, and rules.yaml.
Stores discrete chunks with metadata: type ('table' | 'metric' | 'rule') and stable IDs.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml
except ImportError:
    yaml = None

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
COLLECTION_NAME = "insight_knowledge"


def load_schema_notes(filepath: Path) -> List[Dict[str, Any]]:
    """Loads schema notes into one chunk per table section."""
    if not filepath.exists():
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Split by markdown ## headers
    sections = re.split(r'\n(?=## )', content)
    chunks = []
    for sec in sections:
        clean = sec.strip()
        if not clean or clean.startswith("# Northwind"):
            continue
        first_line = clean.splitlines()[0].replace("#", "").strip().replace("[", "").replace("]", "")
        table_name = first_line.split()[0]
        chunks.append({
            "id": f"table_{table_name.lower()}",
            "text": clean,
            "metadata": {
                "type": "table",
                "name": table_name,
                "source": "schema_notes.md"
            }
        })
    return chunks


def load_metrics(filepath: Path) -> List[Dict[str, Any]]:
    """Loads metrics from metrics.yaml into one chunk per metric."""
    if not filepath.exists():
        return []
    if yaml is None:
        print("[build_index] pyyaml not installed. Run 'pip install pyyaml'")
        return []

    with open(filepath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    chunks = []
    for key, info in data.get("metrics", {}).items():
        synonyms_str = ", ".join(info.get("synonyms", []))
        caveats_str = "; ".join(info.get("caveats", []))
        text = (
            f"Metric: {info.get('name', key)}\n"
            f"Description: {info.get('description', '')}\n"
            f"Synonyms: {synonyms_str}\n"
            f"Formula SQL: {info.get('formula_sql', '')}\n"
            f"Grain: {info.get('grain', '')}\n"
            f"Caveats: {caveats_str}\n"
            f"Example Question: {info.get('example_question', '')}"
        )
        chunks.append({
            "id": f"metric_{key}",
            "text": text,
            "metadata": {
                "type": "metric",
                "name": key,
                "source": "metrics.yaml"
            }
        })
    return chunks


def load_rules(filepath: Path) -> List[Dict[str, Any]]:
    """Loads business rules from rules.yaml into one chunk per rule."""
    if not filepath.exists():
        return []
    if yaml is None:
        return []

    with open(filepath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    chunks = []
    for item in data.get("rules", []):
        rule_id = item.get("id", "")
        title = item.get("title", "")
        text = item.get("text", "").strip()
        doc_str = f"Rule: {title}\n{text}"
        chunks.append({
            "id": rule_id,
            "text": doc_str,
            "metadata": {
                "type": "rule",
                "name": title,
                "source": "rules.yaml"
            }
        })
    return chunks


def build_index() -> int:
    """Builds and persists the local ChromaDB index."""
    try:
        import chromadb
    except ImportError:
        print("[build_index] Note: chromadb is not installed. To build index, run 'pip install chromadb'.")
        return 0

    base_dir = Path(__file__).resolve().parent
    all_chunks: List[Dict[str, Any]] = []

    # 1. Schema notes (one chunk per table)
    all_chunks.extend(load_schema_notes(base_dir / "schema_notes.md"))
    # 2. Metrics (one chunk per metric)
    all_chunks.extend(load_metrics(base_dir / "metrics.yaml"))
    # 3. Rules (one chunk per rule)
    all_chunks.extend(load_rules(base_dir / "rules.yaml"))

    if not all_chunks:
        print("[build_index] No knowledge chunks found.")
        return 0

    print("[build_index] Notice: ChromaDB's default embedding model downloads on first use.")
    print("[build_index] Run this once while connected to internet, then the index can be reused offline.")

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    try:
        client.delete_collection(name=COLLECTION_NAME)
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "Knowledge chunks for InsightAnalyst"}
    )

    collection.add(
        ids=[c["id"] for c in all_chunks],
        documents=[c["text"] for c in all_chunks],
        metadatas=[c["metadata"] for c in all_chunks]
    )

    print(f"[build_index] Successfully indexed {len(all_chunks)} chunks into collection '{COLLECTION_NAME}' at '{CHROMA_PATH}'.")
    return len(all_chunks)


if __name__ == "__main__":
    build_index()
