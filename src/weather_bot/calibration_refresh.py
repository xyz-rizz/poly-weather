from __future__ import annotations

import json
import os
from pathlib import Path

from weather_bot.calibration_effectiveness import calibration_effectiveness_report
from weather_bot.calibration_profile_build import build_calibration_profile
from weather_bot.calibration_walkforward import walkforward_calibration_report
from weather_bot.feature_export import export_feature_rows
from weather_bot.threshold_sweep import threshold_sweep_report


def main() -> int:
    base_dir = Path(os.getenv("WEATHER_BOT_RUNNER_BASEDIR", "data/sample"))
    feature_rows_path = Path(os.getenv("WEATHER_BOT_FEATURE_EXPORT_PATH", str(base_dir / "feature_rows_export.jsonl")))
    profile_path = Path(os.getenv("WEATHER_BOT_CAL_PROFILE_OUT", str(base_dir / "calibration_profile.json")))
    profile_report_path = Path(
        os.getenv("WEATHER_BOT_CAL_PROFILE_REPORT_OUT", str(base_dir / "calibration_profile_report.json"))
    )
    effectiveness_path = Path(
        os.getenv("WEATHER_BOT_CAL_EFFECTIVENESS_OUT", str(base_dir / "calibration_effectiveness_report.json"))
    )
    threshold_sweep_path = Path(
        os.getenv("WEATHER_BOT_THRESHOLD_SWEEP_OUT", str(base_dir / "threshold_sweep_report.json"))
    )
    walkforward_path = Path(
        os.getenv("WEATHER_BOT_WALKFORWARD_OUT", str(base_dir / "calibration_walkforward_report.json"))
    )

    export_result = export_feature_rows(
        base_dir=base_dir,
        out_path=feature_rows_path,
        strategy_id=os.getenv("WEATHER_BOT_STRATEGY_ID") or None,
        scan_mode=os.getenv("WEATHER_BOT_SCAN_MODE") or None,
        latest_only=os.getenv("WEATHER_BOT_FEATURE_LATEST_ONLY", "0").strip().lower() in {"1", "true", "yes"},
        attach_outcomes=os.getenv("WEATHER_BOT_FEATURE_ATTACH_OUTCOMES", "1").strip().lower() in {"1", "true", "yes"},
        universe_level=os.getenv("WEATHER_BOT_UNIVERSE", "tier1"),
        days_back=int(os.getenv("WEATHER_BOT_CAL_DAYS_BACK", "30")),
        insecure_ssl=os.getenv("WEATHER_BOT_INSECURE_SSL", "0").strip().lower() in {"1", "true", "yes"},
    )

    build_result = build_calibration_profile(
        feature_rows_path=feature_rows_path,
        out_profile_path=profile_path,
        out_report_path=profile_report_path,
        min_bin_samples=int(os.getenv("WEATHER_BOT_CAL_MIN_BIN_SAMPLES", "8")),
        shrinkage_n=int(os.getenv("WEATHER_BOT_CAL_SHRINKAGE_N", "25")),
        restrict_status=(os.getenv("WEATHER_BOT_CAL_RESTRICT_STATUS") or None),
    )

    effectiveness = calibration_effectiveness_report(
        feature_rows_path=feature_rows_path,
        profile_path=profile_path,
        restrict_status=(os.getenv("WEATHER_BOT_CAL_RESTRICT_STATUS") or None),
    )
    effectiveness_path.parent.mkdir(parents=True, exist_ok=True)
    effectiveness_path.write_text(json.dumps(effectiveness, indent=2, sort_keys=True), encoding="utf-8")

    statuses_env = (os.getenv("WEATHER_BOT_THRESHOLD_STATUSES") or "").strip()
    statuses = {s.strip() for s in statuses_env.split(",") if s.strip()} if statuses_env else None
    threshold_report = threshold_sweep_report(
        feature_rows_path=feature_rows_path,
        out_path=threshold_sweep_path,
        stake_usd=float(os.getenv("WEATHER_BOT_BT_SIZE_USD", "5")),
        max_spread=float(os.getenv("WEATHER_BOT_BT_MAX_SPREAD", "0.18")),
        statuses=statuses,
    )
    walkforward = walkforward_calibration_report(
        feature_rows_path=feature_rows_path,
        out_path=walkforward_path,
        folds=int(os.getenv("WEATHER_BOT_WALKFORWARD_FOLDS", "4")),
        min_train_rows=int(os.getenv("WEATHER_BOT_WALKFORWARD_MIN_TRAIN_ROWS", "100")),
        min_bin_samples=int(os.getenv("WEATHER_BOT_CAL_MIN_BIN_SAMPLES", "8")),
        shrinkage_n=int(os.getenv("WEATHER_BOT_CAL_SHRINKAGE_N", "25")),
        restrict_status=(os.getenv("WEATHER_BOT_CAL_RESTRICT_STATUS") or None),
    )

    print(
        json.dumps(
            {
                "export": export_result,
                "profile_build": build_result,
                "effectiveness_report_path": str(effectiveness_path),
                "effectiveness_summary": effectiveness.get("summary"),
                "threshold_sweep_report_path": str(threshold_sweep_path),
                "threshold_sweep_summary": threshold_report.get("summary"),
                "walkforward_report_path": str(walkforward_path),
                "walkforward_summary": walkforward.get("summary"),
                "walkforward_aggregate": walkforward.get("aggregate"),
                "runtime_profile_path_hint": str(profile_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
