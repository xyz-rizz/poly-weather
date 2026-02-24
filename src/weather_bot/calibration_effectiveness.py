from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from weather_bot.core.calibration_profile import calibrate_probability_from_profile


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


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    return i if i in (0, 1) else None


def _horizon_bucket(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours <= 2:
        return "0-2h"
    if hours <= 6:
        return "2-6h"
    if hours <= 12:
        return "6-12h"
    if hours <= 24:
        return "12-24h"
    return "24h+"


def _prob_decile(p: float) -> str:
    i = min(9, max(0, int(p * 10)))
    if p >= 0.999:
        i = 9
    return f"{i/10:.1f}-{(i+1)/10:.1f}"


@dataclass(frozen=True)
class MetricSummary:
    n: int
    brier: float
    log_loss: float
    ece: float
    hit_rate: float
    mean_pred: float
    mean_obs: float


def _summarize(points: list[dict[str, Any]], pred_key: str) -> MetricSummary:
    if not points:
        return MetricSummary(0, math.nan, math.nan, math.nan, math.nan, math.nan, math.nan)
    preds: list[float] = []
    ys: list[int] = []
    hits = 0
    for p in points:
        pr = _to_float(p.get(pred_key))
        y = _to_int(p.get("label_yes"))
        if pr is None or y is None:
            continue
        pr = max(1e-6, min(1 - 1e-6, pr))
        preds.append(pr)
        ys.append(y)
        if bool(p.get("profile_hit")):
            hits += 1
    n = len(preds)
    if n == 0:
        return MetricSummary(0, math.nan, math.nan, math.nan, math.nan, math.nan, math.nan)
    brier = sum((pr - y) ** 2 for pr, y in zip(preds, ys)) / n
    log_loss = sum(-(y * math.log(pr) + (1 - y) * math.log(1 - pr)) for pr, y in zip(preds, ys)) / n
    ece = _ece(preds, ys)
    return MetricSummary(
        n=n,
        brier=brier,
        log_loss=log_loss,
        ece=ece,
        hit_rate=hits / n,
        mean_pred=sum(preds) / n,
        mean_obs=sum(ys) / n,
    )


def _ece(preds: list[float], ys: list[int], bins: int = 10) -> float:
    n = len(preds)
    if n == 0:
        return math.nan
    grouped: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for pr, y in zip(preds, ys):
        idx = min(bins - 1, max(0, int(pr * bins)))
        grouped[idx].append((pr, y))
    total = 0.0
    for bucket in grouped.values():
        p_mean = sum(pr for pr, _ in bucket) / len(bucket)
        y_mean = sum(y for _, y in bucket) / len(bucket)
        total += (len(bucket) / n) * abs(p_mean - y_mean)
    return total


def _safe_summary(s: MetricSummary) -> dict[str, Any]:
    def safe(v: float) -> float | None:
        return None if isinstance(v, float) and math.isnan(v) else round(v, 6)

    return {
        "n": s.n,
        "brier": safe(s.brier),
        "log_loss": safe(s.log_loss),
        "ece": safe(s.ece),
        "hit_rate": safe(s.hit_rate),
        "mean_pred": safe(s.mean_pred),
        "mean_obs": safe(s.mean_obs),
    }


def calibration_effectiveness_report(
    *,
    feature_rows_path: Path,
    profile_path: Path,
    restrict_status: str | None = None,
) -> dict[str, Any]:
    rows = _read_jsonl(feature_rows_path)
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception:
        profile = None

    labeled: list[dict[str, Any]] = []
    for r in rows:
        y = _to_int(r.get("label_yes"))
        raw = _to_float(r.get("model_prob_yes"))
        hours_to_end = _to_float(r.get("hours_to_end"))
        if y is None or raw is None:
            continue
        if restrict_status and str(r.get("status") or "") != restrict_status:
            continue
        if hours_to_end is None or hours_to_end < 0.0:
            continue
        city = str(r.get("city") or "unknown")
        adj = calibrate_probability_from_profile(profile, raw_prob=raw, city=city, hours_to_end=hours_to_end)
        row = dict(r)
        row["raw_prob"] = raw
        row["calibrated_prob"] = adj.calibrated_prob
        row["profile_hit"] = adj.profile_hit
        row["profile_key"] = adj.profile_key
        row["horizon_bucket"] = _horizon_bucket(hours_to_end)
        row["raw_prob_bucket"] = _prob_decile(raw)
        labeled.append(row)

    overall_raw = _summarize(labeled, "raw_prob")
    overall_cal = _summarize(labeled, "calibrated_prob")

    by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_horizon: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_status: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in labeled:
        by_city[str(r.get("city") or "unknown")].append(r)
        by_horizon[str(r.get("horizon_bucket") or "unknown")].append(r)
        by_status[str(r.get("status") or "unknown")].append(r)

    profile_hit_examples = []
    for r in labeled:
        if r.get("profile_hit"):
            profile_hit_examples.append(
                {
                    "market_id": r.get("market_id"),
                    "city": r.get("city"),
                    "status": r.get("status"),
                    "raw_prob": round(float(r["raw_prob"]), 6),
                    "calibrated_prob": round(float(r["calibrated_prob"]), 6),
                    "label_yes": int(r["label_yes"]),
                    "hours_to_end": r.get("hours_to_end"),
                    "profile_key": r.get("profile_key"),
                }
            )
        if len(profile_hit_examples) >= 20:
            break

    return {
        "summary": {
            "rows_total": len(rows),
            "rows_labeled": len(labeled),
            "restrict_status": restrict_status,
            "profile_loaded": isinstance(profile, dict),
            "profile_bins": len((profile or {}).get("bin_adjustments") or {}),
            "profile_hit_count": sum(1 for r in labeled if r.get("profile_hit")),
        },
        "overall": {
            "raw": _safe_summary(overall_raw),
            "calibrated": _safe_summary(overall_cal),
            "delta": {
                "brier": _delta(overall_cal.brier, overall_raw.brier),
                "log_loss": _delta(overall_cal.log_loss, overall_raw.log_loss),
                "ece": _delta(overall_cal.ece, overall_raw.ece),
            },
        },
        "by_city": _segment_compare(by_city),
        "by_horizon": _segment_compare(by_horizon),
        "by_status": _segment_compare(by_status),
        "profile_hit_examples": profile_hit_examples,
    }


def _delta(new: float, old: float) -> float | None:
    if any(isinstance(v, float) and math.isnan(v) for v in (new, old)):
        return None
    return round(new - old, 6)


def _segment_compare(groups: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, points in sorted(groups.items()):
        raw = _summarize(points, "raw_prob")
        cal = _summarize(points, "calibrated_prob")
        out[k] = {
            "raw": _safe_summary(raw),
            "calibrated": _safe_summary(cal),
            "delta": {
                "brier": _delta(cal.brier, raw.brier),
                "log_loss": _delta(cal.log_loss, raw.log_loss),
                "ece": _delta(cal.ece, raw.ece),
            },
        }
    return out


def main() -> int:
    base_dir = Path(os.getenv("WEATHER_BOT_RUNNER_BASEDIR", "data/sample"))
    feature_rows_path = Path(os.getenv("WEATHER_BOT_FEATURE_EXPORT_PATH", str(base_dir / "feature_rows_export.jsonl")))
    profile_path = Path(os.getenv("WEATHER_BOT_CAL_PROFILE_OUT", str(base_dir / "calibration_profile.json")))
    report = calibration_effectiveness_report(
        feature_rows_path=feature_rows_path,
        profile_path=profile_path,
        restrict_status=(os.getenv("WEATHER_BOT_CAL_RESTRICT_STATUS") or None),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
