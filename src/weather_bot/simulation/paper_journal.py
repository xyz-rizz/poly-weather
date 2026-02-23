from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weather_bot.models.domain import Opportunity


def _serialize(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.astimezone(timezone.utc).isoformat()
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def write_signal_event(
    path: str | Path,
    opportunity: Opportunity,
    size_usd: float,
    mode: str = "PAPER",
    strategy_id: str = "unknown",
) -> None:
    record = {
        "event_type": "signal",
        "mode": mode,
        "strategy_id": strategy_id,
        "created_at_utc": datetime.now(timezone.utc),
        "size_usd": size_usd,
        "direction": "BUY_YES" if opportunity.edge > 0 else "BUY_NO",
        "opportunity": asdict(opportunity),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_serialize(record), separators=(",", ":")) + "\n")
