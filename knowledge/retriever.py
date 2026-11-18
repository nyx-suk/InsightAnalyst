"""knowledge/retriever.py
Backwards compatibility wrapper pointing to knowledge/retrieve.py.
Ensures both 'document' and 'text' keys are accessible.
"""

from typing import Any, Dict, List
from knowledge.retrieve import retrieve as _retrieve_base


def retrieve(question: str, k: int = 6) -> List[Dict[str, Any]]:
    """Retrieves top-k knowledge snippets, supporting legacy 'document' and new 'text' keys."""
    raw_results = _retrieve_base(question, k=k)
    adapted = []
    for r in raw_results:
        adapted.append({
            "id": r["id"],
            "document": r["text"],
            "text": r["text"],
            "type": r["type"],
            "distance": r["distance"],
            "metadata": {"type": r["type"], "name": r["id"]}
        })
    return adapted
