from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from weather_bot.adapters.live_market import build_daily_weather_event_slugs
from weather_bot.core.universe import get_universe
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


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    txt = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(txt).astimezone(UTC)
    except ValueError:
        return None


def _parse_outcome_prices(value: Any) -> list[float] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return None
    if not isinstance(value, list):
        return None
    try:
        return [float(x) for x in value]
    except Exception:
        return None


@dataclass(frozen=True)
class SettledBucketOutcome:
    event_slug: str
    market_id: str
    city: str
    station_id: str
    market_date_local: str
    outcome_yes: int  # 1 for winning YES bucket, 0 otherwise
    closed: bool
    end_date_utc: datetime | None


@dataclass(frozen=True)
class CalibrationPoint:
    market_id: str
    event_slug: str
    city: str
    predicted_prob_yes: float
    outcome_yes: int
    snapshot_time_utc: datetime
    market_end_utc: datetime | None
    hours_to_end: float | None


@dataclass(frozen=True)
class CalibrationSummary:
    n: int
    brier: float
    log_loss: float


def fetch_settled_weather_outcomes(
    *,
    universe_level: str = "tier1",
    days_back: int = 14,
    include_today: bool = False,
    http_client: JsonHttpClient | None = None,
) -> list[SettledBucketOutcome]:
    http = http_client or JsonHttpClient()
    universe = get_universe(universe_level)
    today = datetime.now(UTC).date()
    outcomes: list[SettledBucketOutcome] = []
    for i in range(0 if include_today else 1, days_back + 1):
        d = today - timedelta(days=i)
        slugs = build_daily_weather_event_slugs(universe, center_date_utc=d, days_before=0, days_after=0)
        for slug in slugs:
            url = f"https://gamma-api.polymarket.com/events?slug={slug}"
            try:
                payload = http.get_json(url)
            except Exception:
                continue
            events = payload if isinstance(payload, list) else payload.get("data", [])
            if not events:
                continue
            event = events[0]
            markets = event.get("markets") or []
            if not isinstance(markets, list):
                continue
            event_title = str(event.get("title") or "")
            event_desc = str(event.get("description") or "")
            city = _extract_city_from_event_text(event_title + " " + event_desc)
            station_id = _extract_station_from_text(event_desc) or _extract_station_from_text(str(event.get("resolutionSource") or ""))
            market_date_local = _extract_market_date_from_title(event_title) or ""
            for m in markets:
                if not isinstance(m, dict):
                    continue
                prices = _parse_outcome_prices(m.get("outcomePrices"))
                if not prices or len(prices) < 2:
                    continue
                closed = bool(m.get("closed"))
                if not closed:
                    continue
                yes_outcome = 1 if prices[0] >= 0.999 else 0
                if prices[0] <= 0.001:
                    yes_outcome = 0
                elif prices[0] < 0.999:
                    # unresolved/proposed in-between values: skip
                    continue
                outcomes.append(
                    SettledBucketOutcome(
                        event_slug=str(event.get("slug") or slug),
                        market_id=str(m.get("id") or ""),
                        city=city or "",
                        station_id=station_id or "",
                        market_date_local=market_date_local,
                        outcome_yes=yes_outcome,
                        closed=closed,
                        end_date_utc=_parse_iso_dt(m.get("endDate") or m.get("endDateIso")),
                    )
                )
    return outcomes


def extract_latest_predictions_before_end(scan_snapshot_path: Path) -> list[CalibrationPoint]:
    rows = _read_jsonl(scan_snapshot_path)
    by_market: dict[str, CalibrationPoint] = {}
    for row in rows:
        scan_time = _parse_iso_dt((row.get("scan_result") or {}).get("scanned_at_utc") or row.get("created_at_utc"))
        if scan_time is None:
            continue
        for ev in (row.get("scan_result") or {}).get("evaluations", []):
            opp = ev.get("opportunity")
            market = ev.get("market") or {}
            if not isinstance(market, dict) or not isinstance(opp, dict):
                continue
            market_id = str(market.get("market_id") or "")
            if not market_id:
                continue
            p = opp.get("model_prob_yes")
            if not isinstance(p, (int, float)):
                continue
            end_utc = _parse_iso_dt(market.get("target_time_utc"))
            if end_utc is not None and scan_time > end_utc:
                continue
            prev = by_market.get(market_id)
            if prev is None or scan_time > prev.snapshot_time_utc:
                hours_to_end = ((end_utc - scan_time).total_seconds() / 3600.0) if end_utc else None
                by_market[market_id] = CalibrationPoint(
                    market_id=market_id,
                    event_slug=str(market.get("event_slug") or ""),
                    city=str(market.get("city") or ""),
                    predicted_prob_yes=float(max(0.001, min(0.999, p))),
                    outcome_yes=-1,
                    snapshot_time_utc=scan_time,
                    market_end_utc=end_utc,
                    hours_to_end=hours_to_end,
                )
    return list(by_market.values())


def join_predictions_with_outcomes(
    predictions: list[CalibrationPoint],
    outcomes: list[SettledBucketOutcome],
) -> list[CalibrationPoint]:
    outcome_by_market = {o.market_id: o for o in outcomes if o.market_id}
    joined: list[CalibrationPoint] = []
    for p in predictions:
        out = outcome_by_market.get(p.market_id)
        if out is None:
            continue
        joined.append(
            CalibrationPoint(
                market_id=p.market_id,
                event_slug=p.event_slug or out.event_slug,
                city=p.city or out.city,
                predicted_prob_yes=p.predicted_prob_yes,
                outcome_yes=out.outcome_yes,
                snapshot_time_utc=p.snapshot_time_utc,
                market_end_utc=p.market_end_utc or out.end_date_utc,
                hours_to_end=p.hours_to_end,
            )
        )
    return joined


def summarize_calibration(points: list[CalibrationPoint]) -> CalibrationSummary:
    if not points:
        return CalibrationSummary(n=0, brier=math.nan, log_loss=math.nan)
    brier = 0.0
    log_loss = 0.0
    for p in points:
        y = p.outcome_yes
        pr = max(1e-6, min(1 - 1e-6, p.predicted_prob_yes))
        brier += (pr - y) ** 2
        log_loss += -(y * math.log(pr) + (1 - y) * math.log(1 - pr))
    n = len(points)
    return CalibrationSummary(n=n, brier=brier / n, log_loss=log_loss / n)


def _summary_to_json(summary: CalibrationSummary) -> dict[str, Any]:
    def _safe(x: float) -> float | None:
        return None if isinstance(x, float) and math.isnan(x) else x

    return {"n": summary.n, "brier": _safe(summary.brier), "log_loss": _safe(summary.log_loss)}


def calibration_report(
    *,
    scan_snapshot_path: Path,
    universe_level: str = "tier1",
    days_back: int = 14,
    insecure_ssl: bool = False,
) -> dict[str, Any]:
    http = JsonHttpClient(verify_ssl=not insecure_ssl)
    outcomes = fetch_settled_weather_outcomes(
        universe_level=universe_level,
        days_back=days_back,
        include_today=False,
        http_client=http,
    )
    preds = extract_latest_predictions_before_end(scan_snapshot_path)
    joined = join_predictions_with_outcomes(preds, outcomes)

    by_city: dict[str, list[CalibrationPoint]] = defaultdict(list)
    by_horizon: dict[str, list[CalibrationPoint]] = defaultdict(list)
    for p in joined:
        by_city[p.city or "unknown"].append(p)
        h = p.hours_to_end
        if h is None:
            bucket = "unknown"
        elif h <= 2:
            bucket = "0-2h"
        elif h <= 6:
            bucket = "2-6h"
        elif h <= 12:
            bucket = "6-12h"
        else:
            bucket = "12h+"
        by_horizon[bucket].append(p)

    return {
        "counts": {
            "predictions": len(preds),
            "settled_outcomes": len(outcomes),
            "matched_points": len(joined),
        },
        "overall": _summary_to_json(summarize_calibration(joined)),
        "by_city": {k: _summary_to_json(summarize_calibration(v)) for k, v in sorted(by_city.items())},
        "by_horizon": {k: _summary_to_json(summarize_calibration(v)) for k, v in sorted(by_horizon.items())},
        "notes": (
            ["No matched calibration points yet. Record scans over multiple days and rerun after those markets resolve."]
            if not joined
            else []
        ),
        "sample_points": [
            {
                "market_id": p.market_id,
                "event_slug": p.event_slug,
                "city": p.city,
                "predicted_prob_yes": round(p.predicted_prob_yes, 4),
                "outcome_yes": p.outcome_yes,
                "hours_to_end": None if p.hours_to_end is None else round(p.hours_to_end, 2),
            }
            for p in joined[:20]
        ],
    }


def _extract_city_from_event_text(text: str) -> str | None:
    t = text.lower()
    for raw, city in [
        ("new york city", "NYC"),
        ("nyc", "NYC"),
        ("atlanta", "Atlanta"),
        ("dallas", "Dallas"),
        ("chicago", "Chicago"),
        ("seattle", "Seattle"),
        ("miami", "Miami"),
    ]:
        if raw in t:
            return city
    return None


def _extract_station_from_text(text: str) -> str | None:
    import re

    m = re.search(r"\b([A-Z]{4})\b", text or "")
    return m.group(1) if m else None


def _extract_market_date_from_title(title: str) -> str | None:
    import re

    m = re.search(r"on ([A-Za-z]+) (\d{1,2})(?:,? (\d{4}))?", title)
    if not m:
        return None
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    month = months.get(m.group(1).lower())
    if month is None:
        return None
    year = int(m.group(3)) if m.group(3) else datetime.now(UTC).year
    d = date(year, month, int(m.group(2)))
    return d.isoformat()
