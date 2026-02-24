from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from weather_bot.calibration import fetch_settled_weather_outcomes
from weather_bot.utils.http import JsonHttpClient


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    txt = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(txt).astimezone(UTC)
    except ValueError:
        return None


def _serialize(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.astimezone(UTC).isoformat()
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


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


@dataclass
class PaperSignalPosition:
    market_id: str
    event_slug: str
    city: str
    direction: str
    size_usd: float
    entry_price: float
    signal_time_utc: datetime
    target_time_utc: datetime | None = None
    source_event_type: str = "signal"


@dataclass
class PaperSettlementState:
    as_of_utc: datetime | None = None
    open_positions: list[PaperSignalPosition] = field(default_factory=list)
    closed_market_ids: list[str] = field(default_factory=list)
    realized_pnl_usd: float = 0.0
    settled_trades: int = 0


def load_state(path: Path) -> PaperSettlementState:
    if not path.exists():
        return PaperSettlementState(as_of_utc=datetime.now(UTC))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return PaperSettlementState(as_of_utc=datetime.now(UTC))
    positions: list[PaperSignalPosition] = []
    for row in raw.get("open_positions", []):
        if not isinstance(row, dict):
            continue
        dt = _parse_dt(row.get("signal_time_utc")) or datetime.now(UTC)
        try:
            positions.append(
                PaperSignalPosition(
                    market_id=str(row.get("market_id") or ""),
                    event_slug=str(row.get("event_slug") or ""),
                    city=str(row.get("city") or ""),
                    direction=str(row.get("direction") or "BUY_YES"),
                    size_usd=float(row.get("size_usd") or 0.0),
                    entry_price=float(row.get("entry_price") or 0.5),
                    signal_time_utc=dt,
                    target_time_utc=_parse_dt(row.get("target_time_utc")),
                    source_event_type=str(row.get("source_event_type") or "signal"),
                )
            )
        except Exception:
            continue
    return PaperSettlementState(
        as_of_utc=_parse_dt(raw.get("as_of_utc")),
        open_positions=positions,
        closed_market_ids=[str(x) for x in (raw.get("closed_market_ids") or []) if str(x)],
        realized_pnl_usd=float(raw.get("realized_pnl_usd") or 0.0),
        settled_trades=int(raw.get("settled_trades") or 0),
    )


def save_state(path: Path, state: PaperSettlementState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_serialize(asdict(state)), indent=2, sort_keys=True), encoding="utf-8")


def _entry_price_from_signal(signal: dict[str, Any]) -> float | None:
    direction = str(signal.get("direction") or "")
    opp = signal.get("opportunity") or {}
    quote = opp.get("quote") or {}
    implied_mid = opp.get("implied_yes_mid")
    try:
        yes_ask = float(quote.get("yes_ask")) if quote.get("yes_ask") is not None else None
    except Exception:
        yes_ask = None
    try:
        no_ask = float(quote.get("no_ask")) if quote.get("no_ask") is not None else None
    except Exception:
        no_ask = None
    try:
        yes_bid = float(quote.get("yes_bid")) if quote.get("yes_bid") is not None else None
    except Exception:
        yes_bid = None

    if direction == "BUY_YES":
        if yes_ask is not None:
            return max(0.001, min(0.999, yes_ask))
        if implied_mid is not None:
            return max(0.001, min(0.999, float(implied_mid)))
    if direction == "BUY_NO":
        if no_ask is not None:
            return max(0.001, min(0.999, no_ask))
        if yes_bid is not None:
            return max(0.001, min(0.999, 1.0 - yes_bid))
        if implied_mid is not None:
            return max(0.001, min(0.999, 1.0 - float(implied_mid)))
    return None


def _append_ledger_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(_serialize(row), separators=(",", ":")) + "\n")


def _as_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except Exception:
        return None


def _latest_mark_rows_from_feature_export(path: Path) -> dict[str, dict[str, Any]]:
    rows = _read_jsonl(path)
    latest: dict[str, dict[str, Any]] = {}
    latest_ts: dict[str, datetime] = {}
    for row in rows:
        market_id = str(row.get("market_id") or "")
        if not market_id:
            continue
        quote_ts = _parse_dt(row.get("quote_time_utc"))
        snap_ts = _parse_dt(row.get("snapshot_time_utc"))
        ts = quote_ts or snap_ts
        if ts is None:
            continue
        prev_ts = latest_ts.get(market_id)
        if prev_ts is None or ts > prev_ts:
            latest_ts[market_id] = ts
            latest[market_id] = row
    return latest


def _latest_mark_rows_from_scan_snapshots(path: Path) -> dict[str, dict[str, Any]]:
    rows = _read_jsonl(path)
    latest: dict[str, dict[str, Any]] = {}
    latest_ts: dict[str, datetime] = {}
    for row in rows:
        if row.get("event_type") != "scan_snapshot":
            continue
        scan_result = row.get("scan_result") or {}
        features = scan_result.get("feature_rows") or []
        scanned_at = _parse_dt(scan_result.get("scanned_at_utc") or row.get("created_at_utc"))
        for fr in features:
            if not isinstance(fr, dict):
                continue
            market_id = str(fr.get("market_id") or "")
            if not market_id:
                continue
            ts = _parse_dt(fr.get("quote_time_utc")) or scanned_at
            if ts is None:
                continue
            prev_ts = latest_ts.get(market_id)
            if prev_ts is None or ts > prev_ts:
                latest_ts[market_id] = ts
                latest[market_id] = fr
    return latest


def _load_latest_mark_rows(base_dir: Path) -> tuple[dict[str, dict[str, Any]], str]:
    feature_path = Path(os.getenv("WEATHER_BOT_FEATURE_EXPORT_PATH", str(base_dir / "feature_rows_export.jsonl")))
    if feature_path.exists():
        rows = _latest_mark_rows_from_feature_export(feature_path)
        if rows:
            return rows, str(feature_path)
    scan_path = Path(os.getenv("WEATHER_BOT_SCAN_RECORD_PATH", str(base_dir / "scan_snapshots.jsonl")))
    return _latest_mark_rows_from_scan_snapshots(scan_path), str(scan_path)


def _mark_exit_price_and_reason(
    pos: PaperSignalPosition,
    mark_row: dict[str, Any],
    *,
    now_utc: datetime,
    mark_fresh_seconds: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    time_stop_grace_seconds: float,
) -> tuple[float, str, float, float] | None:
    quote_ts = _parse_dt(mark_row.get("quote_time_utc")) or _parse_dt(mark_row.get("snapshot_time_utc"))
    if quote_ts is None:
        return None
    age_sec = max(0.0, (now_utc - quote_ts).total_seconds())
    if age_sec > mark_fresh_seconds:
        return None

    implied_mid = _as_float(mark_row.get("implied_yes_mid"))
    yes_bid = _as_float(mark_row.get("yes_bid"))
    yes_ask = _as_float(mark_row.get("yes_ask"))
    no_bid = _as_float(mark_row.get("no_bid"))
    no_ask = _as_float(mark_row.get("no_ask"))

    mark_yes = implied_mid
    if mark_yes is None and yes_bid is not None and yes_ask is not None:
        mark_yes = (yes_bid + yes_ask) / 2.0
    if mark_yes is None:
        return None
    mark_yes = max(0.001, min(0.999, mark_yes))

    if pos.direction == "BUY_YES":
        mark_pos = mark_yes
        exit_fill = yes_bid if yes_bid is not None else mark_yes
    else:
        mark_pos = 1.0 - mark_yes
        exit_fill = no_bid if no_bid is not None else (1.0 - mark_yes)
    exit_fill = max(0.001, min(0.999, exit_fill))

    ret = (mark_pos - pos.entry_price) / max(pos.entry_price, 1e-9)
    shares = pos.size_usd / max(pos.entry_price, 1e-9)
    unrealized_pnl = shares * (mark_pos - pos.entry_price)

    reason = None
    if ret >= take_profit_pct:
        reason = "take_profit"
    elif ret <= -abs(stop_loss_pct):
        reason = "stop_loss"
    else:
        target_time = pos.target_time_utc or _parse_dt(mark_row.get("target_time_utc"))
        if target_time is not None and now_utc >= (target_time + timedelta(seconds=max(0.0, time_stop_grace_seconds))):
            reason = "time_stop"
    if reason is None:
        return None
    return exit_fill, reason, ret, unrealized_pnl


def _mark_yes_mid_from_row(row: dict[str, Any]) -> float | None:
    implied_mid = _as_float(row.get("implied_yes_mid"))
    if implied_mid is not None:
        return max(0.001, min(0.999, implied_mid))
    yes_bid = _as_float(row.get("yes_bid"))
    yes_ask = _as_float(row.get("yes_ask"))
    if yes_bid is not None and yes_ask is not None:
        return max(0.001, min(0.999, (yes_bid + yes_ask) / 2.0))
    return None


def run_paper_settlement_reconcile() -> dict[str, Any]:
    base_dir = Path(os.getenv("WEATHER_BOT_RUNNER_BASEDIR", "data/sample"))
    journal_path = Path(os.getenv("WEATHER_BOT_PAPER_JOURNAL_PATH", str(base_dir / "paper_journal.jsonl")))
    state_path = Path(os.getenv("WEATHER_BOT_PAPER_SETTLEMENT_STATE", str(base_dir / "paper_settlement_state.json")))
    ledger_path = Path(os.getenv("WEATHER_BOT_PAPER_SETTLEMENT_LEDGER", str(base_dir / "paper_settlement_ledger.jsonl")))
    mark_exits_enabled = os.getenv("WEATHER_BOT_PAPER_MARK_EXITS_ENABLED", "1").strip().lower() in {"1", "true", "yes"}
    mark_fresh_seconds = float(os.getenv("WEATHER_BOT_PAPER_MARK_MAX_AGE_SECONDS", "1800"))
    take_profit_pct = float(os.getenv("WEATHER_BOT_PAPER_TAKE_PROFIT_PCT", "0.35"))
    stop_loss_pct = float(os.getenv("WEATHER_BOT_PAPER_STOP_LOSS_PCT", "0.20"))
    time_stop_grace_seconds = float(os.getenv("WEATHER_BOT_PAPER_TIME_STOP_GRACE_SECONDS", "300"))

    state = load_state(state_path)
    closed_ids = set(state.closed_market_ids)
    open_by_market = {p.market_id: p for p in state.open_positions if p.market_id}

    signals = _read_jsonl(journal_path)
    ingested_signals = 0
    added_positions = 0
    duplicate_signals_skipped = 0
    malformed_signals_skipped = 0
    for row in signals:
        if row.get("event_type") != "signal":
            continue
        ingested_signals += 1
        opp = row.get("opportunity") or {}
        market = opp.get("market") or {}
        market_id = str(market.get("market_id") or "")
        if not market_id:
            malformed_signals_skipped += 1
            continue
        if market_id in closed_ids or market_id in open_by_market:
            duplicate_signals_skipped += 1
            continue
        entry_price = _entry_price_from_signal(row)
        if entry_price is None:
            malformed_signals_skipped += 1
            continue
        signal_time = _parse_dt(row.get("created_at_utc")) or datetime.now(UTC)
        try:
            size_usd = float(row.get("size_usd") or 0.0)
        except Exception:
            size_usd = 0.0
        if size_usd <= 0:
            malformed_signals_skipped += 1
            continue
        pos = PaperSignalPosition(
            market_id=market_id,
            event_slug=str(market.get("event_slug") or ""),
            city=str(market.get("city") or ""),
            direction=str(row.get("direction") or "BUY_YES"),
            size_usd=size_usd,
            entry_price=entry_price,
            signal_time_utc=signal_time,
            target_time_utc=_parse_dt(market.get("target_time_utc")),
            source_event_type="signal",
        )
        open_by_market[market_id] = pos
        added_positions += 1

    http = JsonHttpClient(verify_ssl=os.getenv("WEATHER_BOT_INSECURE_SSL", "0").strip().lower() not in {"1", "true", "yes"})
    outcomes = fetch_settled_weather_outcomes(
        universe_level=os.getenv("WEATHER_BOT_UNIVERSE", "tier1"),
        days_back=int(os.getenv("WEATHER_BOT_CAL_DAYS_BACK", "30")),
        include_today=True,
        http_client=http,
    )
    outcome_by_market = {o.market_id: o for o in outcomes if o.market_id}

    settled_now = 0
    mark_exits_now = 0
    ledger_rows: list[dict[str, Any]] = []
    realized_delta = 0.0
    now_utc = datetime.now(UTC)
    for market_id, pos in list(open_by_market.items()):
        out = outcome_by_market.get(market_id)
        if out is None:
            continue
        yes_payout = 1.0 if out.outcome_yes == 1 else 0.0
        exit_price = yes_payout if pos.direction == "BUY_YES" else (1.0 - yes_payout)
        shares = pos.size_usd / max(pos.entry_price, 1e-9)
        pnl = shares * (exit_price - pos.entry_price)
        ret = (exit_price - pos.entry_price) / max(pos.entry_price, 1e-9)
        settled_now += 1
        realized_delta += pnl
        closed_ids.add(market_id)
        open_by_market.pop(market_id, None)
        ledger_rows.append(
            {
                "event_type": "paper_settlement_trade",
                "created_at_utc": datetime.now(UTC),
                "market_id": market_id,
                "event_slug": pos.event_slug or out.event_slug,
                "city": pos.city or out.city,
                "direction": pos.direction,
                "size_usd": pos.size_usd,
                "entry_price": round(pos.entry_price, 6),
                "exit_price": round(exit_price, 6),
                "shares": round(shares, 6),
                "pnl_usd": round(pnl, 6),
                "return_pct": round(ret, 6),
                "signal_time_utc": pos.signal_time_utc,
                "target_time_utc": pos.target_time_utc or out.end_date_utc,
                "settled_end_utc": out.end_date_utc,
                "outcome_yes": out.outcome_yes,
                "settlement_source": "gamma",
            }
        )

    mark_rows: dict[str, dict[str, Any]] = {}
    mark_source_path = None
    if mark_exits_enabled and open_by_market:
        mark_rows, mark_source_path = _load_latest_mark_rows(base_dir)
        for market_id, pos in list(open_by_market.items()):
            row = mark_rows.get(market_id)
            if not isinstance(row, dict):
                continue
            decision = _mark_exit_price_and_reason(
                pos,
                row,
                now_utc=now_utc,
                mark_fresh_seconds=mark_fresh_seconds,
                take_profit_pct=take_profit_pct,
                stop_loss_pct=stop_loss_pct,
                time_stop_grace_seconds=time_stop_grace_seconds,
            )
            if decision is None:
                continue
            exit_price, exit_reason, ret_mark, _ = decision
            shares = pos.size_usd / max(pos.entry_price, 1e-9)
            pnl = shares * (exit_price - pos.entry_price)
            ret_realized = (exit_price - pos.entry_price) / max(pos.entry_price, 1e-9)
            mark_yes_mid = _mark_yes_mid_from_row(row)
            mark_exits_now += 1
            realized_delta += pnl
            closed_ids.add(market_id)
            open_by_market.pop(market_id, None)
            ledger_rows.append(
                {
                    "event_type": "paper_mark_exit_trade",
                    "created_at_utc": now_utc,
                    "market_id": market_id,
                    "event_slug": pos.event_slug or str(row.get("event_slug") or ""),
                    "city": pos.city or str(row.get("city") or ""),
                    "direction": pos.direction,
                    "size_usd": pos.size_usd,
                    "entry_price": round(pos.entry_price, 6),
                    "exit_price": round(exit_price, 6),
                    "shares": round(shares, 6),
                    "pnl_usd": round(pnl, 6),
                    "return_pct": round(ret_realized, 6),
                    "signal_time_utc": pos.signal_time_utc,
                    "target_time_utc": pos.target_time_utc or _parse_dt(row.get("target_time_utc")),
                    "mark_yes_mid": None if mark_yes_mid is None else round(mark_yes_mid, 6),
                    "mark_quote_time_utc": _parse_dt(row.get("quote_time_utc")) or _parse_dt(row.get("snapshot_time_utc")),
                    "mark_reason": exit_reason,
                    "mark_return_at_mid_pct": round(ret_mark, 6),
                    "mark_source": "feature_rows_export_or_scan_snapshot",
                }
            )

    _append_ledger_rows(ledger_path, ledger_rows)
    next_state = PaperSettlementState(
        as_of_utc=datetime.now(UTC),
        open_positions=sorted(open_by_market.values(), key=lambda p: (p.signal_time_utc, p.market_id)),
        closed_market_ids=sorted(closed_ids),
        realized_pnl_usd=state.realized_pnl_usd + realized_delta,
        settled_trades=state.settled_trades + settled_now,
    )
    save_state(state_path, next_state)

    wins = sum(1 for r in ledger_rows if float(r["pnl_usd"]) > 0)
    losses = sum(1 for r in ledger_rows if float(r["pnl_usd"]) < 0)
    settlement_trades_now = sum(1 for r in ledger_rows if r.get("event_type") == "paper_settlement_trade")
    report = {
        "as_of_utc": now_utc.isoformat(),
        "journal_path": str(journal_path),
        "state_path": str(state_path),
        "ledger_path": str(ledger_path),
        "mark_source_path": mark_source_path,
        "signals_seen": ingested_signals,
        "positions_added": added_positions,
        "duplicate_signals_skipped": duplicate_signals_skipped,
        "malformed_signals_skipped": malformed_signals_skipped,
        "open_positions": len(next_state.open_positions),
        "closed_this_run": len(ledger_rows),
        "settled_this_run": settled_now,
        "mark_exits_this_run": mark_exits_now,
        "settlement_trades_this_run": settlement_trades_now,
        "wins_this_run": wins,
        "losses_this_run": losses,
        "realized_pnl_delta_usd": round(realized_delta, 6),
        "realized_pnl_total_usd": round(next_state.realized_pnl_usd, 6),
        "settled_trades_total": next_state.settled_trades,
        "resolved_market_ids_available": len(outcome_by_market),
        "mark_exits_enabled": mark_exits_enabled,
        "mark_max_age_seconds": mark_fresh_seconds,
        "take_profit_pct": take_profit_pct,
        "stop_loss_pct": stop_loss_pct,
        "time_stop_grace_seconds": time_stop_grace_seconds,
    }
    report_path = Path(os.getenv("WEATHER_BOT_PAPER_SETTLEMENT_REPORT", str(base_dir / "paper_settlement_report.json")))
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def main() -> int:
    print(json.dumps(run_paper_settlement_reconcile(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
