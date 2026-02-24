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
    original_size_usd: float
    entry_price: float
    signal_time_utc: datetime
    target_time_utc: datetime | None = None
    partial_tp_taken: bool = False
    peak_mark_return_pct: float | None = None
    source_event_type: str = "signal"


@dataclass
class PaperSettlementState:
    as_of_utc: datetime | None = None
    open_positions: list[PaperSignalPosition] = field(default_factory=list)
    closed_market_ids: list[str] = field(default_factory=list)
    realized_pnl_usd: float = 0.0
    settled_trades: int = 0


@dataclass(frozen=True)
class ExitRegime:
    key: str
    city_key: str
    horizon_key: str
    take_profit_pct: float
    stop_loss_pct: float
    time_stop_grace_seconds: float
    mark_fresh_seconds: float


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
                    original_size_usd=float(row.get("original_size_usd") or row.get("size_usd") or 0.0),
                    entry_price=float(row.get("entry_price") or 0.5),
                    signal_time_utc=dt,
                    target_time_utc=_parse_dt(row.get("target_time_utc")),
                    partial_tp_taken=bool(row.get("partial_tp_taken", False)),
                    peak_mark_return_pct=_as_float(row.get("peak_mark_return_pct")),
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


def _horizon_bucket_from_hours(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours < 0:
        return "expired"
    if hours <= 2:
        return "0-2h"
    if hours <= 6:
        return "2-6h"
    if hours <= 12:
        return "6-12h"
    if hours <= 24:
        return "12-24h"
    return "24h+"


def _hours_to_target(now_utc: datetime, target_time_utc: datetime | None) -> float | None:
    if target_time_utc is None:
        return None
    try:
        return (target_time_utc - now_utc).total_seconds() / 3600.0
    except Exception:
        return None


def _load_exit_regime_profile(
    *,
    base_dir: Path,
    default_take_profit_pct: float,
    default_stop_loss_pct: float,
    default_time_stop_grace_seconds: float,
    default_mark_fresh_seconds: float,
) -> dict[str, dict[str, dict[str, float]]]:
    profile_path_txt = os.getenv("WEATHER_BOT_PAPER_EXIT_REGIME_PROFILE_PATH", "").strip()
    profile_json_txt = os.getenv("WEATHER_BOT_PAPER_EXIT_REGIME_PROFILE_JSON", "").strip()
    raw: dict[str, Any] | None = None
    if profile_path_txt:
        profile_path = Path(profile_path_txt)
        if not profile_path.is_absolute():
            profile_path = base_dir / profile_path
        try:
            loaded = json.loads(profile_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except Exception:
            raw = None
    elif profile_json_txt:
        try:
            loaded = json.loads(profile_json_txt)
            if isinstance(loaded, dict):
                raw = loaded
        except Exception:
            raw = None

    defaults = {
        "take_profit_pct": default_take_profit_pct,
        "stop_loss_pct": default_stop_loss_pct,
        "time_stop_grace_seconds": default_time_stop_grace_seconds,
        "mark_fresh_seconds": default_mark_fresh_seconds,
    }
    out: dict[str, dict[str, dict[str, float]]] = {"all": {"all": dict(defaults)}}
    if not raw:
        return out
    cities = raw.get("cities") if isinstance(raw.get("cities"), dict) else {}
    global_cfg = raw.get("global") if isinstance(raw.get("global"), dict) else {}
    if global_cfg:
        out["all"]["all"] = _normalize_regime_values(global_cfg, defaults)
        horizons = global_cfg.get("horizons") if isinstance(global_cfg.get("horizons"), dict) else {}
        for hk, hv in horizons.items():
            if isinstance(hv, dict):
                out["all"][str(hk)] = _normalize_regime_values(hv, out["all"]["all"])
    for city_key, cfg in cities.items():
        if not isinstance(cfg, dict):
            continue
        city_norm = str(city_key).strip() or "all"
        city_base = _normalize_regime_values(cfg, out["all"]["all"])
        out[city_norm] = {"all": city_base}
        horizons = cfg.get("horizons") if isinstance(cfg.get("horizons"), dict) else {}
        for hk, hv in horizons.items():
            if isinstance(hv, dict):
                out[city_norm][str(hk)] = _normalize_regime_values(hv, city_base)
    return out


def _normalize_regime_values(values: dict[str, Any], base: dict[str, float]) -> dict[str, float]:
    out = dict(base)
    for key in ("take_profit_pct", "stop_loss_pct", "time_stop_grace_seconds", "mark_fresh_seconds"):
        v = _as_float(values.get(key))
        if v is None:
            continue
        if key in {"take_profit_pct", "stop_loss_pct"}:
            v = max(0.0, min(5.0, v))
        elif key == "time_stop_grace_seconds":
            v = max(0.0, min(86400.0, v))
        elif key == "mark_fresh_seconds":
            v = max(1.0, min(86400.0, v))
        out[key] = float(v)
    return out


def _select_exit_regime(
    pos: PaperSignalPosition,
    *,
    now_utc: datetime,
    profile: dict[str, dict[str, dict[str, float]]],
) -> ExitRegime:
    city_key = (pos.city or "").strip() or "all"
    hours = _hours_to_target(now_utc, pos.target_time_utc)
    horizon_key = _horizon_bucket_from_hours(hours)
    city_cfg = profile.get(city_key) or profile.get("all") or {"all": {}}
    vals = city_cfg.get(horizon_key) or city_cfg.get("all") or (profile.get("all") or {}).get(horizon_key) or (profile.get("all") or {}).get("all") or {}
    return ExitRegime(
        key=f"city={city_key}|h={horizon_key}",
        city_key=city_key,
        horizon_key=horizon_key,
        take_profit_pct=float(vals.get("take_profit_pct", 0.35)),
        stop_loss_pct=float(vals.get("stop_loss_pct", 0.20)),
        time_stop_grace_seconds=float(vals.get("time_stop_grace_seconds", 300.0)),
        mark_fresh_seconds=float(vals.get("mark_fresh_seconds", 1800.0)),
    )


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
    regime: ExitRegime,
    core_break_even_enabled: bool,
    core_break_even_buffer_pct: float,
    core_trailing_enabled: bool,
    core_trailing_drawdown_pct: float,
    core_trailing_min_peak_return_pct: float,
    min_hold_minutes_before_stop_loss: float,
    max_spread_for_stop_loss: float,
    max_spread_for_take_profit: float,
) -> tuple[float, str, float, float, float, float] | None:
    quote_ts = _parse_dt(mark_row.get("quote_time_utc")) or _parse_dt(mark_row.get("snapshot_time_utc"))
    if quote_ts is None:
        return None
    age_sec = max(0.0, (now_utc - quote_ts).total_seconds())
    if age_sec > regime.mark_fresh_seconds:
        return None

    implied_mid = _as_float(mark_row.get("implied_yes_mid"))
    yes_bid = _as_float(mark_row.get("yes_bid"))
    yes_ask = _as_float(mark_row.get("yes_ask"))
    no_bid = _as_float(mark_row.get("no_bid"))
    no_ask = _as_float(mark_row.get("no_ask"))
    yes_spread = None
    no_spread = None
    if yes_bid is not None and yes_ask is not None:
        yes_spread = max(0.0, yes_ask - yes_bid)
    if no_bid is not None and no_ask is not None:
        no_spread = max(0.0, no_ask - no_bid)

    mark_yes = implied_mid
    if mark_yes is None and yes_bid is not None and yes_ask is not None:
        mark_yes = (yes_bid + yes_ask) / 2.0
    if mark_yes is None:
        return None
    mark_yes = max(0.001, min(0.999, mark_yes))

    if pos.direction == "BUY_YES":
        mark_pos = mark_yes
        exit_fill = yes_bid if yes_bid is not None else mark_yes
        pos_spread = yes_spread
    else:
        mark_pos = 1.0 - mark_yes
        exit_fill = no_bid if no_bid is not None else (1.0 - mark_yes)
        pos_spread = no_spread
    exit_fill = max(0.001, min(0.999, exit_fill))

    ret = (mark_pos - pos.entry_price) / max(pos.entry_price, 1e-9)
    ret_exec = (exit_fill - pos.entry_price) / max(pos.entry_price, 1e-9)
    shares = pos.size_usd / max(pos.entry_price, 1e-9)
    unrealized_pnl = shares * (mark_pos - pos.entry_price)
    prev_peak = pos.peak_mark_return_pct
    if prev_peak is None or ret > prev_peak:
        pos.peak_mark_return_pct = ret

    reason = None
    hold_minutes = max(0.0, (now_utc - pos.signal_time_utc).total_seconds() / 60.0)
    if pos_spread is not None and pos_spread > max_spread_for_take_profit and ret_exec < regime.take_profit_pct:
        pass
    elif ret_exec >= regime.take_profit_pct:
        reason = "take_profit"
    elif pos.partial_tp_taken and core_trailing_enabled:
        peak = pos.peak_mark_return_pct if pos.peak_mark_return_pct is not None else ret
        if peak >= core_trailing_min_peak_return_pct and ret_exec <= (peak - abs(core_trailing_drawdown_pct)):
            reason = "trailing_stop"
    elif pos.partial_tp_taken and core_break_even_enabled:
        if ret_exec <= core_break_even_buffer_pct:
            reason = "break_even_stop"
    elif (
        hold_minutes >= max(0.0, min_hold_minutes_before_stop_loss)
        and (pos_spread is None or pos_spread <= max_spread_for_stop_loss)
        and ret_exec <= -abs(regime.stop_loss_pct)
    ):
        reason = "stop_loss"
    else:
        target_time = pos.target_time_utc or _parse_dt(mark_row.get("target_time_utc"))
        if target_time is not None and now_utc >= (target_time + timedelta(seconds=max(0.0, regime.time_stop_grace_seconds))):
            reason = "time_stop"
    if reason is None:
        return None
    return exit_fill, reason, ret, unrealized_pnl, ret_exec, pos_spread or 0.0


def _mark_yes_mid_from_row(row: dict[str, Any]) -> float | None:
    implied_mid = _as_float(row.get("implied_yes_mid"))
    if implied_mid is not None:
        return max(0.001, min(0.999, implied_mid))
    yes_bid = _as_float(row.get("yes_bid"))
    yes_ask = _as_float(row.get("yes_ask"))
    if yes_bid is not None and yes_ask is not None:
        return max(0.001, min(0.999, (yes_bid + yes_ask) / 2.0))
    return None


def _bounded_fraction(value: float) -> float:
    return max(0.0, min(1.0, value))


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
    partial_tp_enabled = os.getenv("WEATHER_BOT_PAPER_PARTIAL_TP_ENABLED", "1").strip().lower() in {"1", "true", "yes"}
    partial_tp_fraction = _bounded_fraction(float(os.getenv("WEATHER_BOT_PAPER_PARTIAL_TP_FRACTION", "0.5")))
    partial_tp_min_close_usd = float(os.getenv("WEATHER_BOT_PAPER_PARTIAL_TP_MIN_CLOSE_USD", "1.0"))
    partial_tp_min_remaining_usd = float(os.getenv("WEATHER_BOT_PAPER_PARTIAL_TP_MIN_REMAINING_USD", "1.0"))
    core_break_even_enabled = os.getenv("WEATHER_BOT_PAPER_CORE_BREAK_EVEN_ENABLED", "1").strip().lower() in {"1", "true", "yes"}
    core_break_even_buffer_pct = float(os.getenv("WEATHER_BOT_PAPER_CORE_BREAK_EVEN_BUFFER_PCT", "0.02"))
    core_trailing_enabled = os.getenv("WEATHER_BOT_PAPER_CORE_TRAILING_ENABLED", "1").strip().lower() in {"1", "true", "yes"}
    core_trailing_drawdown_pct = float(os.getenv("WEATHER_BOT_PAPER_CORE_TRAILING_DRAWDOWN_PCT", "0.15"))
    core_trailing_min_peak_return_pct = float(os.getenv("WEATHER_BOT_PAPER_CORE_TRAILING_MIN_PEAK_RETURN_PCT", "0.25"))
    min_hold_minutes_before_stop_loss = float(os.getenv("WEATHER_BOT_PAPER_MIN_HOLD_MINUTES_BEFORE_SL", "20"))
    max_spread_for_stop_loss = float(os.getenv("WEATHER_BOT_PAPER_MAX_SPREAD_FOR_SL", "0.12"))
    max_spread_for_take_profit = float(os.getenv("WEATHER_BOT_PAPER_MAX_SPREAD_FOR_TP", "0.18"))
    exit_regime_profile = _load_exit_regime_profile(
        base_dir=base_dir,
        default_take_profit_pct=take_profit_pct,
        default_stop_loss_pct=stop_loss_pct,
        default_time_stop_grace_seconds=time_stop_grace_seconds,
        default_mark_fresh_seconds=mark_fresh_seconds,
    )

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
            original_size_usd=size_usd,
            entry_price=entry_price,
            signal_time_utc=signal_time,
            target_time_utc=_parse_dt(market.get("target_time_utc")),
            partial_tp_taken=False,
            peak_mark_return_pct=None,
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
    partial_mark_exits_now = 0
    metadata_backfills = 0
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
            changed = False
            if not pos.event_slug and row.get("event_slug"):
                pos.event_slug = str(row.get("event_slug") or "")
                changed = True
            if not pos.city and row.get("city"):
                pos.city = str(row.get("city") or "")
                changed = True
            if pos.target_time_utc is None:
                tgt = _parse_dt(row.get("target_time_utc"))
                if tgt is not None:
                    pos.target_time_utc = tgt
                    changed = True
            if changed:
                metadata_backfills += 1
            regime = _select_exit_regime(pos, now_utc=now_utc, profile=exit_regime_profile)
            decision = _mark_exit_price_and_reason(
                pos,
                row,
                now_utc=now_utc,
                regime=regime,
                core_break_even_enabled=core_break_even_enabled,
                core_break_even_buffer_pct=core_break_even_buffer_pct,
                core_trailing_enabled=core_trailing_enabled,
                core_trailing_drawdown_pct=core_trailing_drawdown_pct,
                core_trailing_min_peak_return_pct=core_trailing_min_peak_return_pct,
                min_hold_minutes_before_stop_loss=min_hold_minutes_before_stop_loss,
                max_spread_for_stop_loss=max_spread_for_stop_loss,
                max_spread_for_take_profit=max_spread_for_take_profit,
            )
            if decision is None:
                continue
            exit_price, exit_reason, ret_mark, _, ret_exec, yes_spread = decision
            close_size_usd = pos.size_usd
            partial_exit = False
            if (
                partial_tp_enabled
                and exit_reason == "take_profit"
                and not pos.partial_tp_taken
                and 0.0 < partial_tp_fraction < 1.0
            ):
                candidate_close = pos.size_usd * partial_tp_fraction
                candidate_remaining = pos.size_usd - candidate_close
                if candidate_close >= partial_tp_min_close_usd and candidate_remaining >= partial_tp_min_remaining_usd:
                    close_size_usd = candidate_close
                    partial_exit = True
            shares = close_size_usd / max(pos.entry_price, 1e-9)
            pnl = shares * (exit_price - pos.entry_price)
            ret_realized = (exit_price - pos.entry_price) / max(pos.entry_price, 1e-9)
            mark_yes_mid = _mark_yes_mid_from_row(row)
            mark_exits_now += 1
            if partial_exit:
                partial_mark_exits_now += 1
            realized_delta += pnl
            if partial_exit:
                pos.size_usd = max(0.0, pos.size_usd - close_size_usd)
                pos.partial_tp_taken = True
            else:
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
                    "size_usd": close_size_usd,
                    "remaining_size_usd": None if not partial_exit else round(pos.size_usd, 6),
                    "partial_exit": partial_exit,
                    "partial_tp_taken_after": pos.partial_tp_taken,
                    "peak_mark_return_pct_after": None if pos.peak_mark_return_pct is None else round(pos.peak_mark_return_pct, 6),
                    "position_original_size_usd": pos.original_size_usd,
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
                    "mark_return_at_exec_pct": round(ret_exec, 6),
                    "mark_yes_spread": round(yes_spread, 6),
                    "mark_source": "feature_rows_export_or_scan_snapshot",
                    "exit_regime_key": regime.key,
                    "exit_regime_city": regime.city_key,
                    "exit_regime_horizon": regime.horizon_key,
                    "exit_regime_take_profit_pct": regime.take_profit_pct,
                    "exit_regime_stop_loss_pct": regime.stop_loss_pct,
                    "exit_regime_time_stop_grace_seconds": regime.time_stop_grace_seconds,
                    "exit_regime_mark_fresh_seconds": regime.mark_fresh_seconds,
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
        "partial_mark_exits_this_run": partial_mark_exits_now,
        "settlement_trades_this_run": settlement_trades_now,
        "wins_this_run": wins,
        "losses_this_run": losses,
        "realized_pnl_delta_usd": round(realized_delta, 6),
        "realized_pnl_total_usd": round(next_state.realized_pnl_usd, 6),
        "settled_trades_total": next_state.settled_trades,
        "resolved_market_ids_available": len(outcome_by_market),
        "mark_exits_enabled": mark_exits_enabled,
        "metadata_backfills_this_run": metadata_backfills,
        "mark_max_age_seconds": mark_fresh_seconds,
        "take_profit_pct": take_profit_pct,
        "stop_loss_pct": stop_loss_pct,
        "time_stop_grace_seconds": time_stop_grace_seconds,
        "partial_tp_enabled": partial_tp_enabled,
        "partial_tp_fraction": partial_tp_fraction,
        "partial_tp_min_close_usd": partial_tp_min_close_usd,
        "partial_tp_min_remaining_usd": partial_tp_min_remaining_usd,
        "core_break_even_enabled": core_break_even_enabled,
        "core_break_even_buffer_pct": core_break_even_buffer_pct,
        "core_trailing_enabled": core_trailing_enabled,
        "core_trailing_drawdown_pct": core_trailing_drawdown_pct,
        "core_trailing_min_peak_return_pct": core_trailing_min_peak_return_pct,
        "min_hold_minutes_before_stop_loss": min_hold_minutes_before_stop_loss,
        "max_spread_for_stop_loss": max_spread_for_stop_loss,
        "max_spread_for_take_profit": max_spread_for_take_profit,
        "exit_regime_profile_enabled": bool(os.getenv("WEATHER_BOT_PAPER_EXIT_REGIME_PROFILE_PATH", "").strip() or os.getenv("WEATHER_BOT_PAPER_EXIT_REGIME_PROFILE_JSON", "").strip()),
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
