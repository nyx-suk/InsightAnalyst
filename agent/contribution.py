"""agent/contribution.py
Pure Python contribution and variance analysis for InsightAnalyst.
Calculates how much each dimension slice (region, category, product, etc.)
contributed to the total metric change between two periods.

No LLM calls - 100% deterministic, traceable math.
"""

from dataclasses import dataclass, field, asdict
import re
from typing import Any, Dict, List, Optional, Tuple, Sequence
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import QueryResult, Finding


@dataclass
class ContributionItem:
    """Change and contribution metrics for a single dimension slice."""
    dimension_value: str
    prior_value: float
    current_value: float
    absolute_change: float                   # current - prior
    pct_change: Optional[float]              # (current - prior) / prior * 100 (None if prior == 0)
    contribution_pct: Optional[float]        # absolute_change / total_change * 100 (None if total_change == 0)
    rank: int = 1                            # Rank among siblings (1 = top driver)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ContributionAnalysis:
    """Full contribution and variance breakdown over a dimension."""
    dimension_name: str
    total_prior: float
    total_current: float
    total_change: float                      # total_current - total_prior
    total_pct_change: Optional[float]        # total_change / total_prior * 100
    items: List[ContributionItem]            # Ranked by contribution to change
    is_reconciled: bool                      # sum(items.absolute_change) == total_change within tolerance
    reconciliation_error: float              # abs(sum - total_change)
    tolerance: float = 0.01

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def top_driver(self) -> Optional[ContributionItem]:
        """Returns the #1 contributing driver of the change."""
        return self.items[0] if self.items else None

    @property
    def top_drivers(self) -> List[ContributionItem]:
        """Returns top drivers that move in the same direction as the total change."""
        if self.total_change == 0:
            return self.items
        return [
            item for item in self.items
            if (item.absolute_change > 0) == (self.total_change > 0)
        ]


def _safe_float(val: Any) -> float:
    """Safely convert value to float, defaulting to 0.0 on None or error."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def analyze_contribution(
    result: QueryResult,
    dimension_col: Optional[str] = None,
    prior_col: Optional[str] = None,
    current_col: Optional[str] = None,
    tolerance: float = 0.01,
    expected_total_change: Optional[float] = None
) -> ContributionAnalysis:
    """
    Analyzes a QueryResult comparing two periods grouped by one dimension.
    
    Supports:
      1. Wide format: [dimension, prior_metric, current_metric]
      2. Delta format: [dimension, prior_metric, current_metric, change]
      3. Long format: [dimension, period_col, metric_col] (with exactly 2 periods)
    
    Returns:
      ContributionAnalysis with items ranked by impact and reconciliation status.
    """
    if not result or not result.rows:
        return ContributionAnalysis(
            dimension_name=dimension_col or "dimension",
            total_prior=0.0,
            total_current=0.0,
            total_change=0.0,
            total_pct_change=0.0,
            items=[],
            is_reconciled=True,
            reconciliation_error=0.0,
            tolerance=tolerance
        )

    cols = [c.lower() for c in (result.columns or [])]
    orig_cols = result.columns or []
    rows = result.rows

    # Case A1: Long format with dimension breakdown (3+ columns where one is period/date/month)
    period_idx = -1
    time_terms = ["month", "ordermonth", "date", "orderdate", "period", "year", "quarter"]
    for i, c in enumerate(cols):
        if any(term in c for term in time_terms):
            period_idx = i
            break

    # Case A2: Aggregate two-period summary without dimension breakdown (e.g. [OrderMonth, Revenue])
    if period_idx != -1 and len(cols) == 2:
        unique_periods = sorted(list({str(r[period_idx]) for r in rows}))
        if len(unique_periods) == 2:
            val_idx = 1 if period_idx == 0 else 0
            prior_p, curr_p = unique_periods[0], unique_periods[1]
            p_val = _safe_float([r[val_idx] for r in rows if str(r[period_idx]) == prior_p][0])
            c_val = _safe_float([r[val_idx] for r in rows if str(r[period_idx]) == curr_p][0])
            tot_chg = c_val - p_val
            tot_pct = (tot_chg / p_val * 100) if p_val != 0 else None
            return ContributionAnalysis(
                dimension_name=orig_cols[period_idx],
                total_prior=p_val,
                total_current=c_val,
                total_change=tot_chg,
                total_pct_change=tot_pct,
                items=[],
                is_reconciled=True,
                reconciliation_error=0.0,
                tolerance=tolerance
            )

    if period_idx != -1 and len(cols) >= 3:
        unique_periods = sorted(list({str(r[period_idx]) for r in rows}))
        if len(unique_periods) == 2:
            return _analyze_long_format(
                result=result,
                period_idx=period_idx,
                unique_periods=unique_periods,
                tolerance=tolerance,
                expected_total_change=expected_total_change
            )

    # Case B: Wide format [dimension, prior, current] or specified columns
    dim_idx = 0
    p_idx = 1
    c_idx = 2

    if dimension_col and dimension_col in orig_cols:
        dim_idx = orig_cols.index(dimension_col)
    else:
        # Avoid selecting time/date or id columns as the breakdown dimension
        id_terms = ["id", "orderid", "customerid"]
        for idx, c in enumerate(cols):
            if not any(term in c for term in time_terms) and not any(term in c for term in id_terms):
                dim_idx = idx
                break

    if prior_col and prior_col in orig_cols:
        p_idx = orig_cols.index(prior_col)
    if current_col and current_col in orig_cols:
        c_idx = orig_cols.index(current_col)

    dim_name = orig_cols[dim_idx] if dim_idx < len(orig_cols) else "dimension"

    # Extract raw items, skipping synthetic 'total' rows and date strings
    raw_data = []
    found_total_prior = None
    found_total_current = None
    date_pattern = re.compile(r"^\d{4}-\d{2}$|^\d{4}-\d{2}-\d{2}$")

    for r in rows:
        dim_val = str(r[dim_idx]).strip() if dim_idx < len(r) and r[dim_idx] is not None else "Unknown"
        # If the query itself included a 'Total' roll-up row, capture it separately
        if dim_val.lower() in ["total", "all", "overall", "summary"]:
            found_total_prior = _safe_float(r[p_idx]) if p_idx < len(r) else None
            found_total_current = _safe_float(r[c_idx]) if c_idx < len(r) else None
            continue
        # If dimension value is a date/month (time period), do not treat as an entity item
        if date_pattern.match(dim_val):
            continue
        p_val = _safe_float(r[p_idx]) if p_idx < len(r) else 0.0
        c_val = _safe_float(r[c_idx]) if c_idx < len(r) else 0.0
        raw_data.append((dim_val, p_val, c_val))

    # Compute totals
    total_prior = found_total_prior if found_total_prior is not None else sum(p for _, p, _ in raw_data)
    total_current = found_total_current if found_total_current is not None else sum(c for _, _, c in raw_data)
    
    computed_total_change = total_current - total_prior
    total_change = expected_total_change if expected_total_change is not None else computed_total_change
    total_pct_change = (total_change / total_prior * 100) if total_prior != 0 else None

    # Calculate item changes and contributions
    items: List[ContributionItem] = []
    for dim_val, p_val, c_val in raw_data:
        abs_change = c_val - p_val
        pct_change = (abs_change / p_val * 100) if p_val != 0 else None
        contrib_pct = (abs_change / total_change * 100) if total_change != 0 else 0.0
        
        items.append(
            ContributionItem(
                dimension_value=dim_val,
                prior_value=round(p_val, 2),
                current_value=round(c_val, 2),
                absolute_change=round(abs_change, 2),
                pct_change=round(pct_change, 2) if pct_change is not None else None,
                contribution_pct=round(contrib_pct, 2) if contrib_pct is not None else None
            )
        )

    # Rank items:
    # Top driver is the one with the largest contribution to the change.
    # When total_change != 0, rank by contribution in direction of change, or absolute magnitude.
    items.sort(
        key=lambda item: (
            # Items moving in direction of total change first
            (item.absolute_change > 0) == (total_change > 0) if total_change != 0 else True,
            # Largest absolute change next
            abs(item.absolute_change)
        ),
        reverse=True
    )

    for rank_idx, item in enumerate(items, start=1):
        item.rank = rank_idx

    # Reconciliation check: do item changes sum to total change?
    sum_item_changes = sum(item.absolute_change for item in items)
    reconciliation_error = abs(sum_item_changes - total_change)
    is_reconciled = reconciliation_error <= tolerance

    return ContributionAnalysis(
        dimension_name=dim_name,
        total_prior=round(total_prior, 2),
        total_current=round(total_current, 2),
        total_change=round(total_change, 2),
        total_pct_change=round(total_pct_change, 2) if total_pct_change is not None else None,
        items=items,
        is_reconciled=is_reconciled,
        reconciliation_error=round(reconciliation_error, 4),
        tolerance=tolerance
    )


def _analyze_long_format(
    result: QueryResult,
    period_idx: int,
    unique_periods: List[str],
    tolerance: float = 0.01,
    expected_total_change: Optional[float] = None
) -> ContributionAnalysis:
    """Handles 3-column long format: [dim, period, val] or [period, dim, val]."""
    cols = result.columns
    dim_idx = [i for i in range(len(cols)) if i != period_idx and i != 2][0] if len(cols) == 3 else 0
    val_idx = 2 if period_idx != 2 and dim_idx != 2 else 1

    prior_period, current_period = unique_periods[0], unique_periods[1]
    dim_name = cols[dim_idx]

    prior_map: Dict[str, float] = {}
    current_map: Dict[str, float] = {}

    for r in result.rows:
        dim_val = str(r[dim_idx])
        p_val = str(r[period_idx])
        val = _safe_float(r[val_idx])

        if p_val == prior_period:
            prior_map[dim_val] = val
        elif p_val == current_period:
            current_map[dim_val] = val

    all_dims = sorted(list(set(prior_map.keys()) | set(current_map.keys())))
    synthetic_rows = [
        (dim, prior_map.get(dim, 0.0), current_map.get(dim, 0.0))
        for dim in all_dims
    ]

    synthetic_result = QueryResult(
        query_id=result.query_id,
        sql=result.sql,
        columns=[dim_name, f"{prior_period}_val", f"{current_period}_val"],
        rows=synthetic_rows,
        row_count=len(synthetic_rows)
    )

    return analyze_contribution(
        synthetic_result,
        dimension_col=dim_name,
        prior_col=f"{prior_period}_val",
        current_col=f"{current_period}_val",
        tolerance=tolerance,
        expected_total_change=expected_total_change
    )


def compare_two_results(
    prior_result: QueryResult,
    current_result: QueryResult,
    dimension_col: str,
    metric_col: str,
    tolerance: float = 0.01
) -> ContributionAnalysis:
    """
    Computes contribution analysis from two separate QueryResults
    (e.g., Q1 for prior month and Q2 for current month).
    """
    prior_dim_idx = prior_result.columns.index(dimension_col) if dimension_col in prior_result.columns else 0
    prior_met_idx = prior_result.columns.index(metric_col) if metric_col in prior_result.columns else 1

    curr_dim_idx = current_result.columns.index(dimension_col) if dimension_col in current_result.columns else 0
    curr_met_idx = current_result.columns.index(metric_col) if metric_col in current_result.columns else 1

    prior_map = {
        str(r[prior_dim_idx]): _safe_float(r[prior_met_idx])
        for r in prior_result.rows
    }
    curr_map = {
        str(r[curr_dim_idx]): _safe_float(r[curr_met_idx])
        for r in current_result.rows
    }

    all_dims = sorted(list(set(prior_map.keys()) | set(curr_map.keys())))
    combined_rows = [
        (dim, prior_map.get(dim, 0.0), curr_map.get(dim, 0.0))
        for dim in all_dims
    ]

    combined_qr = QueryResult(
        query_id=f"{prior_result.query_id}+{current_result.query_id}",
        sql="",
        columns=[dimension_col, "prior_value", "current_value"],
        rows=combined_rows,
        row_count=len(combined_rows)
    )

    return analyze_contribution(
        combined_qr,
        dimension_col=dimension_col,
        prior_col="prior_value",
        current_col="current_value",
        tolerance=tolerance
    )


def build_driver_finding(
    analysis: ContributionAnalysis,
    query_id: str,
    metric_name: str = "revenue",
    item_index: int = 0
) -> Optional[Finding]:
    """
    Constructs a traceable Finding object adhering to Prompt 3.1:
    - Numbers appear ONLY in values dict.
    - Template contains placeholders filled by code via render().
    """
    if not analysis.items or item_index >= len(analysis.items):
        return None

    item = analysis.items[item_index]
    direction = "fell" if item.absolute_change < 0 else "rose"
    
    template = (
        "{dimension_value} {metric_name} {direction} by {abs_change} "
        "({pct_change}%), driving {contribution_pct}% of total change [{query_id}]"
    )

    values = {
        "dimension_value": item.dimension_value,
        "dimension_name": analysis.dimension_name,
        "metric_name": metric_name,
        "direction": direction,
        "abs_change": abs(item.absolute_change),
        "actual_change": item.absolute_change,
        "pct_change": item.pct_change if item.pct_change is not None else 0.0,
        "contribution_pct": item.contribution_pct if item.contribution_pct is not None else 0.0,
        "prior_value": item.prior_value,
        "current_value": item.current_value,
        "query_id": query_id
    }

    return Finding(
        id=f"driver_{item.rank}",
        kind="driver",
        template=template,
        values=values,
        query_id=query_id
    )
