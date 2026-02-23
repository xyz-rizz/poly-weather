from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weather_bot.models.risk import OpenPaperPosition, PlannedPaperExit, PlannedPaperOrder, PortfolioState


def _serialize(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.astimezone(timezone.utc).isoformat()
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def load_portfolio_state(path: str | Path) -> PortfolioState:
    path = Path(path)
    if not path.exists():
        return PortfolioState(as_of_utc=datetime.now(timezone.utc))
    data = json.loads(path.read_text(encoding="utf-8"))
    positions = []
    for row in data.get("open_positions", []):
        positions.append(
            OpenPaperPosition(
                market_id=row["market_id"],
                event_slug=row.get("event_slug", ""),
                city=row["city"],
                direction=row["direction"],
                size_usd=float(row["size_usd"]),
                opened_at_utc=_parse_dt(row["opened_at_utc"]) or datetime.now(timezone.utc),
                entry_ref_price=float(row["entry_ref_price"]),
            )
        )
    return PortfolioState(
        as_of_utc=_parse_dt(data.get("as_of_utc")),
        realized_pnl_today_usd=float(data.get("realized_pnl_today_usd", 0.0)),
        open_positions=positions,
    )


def save_portfolio_state(path: str | Path, state: PortfolioState) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_serialize(asdict(state)), separators=(",", ":")), encoding="utf-8")


def apply_accepted_plans(state: PortfolioState, plans: list[PlannedPaperOrder]) -> PortfolioState:
    next_positions = list(state.open_positions)
    now_utc = datetime.now(timezone.utc)
    existing_ids = {p.market_id for p in next_positions}
    for plan in plans:
        if not plan.accepted or plan.market_id in existing_ids:
            continue
        next_positions.append(
            OpenPaperPosition(
                market_id=plan.market_id,
                event_slug=plan.event_slug,
                city=plan.city,
                direction=plan.direction,
                size_usd=plan.size_usd,
                opened_at_utc=now_utc,
                entry_ref_price=plan.ref_price,
            )
        )
        existing_ids.add(plan.market_id)
    return PortfolioState(
        as_of_utc=now_utc,
        realized_pnl_today_usd=state.realized_pnl_today_usd,
        open_positions=next_positions,
    )


def append_plan_log(path: str | Path, plans: list[PlannedPaperOrder], mode: str, strategy_id: str = "unknown") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    now_utc = datetime.now(timezone.utc)
    with path.open("a", encoding="utf-8") as fh:
        for plan in plans:
            row = {
                "event_type": "planned_order",
                "created_at_utc": now_utc,
                "mode": mode,
                "strategy_id": strategy_id,
                "plan": asdict(plan),
            }
            fh.write(json.dumps(_serialize(row), separators=(",", ":")) + "\n")


def append_exit_log(path: str | Path, exits: list[PlannedPaperExit], mode: str, strategy_id: str = "unknown") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    now_utc = datetime.now(timezone.utc)
    with path.open("a", encoding="utf-8") as fh:
        for plan in exits:
            row = {
                "event_type": "planned_exit",
                "created_at_utc": now_utc,
                "mode": mode,
                "strategy_id": strategy_id,
                "plan": asdict(plan),
            }
            fh.write(json.dumps(_serialize(row), separators=(",", ":")) + "\n")


def apply_accepted_exits(state: PortfolioState, exits: list[PlannedPaperExit]) -> PortfolioState:
    accepted = {e.market_id: e for e in exits if e.accepted}
    next_positions: list[OpenPaperPosition] = []
    realized_delta = 0.0
    for pos in state.open_positions:
        exit_plan = accepted.get(pos.market_id)
        if exit_plan is None:
            next_positions.append(pos)
            continue
        realized_delta += exit_plan.unrealized_pnl_usd
    return PortfolioState(
        as_of_utc=datetime.now(timezone.utc),
        realized_pnl_today_usd=state.realized_pnl_today_usd + realized_delta,
        open_positions=next_positions,
    )
