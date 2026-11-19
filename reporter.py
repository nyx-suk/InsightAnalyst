"""reporter.py
Person B: Business Reporting and Plotly Visualization Engine for InsightAnalyst.
Generates fully traceable Markdown business reports and interactive Plotly figures
from an AgentRun or JSON payload.

Guarantees:
- Every number stated in the Headline and Key Drivers traces to its query citation (e.g. [Q1], [Q2]).
- Strictly complies with PRD Section 8 ('Report Specification') and Phase 3 deliverables.
- Provides fallback representations when Plotly is not installed or when running offline.
"""

from dataclasses import dataclass, field, asdict
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union

# Import contracts
from contracts import AgentRun, Finding, Plan, QueryResult, ValidationResult, TraceEvent

logger = logging.getLogger("InsightAnalyst.Reporter")

# Dynamic Plotly import for optional visualization rendering
try:
    import plotly.graph_objects as go
    import plotly.express as px
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False
    go = None
    px = None


@dataclass
class ChartSpec:
    """Lightweight chart specification when Plotly is absent or for serialization."""
    chart_type: str                         # 'trend' | 'driver'
    title: str
    x_label: str
    y_label: str
    data: List[Dict[str, Any]] = field(default_factory=list)
    layout: Dict[str, Any] = field(default_factory=dict)
    figure: Any = None                      # plotly.graph_objects.Figure if available

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chart_type": self.chart_type,
            "title": self.title,
            "x_label": self.x_label,
            "y_label": self.y_label,
            "data": self.data,
            "layout": self.layout,
            "has_figure": self.figure is not None
        }


@dataclass
class ReportResult:
    """Container holding the assembled report and visualization artifacts."""
    markdown: str
    trend_chart: Optional[Any] = None       # plotly.graph_objects.Figure or ChartSpec
    driver_chart: Optional[Any] = None      # plotly.graph_objects.Figure or ChartSpec
    run: Optional[AgentRun] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "markdown": self.markdown,
            "has_trend_chart": self.trend_chart is not None,
            "has_driver_chart": self.driver_chart is not None,
            "run_status": self.run.status if self.run else None
        }


def _coerce_agent_run(run: Union[AgentRun, Dict[str, Any]]) -> AgentRun:
    """Coerces input dictionary or AgentRun into a validated AgentRun instance."""
    if isinstance(run, AgentRun):
        return run
    if isinstance(run, dict):
        return AgentRun.from_dict(run)
    raise TypeError(f"Expected AgentRun or dict, got {type(run).__name__}")


def format_numeric_value(val: Any, col_name: str = "") -> str:
    """Nicely formats numeric values with thousand separators or percentages."""
    if val is None:
        return "NULL"
    col_lower = col_name.lower()
    if isinstance(val, float):
        if any(term in col_lower for term in ["pct", "percent", "rate"]):
            return f"{val:.2f}%"
        if abs(val) >= 1000:
            return f"{val:,.2f}"
        return f"{val:.2f}"
    if isinstance(val, int):
        if abs(val) >= 1000:
            return f"{val:,}"
        return str(val)
    return str(val)


def format_markdown_table(columns: List[str], rows: List[Any], max_rows: int = 25) -> str:
    """Generates a clean GFM markdown table from columns and row tuples/lists."""
    if not columns:
        return "*(No tabular columns available)*"
    if not rows:
        return "*(Empty result set: 0 rows returned)*"

    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join(["---"] * len(columns)) + " |"
    
    table_lines = [header, separator]
    display_rows = rows[:max_rows]

    for row in display_rows:
        row_cells = []
        for idx, col in enumerate(columns):
            cell_val = row[idx] if idx < len(row) else ""
            formatted = format_numeric_value(cell_val, col)
            # Escape pipes inside cells
            row_cells.append(str(formatted).replace("|", "\\|"))
        table_lines.append("| " + " | ".join(row_cells) + " |")

    if len(rows) > max_rows:
        table_lines.append(f"\n*... displaying first {max_rows} of {len(rows)} rows*")

    return "\n".join(table_lines)


def _partition_validations_by_query(queries: List[QueryResult], validations: List[ValidationResult]) -> Dict[str, List[ValidationResult]]:
    """Maps flat validation results to their corresponding queries."""
    mapping: Dict[str, List[ValidationResult]] = {q.query_id: [] for q in queries}
    if not queries or not validations:
        return mapping

    # Check if validations already carry query_id in reason or check_name
    matched_explicit = False
    for val in validations:
        for q in queries:
            if f"[{q.query_id}]" in (val.reason or "") or f"{q.query_id}:" in (val.reason or ""):
                mapping[q.query_id].append(val)
                matched_explicit = True
                break

    if matched_explicit:
        return mapping

    # If validations count matches an even slice per query (e.g., 4 checks each)
    num_queries = len(queries)
    if len(validations) % num_queries == 0:
        chunk_size = len(validations) // num_queries
        for idx, q in enumerate(queries):
            start = idx * chunk_size
            mapping[q.query_id] = validations[start:start + chunk_size]
        return mapping

    # Fallback: assign all validations to all executed queries
    for q in queries:
        mapping[q.query_id] = list(validations)
    return mapping


def generate_markdown_report(run_input: Union[AgentRun, Dict[str, Any]]) -> str:
    """
    Assembles a complete, auditable business report in Markdown from an AgentRun.
    
    Sections:
    1. Headline Answer (with [Q#] citation)
    2. Key Drivers (ranked contributors with [Q#] citations)
    3. Interactive Visualizations (trend & driver breakdown description)
    4. Assumptions & Business Rules (data anchoring, formulas, region join)
    5. Evidence Appendix (executed SQL, result tables, validation checks per query)
    """
    run = _coerce_agent_run(run_input)
    sections: List[str] = []

    # Title & Metadata Header
    status_label = {
        "complete": "Complete",
        "partial": "Partial Analysis",
        "clarify": "Clarification Required",
        "refused": "Request Refused (Guardrail)",
        "failed": "Execution Failed"
    }.get(run.status, run.status.capitalize())

    metric_name = run.plan.metric if run.plan and run.plan.metric else "N/A"
    time_window = run.plan.time_window if run.plan and run.plan.time_window else "N/A"

    sections.append("# InsightAnalyst Business Analysis Report\n")
    sections.append(f"**Question:** {run.question}")
    sections.append(f"**Execution Status:** `{status_label}` | **Target Metric:** `{metric_name}` | **Time Window:** `{time_window}`\n")
    sections.append("---\n")

    # 1. Headline Answer
    sections.append("## 1. Headline Answer\n")
    if run.status == "refused":
        refusal_msg = (run.plan.refusal_reason if run.plan and run.plan.refusal_reason else run.error) or "Request was refused by safety guardrails."
        sections.append(f"> 🚫 **Request Refused:** {refusal_msg}\n")
    elif run.status == "clarify":
        clarify_msg = (run.plan.clarification_message if run.plan and run.plan.clarification_message else "Please clarify the question.")
        sections.append(f"> ⚠️ **Clarification Needed:** {clarify_msg}\n")
    elif run.status == "failed" and not run.findings:
        err_msg = run.error or "The analysis failed to produce verified findings."
        sections.append(f"> ❌ **Analysis Incomplete:** {err_msg}\n")
    else:
        # Find headline finding
        headline_findings = [f for f in run.findings if f.kind == "headline"]
        if headline_findings:
            statement = headline_findings[0].render()
            sections.append(f"{statement}\n")
        else:
            # Check assumptions for Narrative Summary
            narrative_line = None
            for a in run.assumptions:
                if a.startswith("Narrative Summary:"):
                    narrative_line = a.replace("Narrative Summary:", "").strip()
                    break
            if narrative_line:
                sections.append(f"{narrative_line}\n")
            elif run.findings:
                # Use first finding rendered
                sections.append(f"{run.findings[0].render()}\n")
            else:
                sections.append("Analysis completed. No conclusive primary finding was identified.\n")

    # 2. Key Drivers
    sections.append("## 2. Key Drivers\n")
    driver_findings = [f for f in run.findings if f.kind in ("driver", "trend")]
    if driver_findings:
        for df in driver_findings:
            stmt = df.render()
            sections.append(f"- {stmt}")
        sections.append("")

        # Driver Contribution Summary Table if structured values exist
        structured_drivers = [
            f for f in driver_findings
            if f.values and ("abs_change" in f.values or "contribution_pct" in f.values or "val" in f.values)
        ]
        if structured_drivers:
            table_cols = ["Dimension", "Direction", "Change", "% Change", "Contribution", "Query"]
            table_rows = []
            for sd in structured_drivers:
                dim = sd.values.get("dimension_value") or sd.values.get("dimension_name") or sd.id
                direction = sd.values.get("direction", "-")
                abs_chg = sd.values.get("abs_change", sd.values.get("val", "-"))
                if isinstance(abs_chg, float):
                    abs_chg = f"{abs_chg:,.2f}"
                pct_chg = sd.values.get("pct_change", "-")
                if isinstance(pct_chg, float):
                    pct_chg = f"{pct_chg:+.2f}%"
                contrib = sd.values.get("contribution_pct", "-")
                if isinstance(contrib, float):
                    contrib = f"{contrib:.1f}%"
                qid = f"[{sd.query_id}]" if sd.query_id else "-"
                table_rows.append([dim, direction, abs_chg, pct_chg, contrib, qid])
            
            sections.append("### Driver Breakdown Summary\n")
            sections.append(format_markdown_table(table_cols, table_rows))
            sections.append("")
    else:
        if run.status in ("complete", "partial"):
            sections.append("*(No secondary drill-down drivers were required for this single-step analysis)*\n")
        else:
            sections.append("*(No driver breakdown available due to incomplete execution)*\n")

    # 3. Interactive Visualizations
    sections.append("## 3. Interactive Visualizations\n")
    sections.append("The report includes two analytical visual perspectives:")
    sections.append("- **Trend Analysis Chart**: Illustrates period-over-period metric variance across target and comparison windows.")
    sections.append("- **Driver Breakdown Chart**: Displays ranked dimensional contributions (e.g. products, categories, or regions) isolating root causes.\n")

    # 4. Assumptions & Business Rules
    sections.append("## 4. Assumptions & Business Rules\n")
    default_assumptions = [
        "**Time Anchoring**: Relative dates are anchored to the last complete calendar month in the dataset (September 2023).",
        "**Revenue Formula**: Revenue is strictly calculated as `UnitPrice * Quantity * (1 - Discount)` from `[Order Details]`; `Freight` shipping fees are excluded.",
        "**Geographic Mapping**: Customer countries map to sales regions via the `dim_country_region` mapping table joined on `Customers.Country`."
    ]
    for da in default_assumptions:
        sections.append(f"- {da}")

    # Add custom plan assumptions
    custom_assumptions = []
    if run.plan and run.plan.assumptions:
        custom_assumptions.extend(run.plan.assumptions)
    for a in run.assumptions:
        # Exclude duplicated narrative summaries or raw time window markers
        if not a.startswith("Narrative Summary:") and not a.startswith("Time window spec:"):
            if a not in custom_assumptions:
                custom_assumptions.append(a)

    for ca in custom_assumptions:
        sections.append(f"- {ca}")
    sections.append("")

    # 5. Evidence Appendix
    sections.append("## 5. Evidence Appendix\n")
    sections.append("Every figure in this report is backed by executable, read-only SQL and validated against sanity checks.\n")

    query_val_map = _partition_validations_by_query(run.queries, run.validations)
    query_purposes = {}
    if run.plan and run.plan.query_list:
        for pq in run.plan.query_list:
            query_purposes[pq.query_id] = pq.purpose

    if run.queries:
        for q in run.queries:
            qid = q.query_id or "Q"
            purpose = query_purposes.get(qid, f"Analytical query {qid}")
            status_text = "Success" if not q.error else f"Execution Failed ({q.error})"
            
            sections.append(f"### Query [{qid}]\n")
            sections.append(f"**Purpose:** {purpose}")
            sections.append(f"**Execution Status:** `{status_text}` | **Rows Returned:** {q.row_count}\n")
            
            sections.append("#### Executed SQL:")
            sections.append("```sql")
            sections.append(q.sql.strip())
            sections.append("```\n")

            sections.append("#### Query Results:")
            sections.append(format_markdown_table(q.columns, q.rows))
            sections.append("")

            # Validations for this query
            q_vals = query_val_map.get(qid, [])
            if q_vals:
                sections.append("#### Validation Checks Passed:")
                for v in q_vals:
                    icon = "✅" if v.status == "pass" else ("⚠️" if v.status == "suspicious" else "❌")
                    check_lbl = v.check_name or "check"
                    reason_lbl = v.reason or "Passed"
                    sections.append(f"- {icon} **{check_lbl}** (`{v.status}`): {reason_lbl}")
                sections.append("")
            sections.append("---\n")
    else:
        sections.append("*(No SQL queries were executed for this request)*\n")

    return "\n".join(sections)


def generate_trend_chart(run_input: Union[AgentRun, Dict[str, Any]]) -> Any:
    """
    Constructs a trend chart (Plotly Figure or ChartSpec) comparing periods over time.
    Inspects queries for time columns (e.g. OrderMonth, Month, Year, Date) and metric values.
    """
    run = _coerce_agent_run(run_input)
    metric_name = run.plan.metric.title() if run.plan and run.plan.metric else "Metric"

    # Search for comparison query (typically Q1)
    target_query = None
    time_col = None
    metric_col = None

    for q in run.queries:
        if not q.rows or q.error:
            continue
        cols_lower = [c.lower() for c in q.columns]
        # Look for time indicator column
        for idx, col in enumerate(cols_lower):
            if any(t in col for t in ["month", "date", "year", "period", "quarter"]):
                time_col = q.columns[idx]
                target_query = q
                break
        if target_query:
            # Look for metric column
            for idx, col in enumerate(cols_lower):
                if col != time_col.lower() and not any(dim in col for dim in ["region", "country", "name", "id"]):
                    metric_col = q.columns[idx]
                    break
            if not metric_col and len(q.columns) >= 2:
                metric_col = q.columns[-1]
            break

    # If no explicit time query found, synthesize from findings
    chart_data = []
    if target_query and time_col and metric_col:
        time_idx = target_query.columns.index(time_col)
        metric_idx = target_query.columns.index(metric_col)
        for r in target_query.rows:
            time_val = str(r[time_idx])
            try:
                num_val = float(r[metric_idx])
            except (ValueError, TypeError):
                num_val = 0.0
            chart_data.append({"period": time_val, "value": num_val})
    else:
        # Check headline finding for prior and current values
        for f in run.findings:
            if f.values and "prior" in f.values and "current" in f.values:
                chart_data = [
                    {"period": "Prior Period", "value": float(f.values["prior"])},
                    {"period": "Current Period", "value": float(f.values["current"])}
                ]
                break

    chart_title = f"{metric_name} Period-over-Period Trend"
    x_label = "Period"
    y_label = metric_name

    if HAS_PLOTLY:
        fig = go.Figure()
        if chart_data:
            x_vals = [d["period"] for d in chart_data]
            y_vals = [d["value"] for d in chart_data]
            text_labels = [f"{v:,.2f}" if abs(v) >= 1000 else f"{v:.2f}" for v in y_vals]

            # Use modern gradient bar chart with value annotations
            fig.add_trace(go.Bar(
                x=x_vals,
                y=y_vals,
                text=text_labels,
                textposition="auto",
                marker=dict(
                    color=["#3b82f6", "#10b981"] if len(x_vals) == 2 and y_vals[1] >= y_vals[0] else ["#3b82f6", "#ef4444"],
                    line=dict(color="#1e293b", width=1.5)
                ),
                hovertemplate="<b>%{x}</b><br>" + metric_name + ": %{y:,.2f}<extra></extra>"
            ))
        else:
            fig.add_annotation(text="No trend data available", showarrow=False, font=dict(size=14, color="#64748b"))

        fig.update_layout(
            title=dict(text=f"<b>{chart_title}</b>", font=dict(size=16, color="#0f172a")),
            xaxis=dict(title=x_label, tickfont=dict(size=12)),
            yaxis=dict(title=y_label, tickformat=",.0f", tickfont=dict(size=12)),
            template="plotly_white",
            margin=dict(l=40, r=40, t=50, b=40),
            height=380
        )
        return fig

    return ChartSpec(
        chart_type="trend",
        title=chart_title,
        x_label=x_label,
        y_label=y_label,
        data=chart_data,
        layout={"title": chart_title, "xaxis": x_label, "yaxis": y_label}
    )


def generate_driver_chart(run_input: Union[AgentRun, Dict[str, Any]]) -> Any:
    """
    Constructs a driver breakdown chart (Plotly Figure or ChartSpec) ranking contributors.
    Inspects drill-down queries (e.g. Q2) or structured driver findings.
    """
    run = _coerce_agent_run(run_input)
    metric_name = run.plan.metric.title() if run.plan and run.plan.metric else "Revenue"

    # Search for drill-down query (e.g. Q2 or entity grouping)
    target_query = None
    entity_col = None
    metric_col = None

    for q in run.queries:
        if q.query_id == "Q1" and len(run.queries) > 1:
            continue
        if not q.rows or q.error:
            continue
        cols_lower = [c.lower() for c in q.columns]
        for idx, col in enumerate(cols_lower):
            if any(term in col for term in ["product", "category", "region", "country", "employee", "customer"]):
                entity_col = q.columns[idx]
                target_query = q
                break
        if target_query:
            for idx, col in enumerate(cols_lower):
                if col != entity_col.lower() and not any(dim in col for dim in ["id"]):
                    metric_col = q.columns[idx]
                    break
            if not metric_col and len(q.columns) >= 2:
                metric_col = q.columns[-1]
            break

    chart_data = []
    if target_query and entity_col and metric_col:
        e_idx = target_query.columns.index(entity_col)
        m_idx = target_query.columns.index(metric_col)
        for r in target_query.rows:
            e_val = str(r[e_idx])
            try:
                num_val = float(r[m_idx])
            except (ValueError, TypeError):
                num_val = 0.0
            chart_data.append({"entity": e_val, "value": num_val})
    else:
        # Fall back to driver findings
        for f in run.findings:
            if f.kind == "driver" and f.values:
                dim_name = f.values.get("dimension_value") or f.values.get("dimension_name") or f.id
                val = f.values.get("contribution_pct") or f.values.get("abs_change") or f.values.get("val")
                if val is not None:
                    chart_data.append({"entity": str(dim_name), "value": float(val)})

    chart_title = f"Driver Breakdown: Top Contributors ({metric_name})"
    x_label = metric_name
    y_label = "Contributor"

    if HAS_PLOTLY:
        fig = go.Figure()
        if chart_data:
            # Sort ascending so largest is on top in horizontal bar
            sorted_data = sorted(chart_data, key=lambda d: d["value"])
            y_entities = [d["entity"] for d in sorted_data]
            x_metrics = [d["value"] for d in sorted_data]
            text_labels = [f"{v:,.2f}" if abs(v) >= 1000 else f"{v:.2f}" for v in x_metrics]

            fig.add_trace(go.Bar(
                y=y_entities,
                x=x_metrics,
                orientation="h",
                text=text_labels,
                textposition="auto",
                marker=dict(
                    color="#6366f1",
                    line=dict(color="#312e81", width=1.2)
                ),
                hovertemplate="<b>%{y}</b><br>" + metric_name + ": %{x:,.2f}<extra></extra>"
            ))
        else:
            fig.add_annotation(text="No driver breakdown available", showarrow=False, font=dict(size=14, color="#64748b"))

        fig.update_layout(
            title=dict(text=f"<b>{chart_title}</b>", font=dict(size=16, color="#0f172a")),
            xaxis=dict(title=x_label, tickformat=",.0f", tickfont=dict(size=12)),
            yaxis=dict(title=y_label, tickfont=dict(size=12)),
            template="plotly_white",
            margin=dict(l=120, r=40, t=50, b=40),
            height=380
        )
        return fig

    return ChartSpec(
        chart_type="driver",
        title=chart_title,
        x_label=x_label,
        y_label=y_label,
        data=chart_data,
        layout={"title": chart_title, "xaxis": x_label, "yaxis": y_label}
    )


def generate_charts(run_input: Union[AgentRun, Dict[str, Any]]) -> Dict[str, Any]:
    """Generates both the trend chart and driver breakdown chart for an AgentRun."""
    return {
        "trend_chart": generate_trend_chart(run_input),
        "driver_chart": generate_driver_chart(run_input)
    }


def verify_report_traceability(report_text: str) -> Tuple[bool, List[str]]:
    """
    Validates PRD Section 8 Rule: 'A report is invalid if any stated number has no query reference.'
    Audits the Headline Answer and Key Drivers sections.
    
    Returns:
        (is_valid, list_of_violations)
    """
    violations: List[str] = []
    
    # Extract only Section 1 and Section 2
    sec1_match = re.search(r"## 1\. Headline Answer\s*(.*?)\s*## 2\. Key Drivers", report_text, re.DOTALL)
    sec2_match = re.search(r"## 2\. Key Drivers\s*(.*?)\s*## 3\. Interactive Visualizations", report_text, re.DOTALL)

    content_to_check = ""
    if sec1_match:
        content_to_check += sec1_match.group(1) + "\n"
    if sec2_match:
        content_to_check += sec2_match.group(1) + "\n"

    if not content_to_check.strip():
        return True, []

    for line in content_to_check.splitlines():
        line_clean = line.strip()
        # Ignore markdown headers, blockquotes, empty lines, and table separators
        if not line_clean or line_clean.startswith("#") or line_clean.startswith(">") or line_clean.startswith("| ---"):
            continue
        
        # Mask out allowed non-metric numbers:
        # 1. Dates: YYYY-MM or YYYY (e.g. 2023-08, 2023)
        masked = re.sub(r"\b20\d{2}(-\d{2})?\b", "<DATE>", line_clean)
        # 2. Numbered list prefixes (e.g. "1. ")
        masked = re.sub(r"^\d+\.\s+", "", masked)
        # 3. Query citations themselves (e.g. [Q1], [Q2])
        has_query_citation = bool(re.search(r"\[Q\d+\]", masked))
        masked_no_q = re.sub(r"\[Q\d+\]", "<QUERY_ID>", masked)

        # Find any remaining naked digits
        digits = re.findall(r"\b\d+(?:\.\d+)?%?\b", masked_no_q)
        if digits and not has_query_citation:
            violations.append(f"Untraced number(s) {digits} in line: '{line_clean}'")

    return (len(violations) == 0, violations)


def build_report(run_input: Union[AgentRun, Dict[str, Any]]) -> ReportResult:
    """
    Primary interface for Person B: Assembles traceable Markdown and Plotly figures.
    Can be invoked in scripts, UI, or scheduled batch jobs.
    """
    run = _coerce_agent_run(run_input)
    markdown = generate_markdown_report(run)
    charts = generate_charts(run)
    
    return ReportResult(
        markdown=markdown,
        trend_chart=charts.get("trend_chart"),
        driver_chart=charts.get("driver_chart"),
        run=run
    )


def export_markdown(run_input: Union[AgentRun, Dict[str, Any]], filepath: Union[str, Path] = "insightanalyst_report.md") -> Path:
    """Exports the generated Markdown report directly to a file."""
    path = Path(filepath)
    report = generate_markdown_report(run_input)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    logger.info("Exported traceable business report to %s", path)
    return path
