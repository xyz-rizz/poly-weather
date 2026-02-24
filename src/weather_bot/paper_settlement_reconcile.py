from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
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


def run_paper_settlement_reconcile() -> dict[str, Any]:
    base_dir = Path(os.getenv("WEATHER_BOT_RUNNER_BASEDIR", "data/sample"))
    journal_path = Path(os.getenv("WEATHER_BOT_PAPER_JOURNAL_PATH", str(base_dir / "paper_journal.jsonl")))
    state_path = Path(os.getenv("WEATHER_BOT_PAPER_SETTLEMENT_STATE", str(base_dir / "paper_settlement_state.json")))
    ledger_path = Path(os.getenv("WEATHER_BOT_PAPER_SETTLEMENT_LEDGER", str(base_dir / "paper_settlement_ledger.jsonl")))

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
    ledger_rows: list[dict[str, Any]] = []
    realized_delta = 0.0
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
                "settled_end_utc": out.end_date_utc,
                "outcome_yes": out.outcome_yes,
                "settlement_source": "gamma",
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
    report = {
        "as_of_utc": datetime.now(UTC).isoformat(),
        "journal_path": str(journal_path),
        "state_path": str(state_path),
        "ledger_path": str(ledger_path),
        "signals_seen": ingested_signals,
        "positions_added": added_positions,
        "duplicate_signals_skipped": duplicate_signals_skipped,
        "malformed_signals_skipped": malformed_signals_skipped,
        "open_positions": len(next_state.open_positions),
        "settled_this_run": settled_now,
        "wins_this_run": wins,
        "losses_this_run": losses,
        "realized_pnl_delta_usd": round(realized_delta, 6),
        "realized_pnl_total_usd": round(next_state.realized_pnl_usd, 6),
        "settled_trades_total": next_state.settled_trades,
        "resolved_market_ids_available": len(outcome_by_market),
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

