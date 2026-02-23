from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weather_bot.health import check_health


def _serialize(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.astimezone(timezone.utc).isoformat()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    return obj


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_serialize(row), separators=(",", ":")) + "\n")


def _run_scan_once(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "weather_bot"]
    return subprocess.run(cmd, env=env, text=True, capture_output=True)


def main() -> int:
    interval_seconds = float(os.getenv("WEATHER_BOT_RUNNER_INTERVAL_SECONDS", "300"))
    max_cycles = int(os.getenv("WEATHER_BOT_RUNNER_CYCLES", "0"))  # 0 means forever
    base_dir = Path(os.getenv("WEATHER_BOT_RUNNER_BASEDIR", "data/sample"))
    run_log_path = base_dir / "runner_cycles.jsonl"
    health_log_path = base_dir / "health_snapshots.jsonl"
    run_id = os.getenv("WEATHER_BOT_RUN_ID") or f"runner-{uuid.uuid4().hex[:10]}"

    # Default runner posture: record scans and avoid shadow trading unless explicitly enabled.
    passthrough_env = os.environ.copy()
    passthrough_env.setdefault("WEATHER_BOT_RECORD_SCAN", "1")

    cycle = 0
    while True:
        cycle += 1
        start = datetime.now(timezone.utc)
        env = passthrough_env.copy()
        env["WEATHER_BOT_RUN_ID"] = run_id
        env["WEATHER_BOT_SCAN_SEQ"] = str(cycle)

        proc = _run_scan_once(env)
        end = datetime.now(timezone.utc)
        duration = (end - start).total_seconds()

        _append_jsonl(
            run_log_path,
            {
                "event_type": "runner_cycle",
                "created_at_utc": end,
                "run_id": run_id,
                "cycle": cycle,
                "hostname": socket.gethostname(),
                "duration_seconds": duration,
                "returncode": proc.returncode,
                "stdout_tail": proc.stdout[-2000:],
                "stderr_tail": proc.stderr[-2000:],
            },
        )

        health = check_health(base_dir)
        _append_jsonl(
            health_log_path,
            {
                "event_type": "health_snapshot",
                "created_at_utc": end,
                "run_id": run_id,
                "cycle": cycle,
                "health": health,
            },
        )

        print(
            f"[{end.isoformat()}] cycle={cycle} rc={proc.returncode} dur={duration:.1f}s "
            f"health={health.get('status')} scans={health.get('scan_count')}"
        )
        if proc.returncode != 0:
            # Keep logs and stop so failures are visible.
            print(proc.stdout[-4000:])
            print(proc.stderr[-4000:], file=sys.stderr)
            return proc.returncode

        if max_cycles > 0 and cycle >= max_cycles:
            return 0

        sleep_for = max(0.0, interval_seconds - duration)
        time.sleep(sleep_for)


if __name__ == "__main__":
    raise SystemExit(main())
