from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def build_report(base_dir: Path) -> dict:
    scan_rows = _read_jsonl(base_dir / "scan_snapshots.jsonl")
    plan_rows = _read_jsonl(base_dir / "planned_orders.jsonl")
    signal_rows = _read_jsonl(base_dir / "paper_journal.jsonl")
    portfolio = {}
    portfolio_path = base_dir / "portfolio_state.json"
    if portfolio_path.exists():
        try:
            portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            portfolio = {}

    strategies = Counter()
    opportunity_counts = []
    skipped_counts = []
    eval_status = Counter()
    for row in scan_rows:
        strategies[row.get("strategy_id", "unknown")] += 1
        scan_result = row.get("scan_result", {})
        opportunity_counts.append(int(scan_result.get("opportunity_count", 0)))
        skipped_counts.append(int(scan_result.get("skipped_count", 0)))
        for ev in scan_result.get("evaluations", []):
            status = ev.get("status", "unknown")
            eval_status[status] += 1

    plan_accepts = Counter()
    city_accepts = defaultdict(int)
    city_rejects = defaultdict(int)
    reject_reasons = Counter()
    for row in plan_rows:
        plan = row.get("plan", {})
        accepted = bool(plan.get("accepted"))
        plan_accepts["accepted" if accepted else "rejected"] += 1
        city = str(plan.get("city", "unknown"))
        if accepted:
            city_accepts[city] += 1
        else:
            city_rejects[city] += 1
            reject_reasons[str(plan.get("reason", "unknown"))] += 1

    avg_opps = (sum(opportunity_counts) / len(opportunity_counts)) if opportunity_counts else 0.0
    avg_skips = (sum(skipped_counts) / len(skipped_counts)) if skipped_counts else 0.0

    return {
        "files": {
            "scan_snapshots": len(scan_rows),
            "planned_orders": len(plan_rows),
            "paper_signals": len(signal_rows),
        },
        "scan_summary": {
            "avg_opportunities_per_scan": round(avg_opps, 2),
            "avg_skipped_per_scan": round(avg_skips, 2),
            "evaluation_status_counts": dict(eval_status),
            "strategy_ids": dict(strategies),
        },
        "planning_summary": {
            "accept_reject_counts": dict(plan_accepts),
            "accepted_by_city": dict(city_accepts),
            "rejected_by_city": dict(city_rejects),
            "top_reject_reasons": dict(reject_reasons.most_common(10)),
        },
        "portfolio_state": portfolio,
    }


def main() -> int:
    base_dir = Path("data/sample")
    report = build_report(base_dir)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
