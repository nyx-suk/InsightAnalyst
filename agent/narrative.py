"""agent/narrative.py
Zero-hallucination business narrative generator for InsightAnalyst (Prompt 3.5).
Prompts the LLM to write explanation text using ONLY placeholders ({FindingId.field}),
strictly checks for naked digits via regex, regenerates once if digits are found,
and falls back to deterministic code-built templates if needed.
All placeholders are filled by pure Python code.
"""

from dataclasses import dataclass, field, asdict
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Set
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import Finding
import agent.llm as default_llm

logger = logging.getLogger("InsightAnalyst.Narrative")

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "narrative.txt"


@dataclass
class NarrativeResult:
    """The result of narrative generation containing template and code-filled text."""
    raw_template: str                      # Text with placeholders e.g. "{F1.pct_change}"
    filled_text: str                       # Final narrative with placeholders replaced by code
    used_placeholders: List[str] = field(default_factory=list)
    fallback_used: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def has_naked_digits(text: str) -> bool:
    """
    Checks if any numeric digits (0-9) exist outside of placeholder braces '{...}'.
    Digits inside placeholders (such as {F1.value} or {driver_1.change}) are valid.
    Any digit outside curly braces is a violation of the zero-hallucination rule.
    """
    if not text:
        return False
    # Strip all placeholder tags {...}
    stripped = re.sub(r"\{[^{}]*\}", "", text)
    # Search for any remaining digits
    return bool(re.search(r"\d", stripped))


def _format_val(key: str, val: Any) -> str:
    """Formats numeric values nicely based on field name semantics."""
    if val is None:
        return ""
    if isinstance(val, float):
        if any(term in key.lower() for term in ["pct", "percent", "rate"]):
            return f"{val:.1f}%"
        if abs(val) >= 1000:
            return f"{val:,.2f}"
        return f"{val:.2f}"
    if isinstance(val, int):
        if abs(val) >= 1000:
            return f"{val:,}"
        return str(val)
    return str(val)


def build_placeholder_map(findings: List[Finding]) -> Dict[str, str]:
    """
    Constructs a lookup dictionary mapping placeholder names to formatted strings.
    Supports:
      - {fid.field_name} (e.g. {F_headline.total_abs_change}, {driver_1.pct_change})
      - {F{i}.field_name} (index-based aliases e.g. {F1.val}, {F2.direction})
      - {query_id.val} (e.g. {Q1.value}, {Q1.val})
    """
    lookup: Dict[str, str] = {}

    for idx, f in enumerate(findings, start=1):
        fid = f.id or f"F{idx}"
        alias_id = f"F{idx}"

        # Populate from values dict
        if f.values:
            for k, v in f.values.items():
                formatted = _format_val(k, v)
                lookup[f"{fid}.{k}"] = formatted
                lookup[f"{alias_id}.{k}"] = formatted
                if f.query_id:
                    lookup[f"{f.query_id}.{k}"] = formatted

        # Standard attributes
        if f.query_id:
            lookup[f"{fid}.query_id"] = f.query_id
            lookup[f"{alias_id}.query_id"] = f.query_id
            lookup[f"{f.query_id}.query_id"] = f.query_id
            lookup[f"{f.query_id}.val"] = _format_val("val", f.value)
            lookup[f"{f.query_id}.value"] = _format_val("value", f.value)

        if f.statement:
            lookup[f"{fid}.statement"] = f.statement
            lookup[f"{alias_id}.statement"] = f.statement

        if f.value is not None:
            formatted_val = _format_val("value", f.value)
            lookup[f"{fid}.value"] = formatted_val
            lookup[f"{fid}.val"] = formatted_val
            lookup[f"{alias_id}.value"] = formatted_val
            lookup[f"{alias_id}.val"] = formatted_val

    return lookup


def fill_placeholders(template: str, findings: List[Finding]) -> Tuple[str, List[str]]:
    """
    Replaces all {Placeholder} patterns in template using finding values.
    Returns:
        (filled_text, list_of_used_placeholders)
    """
    lookup = build_placeholder_map(findings)
    used: Set[str] = set()

    def replace_match(match: re.Match) -> str:
        key = match.group(1).strip()
        used.add(key)
        if key in lookup:
            return lookup[key]
        # Try case-insensitive lookup
        for lk, lv in lookup.items():
            if lk.lower() == key.lower():
                return lv
        # Return fallback placeholder label if not found
        return f"[{key}]"

    filled = re.sub(r"\{([^{}]+)\}", replace_match, template)
    return filled, sorted(list(used))


def build_fallback_narrative(findings: List[Finding], question: str) -> NarrativeResult:
    """
    Deterministic pure-Python fallback when LLM narrative contains naked digits.
    Builds narrative directly from findings' verified rendered statements.
    """
    if not findings:
        msg = f"Analysis complete for question: '{question}'. No conclusive findings were generated."
        return NarrativeResult(raw_template=msg, filled_text=msg, used_placeholders=[], fallback_used=True)

    templates = []
    filled_sentences = []

    for f in findings:
        tmpl = f.template or f.statement
        if tmpl:
            templates.append(tmpl)
        rendered = f.render()
        if rendered:
            filled_sentences.append(rendered)

    raw_template = " ".join(templates)
    filled_text = " ".join(filled_sentences)

    return NarrativeResult(
        raw_template=raw_template,
        filled_text=filled_text,
        used_placeholders=[],
        fallback_used=True
    )


def generate_narrative(
    findings: List[Finding],
    question: str,
    llm: Any = None
) -> NarrativeResult:
    """
    Generates a natural-language narrative for the findings.
    
    1. Prompts LLM using prompts/narrative.txt with strict placeholder requirement.
    2. Runs regex check for naked digits.
    3. If naked digits found, regenerates once with stern reminder.
    4. If digits persist, falls back to deterministic code-built summary.
    5. Fills all placeholders safely in Python.
    """
    if not findings:
        return build_fallback_narrative(findings, question)

    # Format findings context for prompt
    context_lines = []
    for idx, f in enumerate(findings, start=1):
        fid = f.id or f"F{idx}"
        val_desc = ", ".join(f"{k}='{v}'" for k, v in (f.values or {}).items())
        context_lines.append(f"- Finding ID: '{fid}' (Query: [{f.query_id}]): {val_desc}")

    findings_context = "\n".join(context_lines)

    if PROMPT_PATH.exists():
        template = PROMPT_PATH.read_text(encoding="utf-8")
    else:
        template = (
            "Write a 2-sentence business summary using ONLY placeholders like {FindingId.field}.\n"
            "Zero raw digits allowed!\n\nFindings:\n{findings_context}\n\nQuestion:\n{question}"
        )

    user_prompt = (
        template
        .replace("{findings_context}", findings_context)
        .replace("{question}", question)
    )

    active_llm = llm or default_llm

    def call_llm(p: str) -> str:
        if hasattr(active_llm, "generate"):
            return active_llm.generate(p, temperature=0.0)
        if callable(active_llm):
            return active_llm(p)
        return ""

    # Attempt 1
    raw_reply = call_llm(user_prompt).strip()

    # Digit validation check
    if has_naked_digits(raw_reply):
        logger.warning("Naked digits detected in LLM narrative attempt 1. Regenerating once with reminder...")
        reminder_prompt = (
            user_prompt +
            "\n\nERROR IN PREVIOUS ATTEMPT:\n"
            "Your previous text contained raw numeric digits, violating the Zero-Hallucination rule:\n"
            f"\"{raw_reply}\"\n"
            "You MUST use ONLY placeholders like {FindingId.field_name} for every number or percentage. "
            "Never type numbers or percentages directly. Rewrite the narrative now:"
        )
        # Attempt 2
        raw_reply = call_llm(reminder_prompt).strip()

        # If digits still persist after 2 attempts, fall back to pure code
        if has_naked_digits(raw_reply):
            logger.warning("Naked digits persisted in LLM narrative attempt 2. Falling back to deterministic code template.")
            return build_fallback_narrative(findings, question)

    # Clean any unwanted markdown quotes
    cleaned = raw_reply.strip().strip('"').strip("'")
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = re.sub(r"^```[a-z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()

    # Code-based placeholder replacement
    filled_text, used_placeholders = fill_placeholders(cleaned, findings)

    return NarrativeResult(
        raw_template=cleaned,
        filled_text=filled_text,
        used_placeholders=used_placeholders,
        fallback_used=False
    )
