from __future__ import annotations

import json
import math
import os
from collections import defaultdict
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


def _horizon_bucket(v: Any) -> str:
    h = _to_float(v)
    if h is None:
        return "unknown"
    if h <= 2:
        return "0-2h"
    if h <= 6:
        return "2-6h"
    if h <= 12:
        return "6-12h"
    if h <= 24:
        return "12-24h"
    return "24h+"


def _prob_bucket(v: float) -> str:
    idx = min(9, max(0, int(v * 10)))
    if v >= 0.999:
        idx = 9
    return f"{idx/10:.1f}-{(idx+1)/10:.1f}"


@dataclass(frozen=True)
class BinStats:
    n: int
    mean_pred: float
    obs_rate: float
    calibrated_prob: float
    brier_raw: float
    brier_calibrated: float


def _calc_brier(preds: list[float], ys: list[int], calibrated: float | None = None) -> float:
    if not preds:
        return math.nan
    if calibrated is None:
        return sum((p - y) ** 2 for p, y in zip(preds, ys)) / len(preds)
    return sum((calibrated - y) ** 2 for y in ys) / len(ys)


def _filter_labeled_rows(rows: list[dict[str, Any]], *, restrict_status: str | None = None) -> list[dict[str, Any]]:
    labeled_rows: list[dict[str, Any]] = []
    for r in rows:
        y = _to_int(r.get("label_yes"))
        p = _to_float(r.get("model_prob_yes"))
        if y is None or p is None:
            continue
        if restrict_status and str(r.get("status") or "") != restrict_status:
            continue
        if not (0.0 <= p <= 1.0):
            continue
        labeled_rows.append(r)
    return labeled_rows


def build_calibration_profile_from_rows(
    rows: list[dict[str, Any]],
    *,
    min_bin_samples: int = 8,
    shrinkage_n: int = 25,
    restrict_status: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    labeled_rows = _filter_labeled_rows(rows, restrict_status=restrict_status)

    if not labeled_rows:
        profile = {
            "schema_version": 1,
            "meta": {
                "rows_total": len(rows),
                "rows_labeled": 0,
                "min_bin_samples": min_bin_samples,
                "shrinkage_n": shrinkage_n,
                "restrict_status": restrict_status,
            },
            "bin_adjustments": {},
        }
        return profile, {"rows_total": len(rows), "rows_labeled": 0, "profile_bins": 0}

    global_preds = [float(r["model_prob_yes"]) for r in labeled_rows]
    global_ys = [int(r["label_yes"]) for r in labeled_rows]
    global_obs = sum(global_ys) / len(global_ys)
    global_pred = sum(global_preds) / len(global_preds)

    grouped: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for r in labeled_rows:
        city = str(r.get("city") or "unknown")
        hours_to_end = _to_float(r.get("hours_to_end"))
        h_bucket = _horizon_bucket(hours_to_end)
        p = float(r["model_prob_yes"])
        y = int(r["label_yes"])
        p_bucket = _prob_bucket(p)
        keys = [
            f"city={city}|h={h_bucket}|p={p_bucket}",
            f"city={city}|h=all|p={p_bucket}",
            f"city=all|h={h_bucket}|p={p_bucket}",
            f"city=all|h=all|p={p_bucket}",
        ]
        for key in keys:
            grouped[key].append((p, y))

    bin_adjustments: dict[str, Any] = {}
    for key, pairs in sorted(grouped.items()):
        preds = [p for p, _ in pairs]
        ys = [y for _, y in pairs]
        n = len(pairs)
        mean_pred = sum(preds) / n
        obs_rate = sum(ys) / n
        if n < min_bin_samples:
            continue
        w = n / (n + max(1, shrinkage_n))
        calibrated = w * obs_rate + (1 - w) * (0.7 * global_obs + 0.3 * mean_pred)
        calibrated = max(0.001, min(0.999, calibrated))
        bin_adjustments[key] = {
            "n": n,
            "mean_pred": round(mean_pred, 6),
            "obs_rate": round(obs_rate, 6),
            "calibrated_prob": round(calibrated, 6),
        }

    profile = {
        "schema_version": 1,
        "meta": {
            "rows_total": len(rows),
            "rows_labeled": len(labeled_rows),
            "global_mean_pred": round(global_pred, 6),
            "global_obs_rate": round(global_obs, 6),
            "min_bin_samples": min_bin_samples,
            "shrinkage_n": shrinkage_n,
            "restrict_status": restrict_status,
        },
        "bin_adjustments": bin_adjustments,
    }
    result = {
        "rows_total": len(rows),
        "rows_labeled": len(labeled_rows),
        "profile_bins": len(bin_adjustments),
    }
    return profile, result


def build_calibration_profile(
    *,
    feature_rows_path: Path,
    out_profile_path: Path,
    out_report_path: Path | None = None,
    min_bin_samples: int = 8,
    shrinkage_n: int = 25,
    restrict_status: str | None = None,
) -> dict[str, Any]:
    rows = _read_jsonl(feature_rows_path)

    profile, base_result = build_calibration_profile_from_rows(
        rows,
        min_bin_samples=min_bin_samples,
        shrinkage_n=shrinkage_n,
        restrict_status=restrict_status,
    )
    labeled_rows = _filter_labeled_rows(rows, restrict_status=restrict_status)
    if not labeled_rows:
        out_profile_path.parent.mkdir(parents=True, exist_ok=True)
        out_profile_path.write_text(json.dumps(profile, indent=2, sort_keys=True), encoding="utf-8")
        if out_report_path:
            out_report_path.parent.mkdir(parents=True, exist_ok=True)
            out_report_path.write_text(json.dumps({"error": "no labeled rows"}, indent=2), encoding="utf-8")
        return base_result

    global_preds = [float(r["model_prob_yes"]) for r in labeled_rows]
    global_ys = [int(r["label_yes"]) for r in labeled_rows]
    global_obs = sum(global_ys) / len(global_ys)
    global_pred = sum(global_preds) / len(global_preds)

    report_bins: dict[str, Any] = {}
    grouped: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for r in labeled_rows:
        city = str(r.get("city") or "unknown")
        hours_to_end = _to_float(r.get("hours_to_end"))
        h_bucket = _horizon_bucket(hours_to_end)
        p = float(r["model_prob_yes"])
        y = int(r["label_yes"])
        p_bucket = _prob_bucket(p)
        for key in [
            f"city={city}|h={h_bucket}|p={p_bucket}",
            f"city={city}|h=all|p={p_bucket}",
            f"city=all|h={h_bucket}|p={p_bucket}",
            f"city=all|h=all|p={p_bucket}",
        ]:
            grouped[key].append((p, y))
    for key, pairs in sorted(grouped.items()):
        preds = [p for p, _ in pairs]
        ys = [y for _, y in pairs]
        n = len(pairs)
        mean_pred = sum(preds) / n
        obs_rate = sum(ys) / n
        if n < min_bin_samples:
            continue
        calibrated = float(((profile.get("bin_adjustments") or {}).get(key) or {}).get("calibrated_prob") or obs_rate)
        stats = BinStats(
            n=n,
            mean_pred=mean_pred,
            obs_rate=obs_rate,
            calibrated_prob=calibrated,
            brier_raw=_calc_brier(preds, ys),
            brier_calibrated=_calc_brier(preds, ys, calibrated=calibrated),
        )
        report_bins[key] = {
            **((profile.get("bin_adjustments") or {}).get(key) or {}),
            "brier_raw": round(stats.brier_raw, 6),
            "brier_calibrated": round(stats.brier_calibrated, 6),
            "brier_delta": round(stats.brier_calibrated - stats.brier_raw, 6),
        }
    out_profile_path.parent.mkdir(parents=True, exist_ok=True)
    out_profile_path.write_text(json.dumps(profile, indent=2, sort_keys=True), encoding="utf-8")

    if out_report_path:
        report = {
            "summary": {
                "rows_total": len(rows),
                "rows_labeled": len(labeled_rows),
                "profile_bins": len(bin_adjustments),
                "global_mean_pred": round(global_pred, 6),
                "global_obs_rate": round(global_obs, 6),
            },
            "best_brier_improvement_bins": sorted(
                report_bins.items(), key=lambda kv: kv[1]["brier_delta"]
            )[:50],
            "worst_brier_change_bins": sorted(
                report_bins.items(), key=lambda kv: kv[1]["brier_delta"], reverse=True
            )[:50],
        }
        out_report_path.parent.mkdir(parents=True, exist_ok=True)
        out_report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    return {
        **base_result,
        "profile_path": str(out_profile_path),
        "report_path": None if out_report_path is None else str(out_report_path),
    }


def main() -> int:
    base_dir = Path(os.getenv("WEATHER_BOT_RUNNER_BASEDIR", "data/sample"))
    feature_rows_path = Path(os.getenv("WEATHER_BOT_FEATURE_EXPORT_PATH", str(base_dir / "feature_rows_export.jsonl")))
    out_profile_path = Path(os.getenv("WEATHER_BOT_CAL_PROFILE_OUT", str(base_dir / "calibration_profile.json")))
    out_report_path = Path(os.getenv("WEATHER_BOT_CAL_PROFILE_REPORT_OUT", str(base_dir / "calibration_profile_report.json")))
    result = build_calibration_profile(
        feature_rows_path=feature_rows_path,
        out_profile_path=out_profile_path,
        out_report_path=out_report_path,
        min_bin_samples=int(os.getenv("WEATHER_BOT_CAL_MIN_BIN_SAMPLES", "8")),
        shrinkage_n=int(os.getenv("WEATHER_BOT_CAL_SHRINKAGE_N", "25")),
        restrict_status=(os.getenv("WEATHER_BOT_CAL_RESTRICT_STATUS") or None),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
