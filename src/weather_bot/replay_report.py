from __future__ import annotations

import json
from pathlib import Path

from weather_bot.simulation.replay import build_market_timelines, build_replay_summary, summarize_reject_reasons


def main() -> int:
    base_dir = Path("data/sample")
    summary = build_replay_summary(base_dir)
    timelines = build_market_timelines(base_dir)
    out = {
        "summary": summary.__dict__,
        "top_reject_reasons": summarize_reject_reasons(base_dir),
        "markets_with_history": {k: len(v) for k, v in sorted(timelines.items())},
    }
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
