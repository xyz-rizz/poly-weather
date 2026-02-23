from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from weather_bot.calibration_profile_build import build_calibration_profile_from_rows
from weather_bot.core.calibration_profile import calibrate_probability_from_profile


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


def _to_int(v: Any) -> int | None:
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    return i if i in (0, 1) else None


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _labeled_rows(rows: list[dict[str, Any]], *, restrict_status: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        if restrict_status and str(r.get("status") or "") != restrict_status:
            continue
        if _to_int(r.get("label_yes")) is None:
            continue
        if _to_float(r.get("model_prob_yes")) is None:
            continue
        ts = _parse_iso(r.get("snapshot_time_utc"))
        if ts is None:
            continue
        row = dict(r)
        row["_snapshot_dt"] = ts
        out.append(row)
    out.sort(key=lambda r: r["_snapshot_dt"])
    return out


def _metrics(points: list[dict[str, Any]], pred_key: str) -> dict[str, Any]:
    preds: list[float] = []
    ys: list[int] = []
    for p in points:
        pr = _to_float(p.get(pred_key))
        y = _to_int(p.get("label_yes"))
        if pr is None or y is None:
            continue
        pr = max(1e-6, min(1 - 1e-6, pr))
        preds.append(pr)
        ys.append(y)
    if not preds:
        return {"n": 0, "brier": None, "log_loss": None, "ece": None}
    n = len(preds)
    brier = sum((pr - y) ** 2 for pr, y in zip(preds, ys)) / n
    log_loss = sum(-(y * math.log(pr) + (1 - y) * math.log(1 - pr)) for pr, y in zip(preds, ys)) / n
    ece = _ece(preds, ys)
    return {"n": n, "brier": round(brier, 6), "log_loss": round(log_loss, 6), "ece": round(ece, 6)}


def _ece(preds: list[float], ys: list[int], bins: int = 10) -> float:
    n = len(preds)
    bucketed: dict[int, list[tuple[float, int]]] = {}
    for pr, y in zip(preds, ys):
        idx = min(bins - 1, max(0, int(pr * bins)))
        bucketed.setdefault(idx, []).append((pr, y))
    total = 0.0
    for vals in bucketed.values():
        p_mean = sum(p for p, _ in vals) / len(vals)
        y_mean = sum(y for _, y in vals) / len(vals)
        total += (len(vals) / n) * abs(p_mean - y_mean)
    return total


def walkforward_calibration_report(
    *,
    feature_rows_path: Path,
    out_path: Path | None = None,
    folds: int = 4,
    min_train_rows: int = 100,
    min_bin_samples: int = 8,
    shrinkage_n: int = 25,
    restrict_status: str | None = None,
) -> dict[str, Any]:
    rows = _read_jsonl(feature_rows_path)
    labeled = _labeled_rows(rows, restrict_status=restrict_status)
    if not labeled:
        report = {
            "summary": {"rows_total": len(rows), "rows_labeled": 0, "folds_requested": folds, "folds_run": 0},
            "folds": [],
        }
        if out_path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        return report

    fold_rows = max(1, len(labeled) // max(1, folds))
    results: list[dict[str, Any]] = []
    for i in range(1, folds + 1):
        test_start = (i - 1) * fold_rows
        test_end = len(labeled) if i == folds else min(len(labeled), i * fold_rows)
        if test_start >= len(labeled):
            break
        train = labeled[:test_start]
        test = labeled[test_start:test_end]
        if len(train) < min_train_rows or not test:
            continue
        profile, profile_meta = build_calibration_profile_from_rows(
            train,
            min_bin_samples=min_bin_samples,
            shrinkage_n=shrinkage_n,
            restrict_status=restrict_status,
        )
        scored_test: list[dict[str, Any]] = []
        hit_count = 0
        for r in test:
            raw = float(r["model_prob_yes"])
            city = str(r.get("city") or "unknown")
            hours_to_end = _to_float(r.get("hours_to_end"))
            adj = calibrate_probability_from_profile(profile, raw_prob=raw, city=city, hours_to_end=hours_to_end)
            x = dict(r)
            x["raw_prob"] = raw
            x["calibrated_prob"] = adj.calibrated_prob
            x["profile_hit"] = adj.profile_hit
            scored_test.append(x)
            if adj.profile_hit:
                hit_count += 1
        raw_m = _metrics(scored_test, "raw_prob")
        cal_m = _metrics(scored_test, "calibrated_prob")
        results.append(
            {
                "fold": i,
                "train_rows": len(train),
                "test_rows": len(test),
                "profile_bins": int(profile_meta.get("profile_bins") or 0),
                "profile_hit_rate_test": round(hit_count / max(1, len(scored_test)), 6),
                "train_start_utc": train[0]["_snapshot_dt"].isoformat() if train else None,
                "train_end_utc": train[-1]["_snapshot_dt"].isoformat() if train else None,
                "test_start_utc": test[0]["_snapshot_dt"].isoformat(),
                "test_end_utc": test[-1]["_snapshot_dt"].isoformat(),
                "raw": raw_m,
                "calibrated": cal_m,
                "delta": {
                    "brier": _delta(cal_m.get("brier"), raw_m.get("brier")),
                    "log_loss": _delta(cal_m.get("log_loss"), raw_m.get("log_loss")),
                    "ece": _delta(cal_m.get("ece"), raw_m.get("ece")),
                },
            }
        )

    agg = _aggregate_fold_metrics(results)
    report = {
        "summary": {
            "rows_total": len(rows),
            "rows_labeled": len(labeled),
            "folds_requested": folds,
            "folds_run": len(results),
            "min_train_rows": min_train_rows,
            "min_bin_samples": min_bin_samples,
            "shrinkage_n": shrinkage_n,
            "restrict_status": restrict_status,
        },
        "aggregate": agg,
        "folds": results,
    }
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def _delta(new: Any, old: Any) -> float | None:
    try:
        if new is None or old is None:
            return None
        return round(float(new) - float(old), 6)
    except Exception:
        return None


def _aggregate_fold_metrics(folds: list[dict[str, Any]]) -> dict[str, Any]:
    if not folds:
        return {"raw": {}, "calibrated": {}, "delta": {}}
    keys = ["brier", "log_loss", "ece"]
    out: dict[str, Any] = {"raw": {}, "calibrated": {}, "delta": {}}
    for section in ["raw", "calibrated", "delta"]:
        for k in keys:
            vals = [f.get(section, {}).get(k) for f in folds]
            vals = [float(v) for v in vals if isinstance(v, (int, float))]
            out[section][k] = round(sum(vals) / len(vals), 6) if vals else None
    out["folds_with_improved_brier"] = sum(
        1 for f in folds if isinstance(f.get("delta", {}).get("brier"), (int, float)) and f["delta"]["brier"] < 0
    )
    return out


def main() -> int:
    base_dir = Path(os.getenv("WEATHER_BOT_RUNNER_BASEDIR", "data/sample"))
    feature_rows_path = Path(os.getenv("WEATHER_BOT_FEATURE_EXPORT_PATH", str(base_dir / "feature_rows_export.jsonl")))
    out_path = Path(os.getenv("WEATHER_BOT_WALKFORWARD_OUT", str(base_dir / "calibration_walkforward_report.json")))
    report = walkforward_calibration_report(
        feature_rows_path=feature_rows_path,
        out_path=out_path,
        folds=int(os.getenv("WEATHER_BOT_WALKFORWARD_FOLDS", "4")),
        min_train_rows=int(os.getenv("WEATHER_BOT_WALKFORWARD_MIN_TRAIN_ROWS", "100")),
        min_bin_samples=int(os.getenv("WEATHER_BOT_CAL_MIN_BIN_SAMPLES", "8")),
        shrinkage_n=int(os.getenv("WEATHER_BOT_CAL_SHRINKAGE_N", "25")),
        restrict_status=(os.getenv("WEATHER_BOT_CAL_RESTRICT_STATUS") or None),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
