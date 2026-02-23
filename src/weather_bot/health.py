from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


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


def check_health(base_dir: Path) -> dict[str, Any]:
    scans = _read_jsonl(base_dir / "scan_snapshots.jsonl")
    issues = Counter()
    recent_zero_opp = 0
    for row in scans[-50:]:
        sr = row.get("scan_result", {})
        if int(sr.get("opportunity_count", 0)) == 0:
            recent_zero_opp += 1
        for ev in sr.get("evaluations", []):
            reason = str(ev.get("reason") or "")
            if "source error" in reason:
                issues["source_error"] += 1
            if "stale" in reason:
                issues["stale_data"] += 1
            if "spread" in reason and "too wide" in reason:
                issues["wide_spread"] += 1
            if "no forecasts" in reason:
                issues["no_forecasts"] += 1
    status = "OK"
    if issues["source_error"] > 0:
        status = "DEGRADED"
    if recent_zero_opp >= 10 and len(scans) >= 10:
        issues["zero_opportunity_streak_warning"] += 1
    return {
        "status": status,
        "scan_count": len(scans),
        "recent_zero_opportunity_scans": recent_zero_opp,
        "issue_counts_last_50_scans": dict(issues),
    }


def main() -> int:
    base_dir = Path("data/sample")
    print(json.dumps(check_health(base_dir), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
