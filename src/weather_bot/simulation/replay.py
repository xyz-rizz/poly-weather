from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


@dataclass(frozen=True)
class ReplaySummary:
    scans: int
    planned_orders: int
    accepted_orders: int
    rejected_orders: int
    opportunities_seen: int
    skipped_seen: int
    markets_seen: int


def build_replay_summary(base_dir: Path) -> ReplaySummary:
    scan_rows = _read_jsonl(base_dir / "scan_snapshots.jsonl")
    plan_rows = _read_jsonl(base_dir / "planned_orders.jsonl")

    opp_count = 0
    skip_count = 0
    markets = set()
    for row in scan_rows:
        result = row.get("scan_result", {})
        evaluations = result.get("evaluations", [])
        for ev in evaluations:
            market = ev.get("market", {})
            market_id = market.get("market_id")
            if market_id:
                markets.add(market_id)
            status = ev.get("status")
            if status == "opportunity":
                opp_count += 1
            elif status == "skipped":
                skip_count += 1

    accepted = 0
    rejected = 0
    for row in plan_rows:
        plan = row.get("plan", {})
        if bool(plan.get("accepted")):
            accepted += 1
        else:
            rejected += 1

    return ReplaySummary(
        scans=len(scan_rows),
        planned_orders=len(plan_rows),
        accepted_orders=accepted,
        rejected_orders=rejected,
        opportunities_seen=opp_count,
        skipped_seen=skip_count,
        markets_seen=len(markets),
    )


def build_market_timelines(base_dir: Path) -> dict[str, list[dict[str, Any]]]:
    scan_rows = _read_jsonl(base_dir / "scan_snapshots.jsonl")
    timelines: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scan_rows:
        strategy_id = row.get("strategy_id", "unknown")
        scan_mode = row.get("scan_mode", "unknown")
        scan_result = row.get("scan_result", {})
        scanned_at = scan_result.get("scanned_at_utc") or row.get("created_at_utc")
        for ev in scan_result.get("evaluations", []):
            market = ev.get("market", {})
            market_id = market.get("market_id")
            if not market_id:
                continue
            opp = ev.get("opportunity")
            quote = ev.get("quote")
            timelines[market_id].append(
                {
                    "scanned_at_utc": scanned_at,
                    "strategy_id": strategy_id,
                    "scan_mode": scan_mode,
                    "status": ev.get("status"),
                    "reason": ev.get("reason"),
                    "city": market.get("city"),
                    "edge": None if not opp else opp.get("edge"),
                    "confidence_score": None if not opp else opp.get("confidence_score"),
                    "implied_yes_mid": None if not opp else opp.get("implied_yes_mid"),
                    "model_prob_yes": None if not opp else opp.get("model_prob_yes"),
                    "quote_yes_bid": None if not quote else quote.get("yes_bid"),
                    "quote_yes_ask": None if not quote else quote.get("yes_ask"),
                }
            )
    for rows in timelines.values():
        rows.sort(key=lambda r: str(r.get("scanned_at_utc")))
    return dict(timelines)


def summarize_reject_reasons(base_dir: Path) -> dict[str, int]:
    plan_rows = _read_jsonl(base_dir / "planned_orders.jsonl")
    counts: Counter[str] = Counter()
    for row in plan_rows:
        plan = row.get("plan", {})
        if bool(plan.get("accepted")):
            continue
        counts[str(plan.get("reason", "unknown"))] += 1
    return dict(counts.most_common(20))
