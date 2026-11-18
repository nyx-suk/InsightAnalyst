"""knowledge/build_index.py
Builds and persists the ChromaDB vector index from schema notes, metric definitions, and business rules.
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


def load_metrics(filepath: Path) -> List[Dict[str, Any]]:
    """Loads metric definitions from metrics.yaml into discrete knowledge chunks."""
    if not filepath.exists():
        return []
    if yaml is None:
        print("[build_index] pyyaml not installed. Run 'pip install -r requirements.txt'")
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    chunks = []
    metrics = data.get("metrics", {})
    for key, info in metrics.items():
        caveats_str = "; ".join(info.get("caveats", []))
        text = (
            f"Metric: {info.get('name', key)}\n"
            f"Formula: {info.get('formula')}\n"
            f"Table: {info.get('source_table')}\n"
            f"Grain: {info.get('grain')}\n"
            f"Caveats: {caveats_str}\n"
            f"Example SQL:\n{info.get('example_sql', '').strip()}"
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


def load_markdown_sections(filepath: Path, doc_type: str) -> List[Dict[str, Any]]:
    """Splits markdown files into section-level knowledge chunks."""
    if not filepath.exists():
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Split by markdown headers (## )
    sections = re.split(r'\n(?=## )', content)
    chunks = []
    base_name = filepath.stem

    for idx, sec in enumerate(sections):
        clean_text = sec.strip()
        if not clean_text:
            continue
        first_line = clean_text.splitlines()[0].replace("#", "").strip()
        chunks.append({
            "id": f"{base_name}_{idx}",
            "text": clean_text,
            "metadata": {
                "type": doc_type,
                "name": first_line,
                "source": filepath.name
            }
        })
    return chunks


def build_index() -> int:
    """Builds the ChromaDB collection from local knowledge files."""
    try:
        import chromadb
    except ImportError:
        print("[build_index] chromadb is not installed. Please install requirements.txt.")
        return 0

    base_dir = Path(__file__).resolve().parent
    all_chunks: List[Dict[str, Any]] = []

    # 1. Load metrics
    metrics_path = base_dir / "metrics.yaml"
    all_chunks.extend(load_metrics(metrics_path))

    # 2. Load schema notes
    schema_path = base_dir / "schema_notes.md"
    all_chunks.extend(load_markdown_sections(schema_path, "schema"))

    # 3. Load business rules
    rules_path = base_dir / "business_rules.md"
    all_chunks.extend(load_markdown_sections(rules_path, "rule"))

    if not all_chunks:
        print("[build_index] No knowledge chunks found.")
        return 0

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    try:
        client.delete_collection(name=COLLECTION_NAME)
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "Knowledge base for InsightAnalyst text-to-SQL"}
    )

    ids = [c["id"] for c in all_chunks]
    documents = [c["text"] for c in all_chunks]
    metadatas = [c["metadata"] for c in all_chunks]

    collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas
    )

    print(f"[build_index] Successfully indexed {len(all_chunks)} chunks into '{COLLECTION_NAME}' at {CHROATH if 'CHROATH' in locals() else CHROMA_PATH}.")
    return len(all_chunks)


if __name__ == "__main__":
    build_index()
