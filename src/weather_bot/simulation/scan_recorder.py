from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weather_bot.core.pipeline import MarketEvaluation, ScanResult


def _serialize(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.astimezone(timezone.utc).isoformat()
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def write_scan_snapshot(
    path: str | Path,
    result: ScanResult,
    mode: str,
    config: dict[str, Any] | None = None,
    strategy_id: str = "unknown",
    run_meta: dict[str, Any] | None = None,
) -> None:
    payload = {
        "schema_version": 1,
        "event_type": "scan_snapshot",
        "created_at_utc": datetime.now(timezone.utc),
        "scan_mode": mode,
        "strategy_id": strategy_id,
        "run_meta": run_meta or {},
        "config": config or {},
        "scan_result": {
            "scanned_at_utc": result.scanned_at_utc,
            "opportunity_count": len(result.opportunities),
            "skipped_count": len(result.skipped_markets),
            "evaluations": [_evaluation_to_dict(ev) for ev in result.evaluations],
            "feature_rows": [_feature_row(ev, scanned_at_utc=result.scanned_at_utc) for ev in result.evaluations],
        },
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_serialize(payload), separators=(",", ":")) + "\n")


def _evaluation_to_dict(ev: MarketEvaluation) -> dict[str, Any]:
    return asdict(ev)


def _feature_row(ev: MarketEvaluation, *, scanned_at_utc: datetime) -> dict[str, Any]:
    m = ev.market
    q = ev.quote
    o = ev.opportunity
    spread = None
    if q is not None:
        spread = q.yes_ask - q.yes_bid
    source_count = len({p.source for p in ev.forecasts}) if ev.forecasts else 0
    hours_to_end = None
    try:
        hours_to_end = (m.target_time_utc - scanned_at_utc).total_seconds() / 3600.0
    except Exception:
        hours_to_end = None
    return {
        "market_id": m.market_id,
        "event_slug": m.event_slug,
        "city": m.city,
        "station_id": m.station_id,
        "market_date_local": m.market_date_local,
        "target_time_utc": m.target_time_utc,
        "hours_to_end": hours_to_end,
        "bucket_low_f": m.bucket_low_f,
        "bucket_high_f": m.bucket_high_f,
        "status": ev.status,
        "reason": ev.reason,
        "source_count": source_count,
        "forecast_sources": sorted({p.source for p in ev.forecasts}) if ev.forecasts else [],
        "obs_temp_f": None if ev.observation is None else ev.observation.temp_f,
        "obs_time_utc": None if ev.observation is None else ev.observation.observed_at_utc,
        "yes_bid": None if q is None else q.yes_bid,
        "yes_ask": None if q is None else q.yes_ask,
        "no_bid": None if q is None else q.no_bid,
        "no_ask": None if q is None else q.no_ask,
        "depth_yes_top": None if q is None else q.depth_yes_top,
        "depth_no_top": None if q is None else q.depth_no_top,
        "quote_spread_yes": spread,
        "quote_time_utc": None if q is None else q.as_of_utc,
        "implied_yes_mid": None if o is None else o.implied_yes_mid,
        "model_prob_yes": None if o is None else o.model_prob_yes,
        "edge": None if o is None else o.edge,
        "confidence_score": None if o is None else o.confidence_score,
        "liquidity_score": None if o is None else o.liquidity_score,
        "uncertainty_score": None if o is None else o.uncertainty_score,
        "reasons": [] if o is None else list(o.reasons),
    }
