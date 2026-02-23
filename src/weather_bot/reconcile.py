from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from weather_bot.simulation.portfolio_state import load_portfolio_state


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


def _latest_mid_by_market(scan_rows: list[dict[str, Any]]) -> dict[str, float]:
    latest: dict[str, tuple[str, float]] = {}
    for row in scan_rows:
        scan_result = row.get("scan_result", {})
        scanned_at = str(scan_result.get("scanned_at_utc") or row.get("created_at_utc") or "")
        for ev in scan_result.get("evaluations", []):
            opp = ev.get("opportunity") or {}
            market = ev.get("market") or {}
            market_id = market.get("market_id")
            if not market_id:
                continue
            mid = opp.get("implied_yes_mid")
            if mid is None:
                quote = ev.get("quote") or {}
                yb = quote.get("yes_bid")
                ya = quote.get("yes_ask")
                if isinstance(yb, (int, float)) and isinstance(ya, (int, float)):
                    mid = (yb + ya) / 2
            if not isinstance(mid, (int, float)):
                continue
            prev = latest.get(market_id)
            if prev is None or scanned_at >= prev[0]:
                latest[market_id] = (scanned_at, float(mid))
    return {k: v[1] for k, v in latest.items()}


def reconcile_portfolio(base_dir: Path) -> dict[str, Any]:
    portfolio = load_portfolio_state(base_dir / "portfolio_state.json")
    scan_rows = _read_jsonl(base_dir / "scan_snapshots.jsonl")
    latest_mid = _latest_mid_by_market(scan_rows)
    rows = []
    total_unrealized = 0.0
    missing_quotes = 0
    for pos in portfolio.open_positions:
        mark_yes = latest_mid.get(pos.market_id)
        if mark_yes is None:
            missing_quotes += 1
            rows.append({"market_id": pos.market_id, "city": pos.city, "status": "missing_mark"})
            continue
        entry_yes = pos.entry_ref_price
        if pos.direction == "BUY_YES":
            entry_cost = entry_yes
            mark_value = mark_yes
        else:
            entry_cost = 1.0 - entry_yes
            mark_value = 1.0 - mark_yes
        shares = pos.size_usd / max(entry_cost, 1e-9)
        unrealized = shares * (mark_value - entry_cost)
        total_unrealized += unrealized
        rows.append(
            {
                "market_id": pos.market_id,
                "city": pos.city,
                "direction": pos.direction,
                "size_usd": pos.size_usd,
                "entry_ref_price": pos.entry_ref_price,
                "mark_yes_mid": round(mark_yes, 6),
                "unrealized_pnl_usd": round(unrealized, 4),
            }
        )
    return {
        "positions": rows,
        "summary": {
            "open_positions": len(portfolio.open_positions),
            "missing_marks": missing_quotes,
            "realized_pnl_today_usd": portfolio.realized_pnl_today_usd,
            "unrealized_pnl_usd": round(total_unrealized, 4),
            "total_estimated_pnl_usd": round(portfolio.realized_pnl_today_usd + total_unrealized, 4),
        },
    }


def main() -> int:
    base_dir = Path("data/sample")
    print(json.dumps(reconcile_portfolio(base_dir), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
