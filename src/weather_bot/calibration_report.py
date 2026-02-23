from __future__ import annotations

import json
import os
from pathlib import Path

from weather_bot.calibration import calibration_report


def main() -> int:
    scan_path = Path(os.getenv("WEATHER_BOT_SCAN_RECORD_PATH", "data/sample/scan_snapshots.jsonl"))
    universe = os.getenv("WEATHER_BOT_UNIVERSE", "tier1")
    days_back = int(os.getenv("WEATHER_BOT_CAL_DAYS_BACK", "14"))
    insecure_ssl = os.getenv("WEATHER_BOT_INSECURE_SSL", "0").strip().lower() in {"1", "true", "yes"}
    report = calibration_report(
        scan_snapshot_path=scan_path,
        universe_level=universe,
        days_back=days_back,
        insecure_ssl=insecure_ssl,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
