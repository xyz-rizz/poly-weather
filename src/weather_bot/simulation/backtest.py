from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from weather_bot.calibration import fetch_settled_weather_outcomes
from weather_bot.utils.http import JsonHttpClient


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    txt = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(txt).astimezone(UTC)
    except ValueError:
        return None


@dataclass
class SimPosition:
    market_id: str
    event_slug: str
    city: str
    direction: str  # BUY_YES / BUY_NO
    size_usd: float
    entry_scan_time: datetime
    entry_fill_price: float
    last_mark_yes_mid: float | None = None
    target_time_utc: datetime | None = None


@dataclass(frozen=True)
class SimTrade:
    market_id: str
    event_slug: str
    city: str
    direction: str
    size_usd: float
    entry_fill_price: float
    exit_fill_price: float
    pnl_usd: float
    return_pct: float
    entry_time_utc: datetime
    exit_time_utc: datetime
    exit_reason: str


@dataclass(frozen=True)
class BacktestSummary:
    trades: int
    wins: int
    losses: int
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    total_pnl_usd: float
    avg_return_pct: float
    max_drawdown_usd: float
    open_positions: int
    marked_open_positions: int
    unmarked_open_positions: int
    gross_open_exposure_usd: float
    avg_open_age_hours: float


def run_replay_backtest(
    base_dir: Path,
    *,
    strategy_id: str | None = None,
    scan_mode: str | None = "live_scan",
    size_usd: float = 5.0,
    take_profit_pct: float = 0.35,
    stop_loss_pct: float = 0.20,
    max_positions_per_event: int = 1,
    min_entry_price: float = 0.03,
    slippage_bps: float = 50.0,
    settle_with_gamma: bool = False,
    universe_level: str = "tier1",
    days_back: int = 30,
    insecure_ssl: bool = False,
) -> dict[str, Any]:
    scans = _read_jsonl(base_dir / "scan_snapshots.jsonl")
    scans = [s for s in scans if s.get("event_type") == "scan_snapshot"]
    if strategy_id:
        scans = [s for s in scans if s.get("strategy_id") == strategy_id]
    if scan_mode:
        scans = [s for s in scans if s.get("scan_mode") == scan_mode]
    scans.sort(key=lambda r: str((r.get("scan_result") or {}).get("scanned_at_utc") or r.get("created_at_utc")))

    positions: dict[str, SimPosition] = {}
    closed_markets: set[str] = set()
    market_meta: dict[str, dict[str, Any]] = {}
    trades: list[SimTrade] = []
    equity = 0.0
    peak_equity = 0.0
    max_dd = 0.0

    for scan in scans:
        scan_time = _parse_dt((scan.get("scan_result") or {}).get("scanned_at_utc") or scan.get("created_at_utc"))
        if scan_time is None:
            continue
        evals = (scan.get("scan_result") or {}).get("evaluations") or []
        eval_map = {str((ev.get("market") or {}).get("market_id") or ""): ev for ev in evals}
        for ev in evals:
            market = ev.get("market") or {}
            market_id = str(market.get("market_id") or "")
            if not market_id:
                continue
            market_meta[market_id] = market

        # Exit pass on existing positions using current quotes.
        to_remove: list[str] = []
        for market_id, pos in positions.items():
            ev = eval_map.get(market_id)
            if not ev:
                continue
            if not pos.event_slug:
                market = ev.get("market") or {}
                pos.event_slug = str(market.get("event_slug") or pos.event_slug)
                pos.city = str(market.get("city") or pos.city)
            quote = ev.get("quote") or {}
            yb = _num(quote.get("yes_bid"))
            ya = _num(quote.get("yes_ask"))
            nb = _num(quote.get("no_bid"))
            na = _num(quote.get("no_ask"))
            if None in (yb, ya):
                continue
            mark_yes = (yb + ya) / 2
            pos.last_mark_yes_mid = mark_yes
            cur_pos_price = mark_yes if pos.direction == "BUY_YES" else (1.0 - mark_yes)
            ret = (cur_pos_price - pos.entry_fill_price) / max(pos.entry_fill_price, 1e-9)
            target_time = _parse_dt((ev.get("market") or {}).get("target_time_utc"))
            if pos.target_time_utc is None:
                pos.target_time_utc = target_time

            exit_reason = None
            if ret >= take_profit_pct:
                exit_reason = "tp"
            elif ret <= -abs(stop_loss_pct):
                exit_reason = "sl"
            elif target_time is not None and scan_time >= target_time:
                exit_reason = "time_stop"
            if exit_reason is None:
                continue

            exit_fill = _exit_fill_price(pos.direction, yb, ya, nb, na, slippage_bps)
            trade = _close_trade(pos, exit_fill, scan_time, exit_reason)
            trades.append(trade)
            equity += trade.pnl_usd
            peak_equity = max(peak_equity, equity)
            max_dd = min(max_dd, equity - peak_equity)
            to_remove.append(market_id)
        for market_id in to_remove:
            closed_markets.add(market_id)
            positions.pop(market_id, None)

        # Entry pass on opportunities.
        event_open_counts: dict[str, int] = {}
        for p in positions.values():
            key = p.event_slug or "unknown"
            event_open_counts[key] = event_open_counts.get(key, 0) + 1

        for ev in evals:
            if ev.get("status") != "opportunity":
                continue
            market = ev.get("market") or {}
            opp = ev.get("opportunity") or {}
            quote = ev.get("quote") or {}
            market_id = str(market.get("market_id") or "")
            if not market_id or market_id in positions or market_id in closed_markets:
                continue
            edge = _num(opp.get("edge"))
            if edge is None:
                continue
            direction = "BUY_YES" if edge > 0 else "BUY_NO"
            event_slug = str(market.get("event_slug") or "")
            if event_open_counts.get(event_slug, 0) >= max_positions_per_event:
                continue
            yb = _num(quote.get("yes_bid"))
            ya = _num(quote.get("yes_ask"))
            nb = _num(quote.get("no_bid"))
            na = _num(quote.get("no_ask"))
            if None in (yb, ya):
                continue
            target_time = _parse_dt(market.get("target_time_utc"))
            if target_time is not None and scan_time >= target_time:
                continue
            entry_fill = _entry_fill_price(direction, yb, ya, nb, na, slippage_bps)
            if entry_fill < min_entry_price:
                continue
            positions[market_id] = SimPosition(
                market_id=market_id,
                event_slug=event_slug,
                city=str(market.get("city") or ""),
                direction=direction,
                size_usd=size_usd,
                entry_scan_time=scan_time,
                entry_fill_price=entry_fill,
                last_mark_yes_mid=(yb + ya) / 2,
                target_time_utc=target_time,
            )
            event_open_counts[event_slug] = event_open_counts.get(event_slug, 0) + 1

    # Optional final settlement for remaining positions using resolved outcomes.
    if settle_with_gamma and positions:
        http = JsonHttpClient(verify_ssl=not insecure_ssl)
        settled = fetch_settled_weather_outcomes(
            universe_level=universe_level,
            days_back=days_back,
            include_today=True,
            http_client=http,
        )
        outcome_map = {o.market_id: o for o in settled}
        now_utc = datetime.now(UTC)
        for market_id, pos in list(positions.items()):
            if not pos.event_slug:
                market = market_meta.get(market_id) or {}
                pos.event_slug = str(market.get("event_slug") or pos.event_slug)
                pos.city = str(market.get("city") or pos.city)
            out = outcome_map.get(market_id)
            if out is None:
                continue
            yes_final = 1.0 if out.outcome_yes == 1 else 0.0
            exit_fill = yes_final if pos.direction == "BUY_YES" else (1.0 - yes_final)
            trade = _close_trade(pos, exit_fill, now_utc, "settlement")
            trades.append(trade)
            equity += trade.pnl_usd
            peak_equity = max(peak_equity, equity)
            max_dd = min(max_dd, equity - peak_equity)
            positions.pop(market_id, None)
            closed_markets.add(market_id)

    open_position_rows, unrealized_pnl = _mark_open_positions(positions, as_of_utc=datetime.now(UTC))
    marked_open_positions = sum(1 for p in open_position_rows if p.get("mark_position_price") is not None)
    summary = _summarize_trades(
        trades,
        max_drawdown_usd=abs(max_dd),
        unrealized_pnl_usd=unrealized_pnl,
        open_positions=len(open_position_rows),
        marked_open_positions=marked_open_positions,
        unmarked_open_positions=len(open_position_rows) - marked_open_positions,
        gross_open_exposure_usd=sum(float(p["size_usd"]) for p in open_position_rows),
        avg_open_age_hours=(
            sum(float(p.get("age_hours") or 0.0) for p in open_position_rows) / len(open_position_rows)
            if open_position_rows
            else 0.0
        ),
    )
    return {
        "summary": summary.__dict__,
        "open_positions": open_position_rows,
        "trades": [
            {
                "market_id": t.market_id,
                "event_slug": t.event_slug,
                "city": t.city,
                "direction": t.direction,
                "size_usd": t.size_usd,
                "entry_fill_price": round(t.entry_fill_price, 6),
                "exit_fill_price": round(t.exit_fill_price, 6),
                "pnl_usd": round(t.pnl_usd, 4),
                "return_pct": round(t.return_pct, 4),
                "entry_time_utc": t.entry_time_utc.isoformat(),
                "exit_time_utc": t.exit_time_utc.isoformat(),
                "exit_reason": t.exit_reason,
            }
            for t in trades[-200:]
        ],
    }


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _entry_fill_price(direction: str, yb: float, ya: float, nb: float | None, na: float | None, slippage_bps: float) -> float:
    slip = slippage_bps / 10000.0
    if direction == "BUY_YES":
        return min(0.999, max(0.001, ya * (1 + slip)))
    no_ask = na if na is not None else (1.0 - yb)
    return min(0.999, max(0.001, no_ask * (1 + slip)))


def _exit_fill_price(direction: str, yb: float, ya: float, nb: float | None, na: float | None, slippage_bps: float) -> float:
    slip = slippage_bps / 10000.0
    if direction == "BUY_YES":
        return min(0.999, max(0.001, yb * (1 - slip)))
    no_bid = nb if nb is not None else (1.0 - ya)
    return min(0.999, max(0.001, no_bid * (1 - slip)))


def _close_trade(pos: SimPosition, exit_fill_price: float, exit_time_utc: datetime, reason: str) -> SimTrade:
    shares = pos.size_usd / max(pos.entry_fill_price, 1e-9)
    pnl = shares * (exit_fill_price - pos.entry_fill_price)
    ret = (exit_fill_price - pos.entry_fill_price) / max(pos.entry_fill_price, 1e-9)
    return SimTrade(
        market_id=pos.market_id,
        event_slug=pos.event_slug,
        city=pos.city,
        direction=pos.direction,
        size_usd=pos.size_usd,
        entry_fill_price=pos.entry_fill_price,
        exit_fill_price=exit_fill_price,
        pnl_usd=pnl,
        return_pct=ret,
        entry_time_utc=pos.entry_scan_time,
        exit_time_utc=exit_time_utc,
        exit_reason=reason,
    )


def _mark_open_positions(positions: dict[str, SimPosition], *, as_of_utc: datetime) -> tuple[list[dict[str, Any]], float]:
    rows: list[dict[str, Any]] = []
    total_unrealized = 0.0
    for p in positions.values():
        has_mark = p.last_mark_yes_mid is not None
        mark_pos_price = None if p.last_mark_yes_mid is None else (
            p.last_mark_yes_mid if p.direction == "BUY_YES" else (1.0 - p.last_mark_yes_mid)
        )
        unrealized: float | None = None
        if mark_pos_price is not None:
            shares = p.size_usd / max(p.entry_fill_price, 1e-9)
            unrealized = shares * (mark_pos_price - p.entry_fill_price)
            total_unrealized += unrealized
        rows.append(
            {
                "market_id": p.market_id,
                "event_slug": p.event_slug,
                "city": p.city,
                "direction": p.direction,
                "size_usd": round(p.size_usd, 4),
                "entry_fill_price": round(p.entry_fill_price, 6),
                "entry_time_utc": p.entry_scan_time.isoformat(),
                "target_time_utc": None if p.target_time_utc is None else p.target_time_utc.isoformat(),
                "last_mark_yes_mid": None if p.last_mark_yes_mid is None else round(p.last_mark_yes_mid, 6),
                "mark_position_price": None if mark_pos_price is None else round(mark_pos_price, 6),
                "unrealized_pnl_usd": None if unrealized is None else round(unrealized, 4),
                "unrealized_return_pct": (
                    None
                    if mark_pos_price is None
                    else round((mark_pos_price - p.entry_fill_price) / max(p.entry_fill_price, 1e-9), 4)
                ),
                "mark_available": has_mark,
                "age_hours": round((as_of_utc - p.entry_scan_time).total_seconds() / 3600.0, 3),
            }
        )
    rows.sort(
        key=lambda r: (
            1 if r.get("mark_available") else 0,
            float(r.get("unrealized_pnl_usd") or 0.0),
        )
    )
    return rows, total_unrealized


def _summarize_trades(
    trades: list[SimTrade],
    *,
    max_drawdown_usd: float,
    unrealized_pnl_usd: float,
    open_positions: int,
    marked_open_positions: int,
    unmarked_open_positions: int,
    gross_open_exposure_usd: float,
    avg_open_age_hours: float,
) -> BacktestSummary:
    if not trades:
        return BacktestSummary(
            0,
            0,
            0,
            0.0,
            round(unrealized_pnl_usd, 4),
            round(unrealized_pnl_usd, 4),
            0.0,
            max_drawdown_usd,
            open_positions,
            marked_open_positions,
            unmarked_open_positions,
            round(gross_open_exposure_usd, 4),
            round(avg_open_age_hours, 4),
        )
    wins = sum(1 for t in trades if t.pnl_usd > 0)
    losses = sum(1 for t in trades if t.pnl_usd < 0)
    realized = sum(t.pnl_usd for t in trades)
    avg_ret = sum(t.return_pct for t in trades) / len(trades)
    return BacktestSummary(
        len(trades),
        wins,
        losses,
        round(realized, 4),
        round(unrealized_pnl_usd, 4),
        round(realized + unrealized_pnl_usd, 4),
        avg_ret,
        max_drawdown_usd,
        open_positions,
        marked_open_positions,
        unmarked_open_positions,
        round(gross_open_exposure_usd, 4),
        round(avg_open_age_hours, 4),
    )
