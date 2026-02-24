from __future__ import annotations

import json
import os
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


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    txt = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(txt).astimezone(UTC)
    except ValueError:
        return None


def _fallback_feature_rows_from_evaluations(evaluations: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in evaluations:
        if not isinstance(ev, dict):
            continue
        market = ev.get("market") or {}
        quote = ev.get("quote") or {}
        opp = ev.get("opportunity") or {}
        forecasts = ev.get("forecasts") or []
        obs = ev.get("observation") or {}
        if not isinstance(market, dict):
            continue
        sources = sorted(
            {
                str(f.get("source"))
                for f in forecasts
                if isinstance(f, dict) and isinstance(f.get("source"), str) and f.get("source")
            }
        )
        yes_bid = quote.get("yes_bid") if isinstance(quote, dict) else None
        yes_ask = quote.get("yes_ask") if isinstance(quote, dict) else None
        spread = None
        if isinstance(yes_bid, (int, float)) and isinstance(yes_ask, (int, float)):
            spread = float(yes_ask) - float(yes_bid)
        out.append(
            {
                "market_id": str(market.get("market_id") or ""),
                "event_slug": str(market.get("event_slug") or ""),
                "city": market.get("city"),
                "station_id": market.get("station_id"),
                "market_date_local": market.get("market_date_local"),
                "target_time_utc": market.get("target_time_utc"),
                "bucket_low_f": market.get("bucket_low_f"),
                "bucket_high_f": market.get("bucket_high_f"),
                "status": ev.get("status"),
                "reason": ev.get("reason"),
                "source_count": len(sources),
                "forecast_sources": sources,
                "obs_temp_f": obs.get("temp_f") if isinstance(obs, dict) else None,
                "obs_time_utc": obs.get("observed_at_utc") if isinstance(obs, dict) else None,
                "yes_bid": quote.get("yes_bid") if isinstance(quote, dict) else None,
                "yes_ask": quote.get("yes_ask") if isinstance(quote, dict) else None,
                "no_bid": quote.get("no_bid") if isinstance(quote, dict) else None,
                "no_ask": quote.get("no_ask") if isinstance(quote, dict) else None,
                "depth_yes_top": quote.get("depth_yes_top") if isinstance(quote, dict) else None,
                "depth_no_top": quote.get("depth_no_top") if isinstance(quote, dict) else None,
                "quote_spread_yes": spread,
                "quote_time_utc": quote.get("as_of_utc") if isinstance(quote, dict) else None,
                "implied_yes_mid": opp.get("implied_yes_mid") if isinstance(opp, dict) else None,
                "model_prob_yes": opp.get("model_prob_yes") if isinstance(opp, dict) else None,
                "edge": opp.get("edge") if isinstance(opp, dict) else None,
                "confidence_score": opp.get("confidence_score") if isinstance(opp, dict) else None,
                "liquidity_score": opp.get("liquidity_score") if isinstance(opp, dict) else None,
                "uncertainty_score": opp.get("uncertainty_score") if isinstance(opp, dict) else None,
                "reasons": list(opp.get("reasons") or []) if isinstance(opp, dict) else [],
            }
        )
    return out


def export_feature_rows(
    *,
    base_dir: Path,
    out_path: Path,
    strategy_id: str | None = None,
    scan_mode: str | None = None,
    latest_only: bool = False,
    attach_outcomes: bool = False,
    universe_level: str = "tier1",
    days_back: int = 30,
    insecure_ssl: bool = False,
    label_min_hours_to_end: float = 0.0,
) -> dict[str, Any]:
    rows = _read_jsonl(base_dir / "scan_snapshots.jsonl")
    rows = [r for r in rows if r.get("event_type") == "scan_snapshot"]
    if strategy_id:
        rows = [r for r in rows if r.get("strategy_id") == strategy_id]
    if scan_mode:
        rows = [r for r in rows if r.get("scan_mode") == scan_mode]

    flat: list[dict[str, Any]] = []
    for row in rows:
        scan_result = row.get("scan_result") or {}
        scanned_at = scan_result.get("scanned_at_utc") or row.get("created_at_utc")
        features = scan_result.get("feature_rows") or _fallback_feature_rows_from_evaluations(scan_result.get("evaluations") or [])
        for fr in features:
            if not isinstance(fr, dict):
                continue
            market_id = str(fr.get("market_id") or "")
            event_slug = str(fr.get("event_slug") or "")
            out = dict(fr)
            out["snapshot_time_utc"] = scanned_at
            out["snapshot_created_at_utc"] = row.get("created_at_utc")
            out["scan_mode"] = row.get("scan_mode")
            out["strategy_id"] = row.get("strategy_id")
            out["run_id"] = ((row.get("run_meta") or {}).get("run_id"))
            out["scan_seq"] = ((row.get("run_meta") or {}).get("scan_seq"))
            out["market_id"] = market_id
            out["event_slug"] = event_slug
            snap_dt = _parse_iso(scanned_at)
            tgt_dt = _parse_iso(out.get("target_time_utc"))
            out["hours_to_end"] = None
            if snap_dt is not None and tgt_dt is not None:
                out["hours_to_end"] = round((tgt_dt - snap_dt).total_seconds() / 3600.0, 6)
            flat.append(out)

    snapshot_times = sorted(
        [dt.isoformat() for dt in (_parse_iso(fr.get("snapshot_time_utc")) for fr in flat) if dt is not None]
    )
    target_times = sorted(
        [dt.isoformat() for dt in (_parse_iso(fr.get("target_time_utc")) for fr in flat) if dt is not None]
    )
    distinct_market_ids = {str(fr.get("market_id") or "") for fr in flat if str(fr.get("market_id") or "")}

    if latest_only:
        by_market: dict[str, dict[str, Any]] = {}
        for fr in flat:
            market_id = str(fr.get("market_id") or "")
            if not market_id:
                continue
            ts = _parse_iso(fr.get("snapshot_time_utc"))
            prev = by_market.get(market_id)
            prev_ts = _parse_iso(prev.get("snapshot_time_utc")) if prev else None
            if prev is None or (ts and (prev_ts is None or ts > prev_ts)):
                by_market[market_id] = fr
        flat = list(by_market.values())

    outcome_count = 0
    matched_market_ids: set[str] = set()
    resolved_market_ids_available = 0
    label_skipped_post_target = 0
    label_skipped_missing_time = 0
    if attach_outcomes:
        http = JsonHttpClient(verify_ssl=not insecure_ssl)
        outcomes = fetch_settled_weather_outcomes(
            universe_level=universe_level,
            days_back=days_back,
            include_today=False,
            http_client=http,
        )
        outcome_by_market = {o.market_id: o for o in outcomes if o.market_id}
        resolved_market_ids_available = len(outcome_by_market)
        for fr in flat:
            market_id = str(fr.get("market_id") or "")
            out = outcome_by_market.get(market_id)
            if out is None:
                fr["label_yes"] = None
                fr["resolved"] = False
                continue
            hrs = fr.get("hours_to_end")
            hrs_f = None
            try:
                hrs_f = None if hrs is None else float(hrs)
            except Exception:
                hrs_f = None
            if hrs_f is None:
                fr["label_yes"] = None
                fr["resolved"] = False
                fr["label_skip_reason"] = "missing_hours_to_end"
                label_skipped_missing_time += 1
                continue
            if hrs_f < float(label_min_hours_to_end):
                fr["label_yes"] = None
                fr["resolved"] = False
                fr["label_skip_reason"] = "post_target_snapshot"
                label_skipped_post_target += 1
                continue
            fr["label_yes"] = out.outcome_yes
            fr["resolved"] = True
            fr["resolved_event_slug"] = out.event_slug
            fr["resolved_city"] = out.city
            fr["resolved_station_id"] = out.station_id
            fr["resolved_market_date_local"] = out.market_date_local
            fr["resolved_end_utc"] = None if out.end_date_utc is None else out.end_date_utc.isoformat()
            outcome_count += 1
            matched_market_ids.add(market_id)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for fr in flat:
            fh.write(json.dumps(fr, separators=(",", ":"), default=str) + "\n")

    return {
        "rows_in": len(rows),
        "feature_rows_out": len(flat),
        "latest_only": latest_only,
        "attach_outcomes": attach_outcomes,
        "rows_with_outcome": outcome_count,
        "distinct_markets": len(distinct_market_ids),
        "matched_market_ids": len(matched_market_ids),
        "resolved_market_ids_available": resolved_market_ids_available,
        "label_min_hours_to_end": label_min_hours_to_end,
        "label_skipped_post_target": label_skipped_post_target,
        "label_skipped_missing_time": label_skipped_missing_time,
        "snapshot_time_min": snapshot_times[0] if snapshot_times else None,
        "snapshot_time_max": snapshot_times[-1] if snapshot_times else None,
        "target_time_min": target_times[0] if target_times else None,
        "target_time_max": target_times[-1] if target_times else None,
        "output_path": str(out_path),
    }


def main() -> int:
    base_dir = Path(os.getenv("WEATHER_BOT_RUNNER_BASEDIR", "data/sample"))
    out_path = Path(os.getenv("WEATHER_BOT_FEATURE_EXPORT_PATH", str(base_dir / "feature_rows_export.jsonl")))
    result = export_feature_rows(
        base_dir=base_dir,
        out_path=out_path,
        strategy_id=os.getenv("WEATHER_BOT_STRATEGY_ID") or None,
        scan_mode=os.getenv("WEATHER_BOT_SCAN_MODE") or None,
        latest_only=os.getenv("WEATHER_BOT_FEATURE_LATEST_ONLY", "0").strip().lower() in {"1", "true", "yes"},
        attach_outcomes=os.getenv("WEATHER_BOT_FEATURE_ATTACH_OUTCOMES", "0").strip().lower() in {"1", "true", "yes"},
        universe_level=os.getenv("WEATHER_BOT_UNIVERSE", "tier1"),
        days_back=int(os.getenv("WEATHER_BOT_CAL_DAYS_BACK", "30")),
        insecure_ssl=os.getenv("WEATHER_BOT_INSECURE_SSL", "0").strip().lower() in {"1", "true", "yes"},
        label_min_hours_to_end=float(os.getenv("WEATHER_BOT_FEATURE_LABEL_MIN_HOURS_TO_END", "0")),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
