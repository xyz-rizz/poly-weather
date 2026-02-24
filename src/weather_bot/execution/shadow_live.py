from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from weather_bot.core.config import ScanConfig
from weather_bot.core.risk import RiskEngine
from weather_bot.execution.polymarket_clob import PolymarketCLOBExecutor
from weather_bot.models.domain import Opportunity
from weather_bot.models.risk import PlannedPaperOrder
from weather_bot.simulation.portfolio_state import load_portfolio_state


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    txt = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(txt).astimezone(UTC)
    except ValueError:
        return None


def _f(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except Exception:
        return None


def _serialize(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.astimezone(UTC).isoformat()
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@dataclass(frozen=True)
class ExecutionGuardDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class ShadowExecutionIntent:
    market_id: str
    event_slug: str
    city: str
    station_id: str
    direction: str
    size_usd: float
    ref_price: float
    price_limit: float
    shares_estimate: float
    confidence_score: float
    edge: float
    quote_age_seconds: float
    payload: dict[str, Any]


def _build_guard_summary(base_dir: Path) -> dict[str, Any]:
    perf = _read_json(Path(os.getenv("WEATHER_BOT_PAPER_PERFORMANCE_REPORT", str(base_dir / "paper_performance_report.json"))))
    settle = _read_json(Path(os.getenv("WEATHER_BOT_PAPER_SETTLEMENT_REPORT", str(base_dir / "paper_settlement_report.json"))))
    state = _read_json(Path(os.getenv("WEATHER_BOT_PAPER_SETTLEMENT_STATE", str(base_dir / "paper_settlement_state.json"))))
    return {"paper_performance": perf, "paper_settlement": settle, "paper_state": state}


def _execution_guard(
    *,
    base_dir: Path,
    scan_time_utc: datetime | None,
    cfg: ScanConfig,
) -> ExecutionGuardDecision:
    if os.getenv("WEATHER_BOT_EXEC_ALLOW", "0").strip().lower() not in {"1", "true", "yes"}:
        return ExecutionGuardDecision(False, "exec allow flag disabled")

    if scan_time_utc is None:
        return ExecutionGuardDecision(False, "missing scan time")
    max_scan_age = float(os.getenv("WEATHER_BOT_EXEC_MAX_SCAN_AGE_SECONDS", "180"))
    age = max(0.0, (datetime.now(UTC) - scan_time_utc).total_seconds())
    if age > max_scan_age:
        return ExecutionGuardDecision(False, f"scan too stale ({age:.1f}s)")

    checks = _build_guard_summary(base_dir)
    perf = checks["paper_performance"]
    settle = checks["paper_settlement"]
    state = checks["paper_state"]
    closed_summary = (perf.get("closed_summary") or {}) if isinstance(perf, dict) else {}
    open_summary = (perf.get("open_summary") or {}) if isinstance(perf, dict) else {}

    min_closed_trades = int(os.getenv("WEATHER_BOT_EXEC_MIN_CLOSED_TRADES", "20"))
    closed_trades = int(closed_summary.get("trades") or 0)
    if closed_trades < min_closed_trades:
        return ExecutionGuardDecision(False, f"insufficient paper closed trades ({closed_trades} < {min_closed_trades})")

    min_total_pnl = float(os.getenv("WEATHER_BOT_EXEC_MIN_TOTAL_PNL_USD", "0"))
    total_pnl = _f(open_summary.get("total_pnl_including_open_usd"))
    if total_pnl is None:
        total_pnl = _f(closed_summary.get("total_pnl_usd")) or 0.0
    if total_pnl < min_total_pnl:
        return ExecutionGuardDecision(False, f"paper total pnl below floor ({total_pnl:.2f} < {min_total_pnl:.2f})")

    max_live_open_positions = int(os.getenv("WEATHER_BOT_EXEC_MAX_OPEN_POSITIONS", str(cfg.max_open_positions)))
    live_state_path = Path(os.getenv("WEATHER_BOT_PORTFOLIO_STATE_PATH", str(base_dir / "portfolio_state.json")))
    live_state = load_portfolio_state(live_state_path)
    if len(live_state.open_positions) >= max_live_open_positions:
        return ExecutionGuardDecision(False, "live shadow portfolio max open positions reached")

    max_realized_loss = float(os.getenv("WEATHER_BOT_EXEC_MAX_REALIZED_LOSS_USD", str(cfg.daily_loss_cap_usd)))
    realized_total = _f(settle.get("realized_pnl_total_usd")) if isinstance(settle, dict) else None
    if realized_total is not None and realized_total <= -abs(max_realized_loss):
        return ExecutionGuardDecision(False, "paper realized loss guard hit")

    return ExecutionGuardDecision(True, "allowed")


def _quote_age_seconds(opp: Opportunity, now_utc: datetime) -> float:
    try:
        return max(0.0, (now_utc - opp.quote.as_of_utc.astimezone(UTC)).total_seconds())
    except Exception:
        return 0.0


def _price_limit_for_direction(opp: Opportunity, direction: str) -> float:
    slip_buffer = float(os.getenv("WEATHER_BOT_EXEC_PRICE_BUFFER", "0.01"))
    if direction == "BUY_YES":
        target = min(0.999, max(0.001, opp.quote.yes_ask + slip_buffer))
    else:
        target = min(0.999, max(0.001, opp.quote.no_ask + slip_buffer))
    return target


def _build_shadow_intent(plan: PlannedPaperOrder, opp: Opportunity, now_utc: datetime) -> ShadowExecutionIntent:
    direction = plan.direction
    ref_price = plan.ref_price
    price_limit = _price_limit_for_direction(opp, direction)
    shares_estimate = plan.size_usd / max(price_limit, 1e-9)
    payload = {
        "market_id": plan.market_id,
        "clob_market_id": opp.market.clob_market_id or opp.market.market_id,
        "event_slug": plan.event_slug,
        "city": plan.city,
        "side": "BUY",
        "outcome": "YES" if direction == "BUY_YES" else "NO",
        "token_id": opp.market.yes_token_id if direction == "BUY_YES" else opp.market.no_token_id,
        "order_type": "limit",
        "size_usd": round(plan.size_usd, 6),
        "price_limit": round(price_limit, 6),
        "shares_estimate": round(shares_estimate, 6),
        "post_only": os.getenv("WEATHER_BOT_EXEC_POST_ONLY", "1").strip().lower() in {"1", "true", "yes"},
        "time_in_force": os.getenv("WEATHER_BOT_EXEC_TIF", "GTC"),
        "shadow_only": True,
        "created_at_utc": now_utc.isoformat(),
    }
    return ShadowExecutionIntent(
        market_id=plan.market_id,
        event_slug=plan.event_slug,
        city=plan.city,
        station_id=opp.market.station_id,
        direction=direction,
        size_usd=plan.size_usd,
        ref_price=ref_price,
        price_limit=price_limit,
        shares_estimate=shares_estimate,
        confidence_score=plan.confidence_score,
        edge=plan.edge,
        quote_age_seconds=_quote_age_seconds(opp, now_utc),
        payload=payload,
    )


def _append_attempts(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(_serialize(row), separators=(",", ":")) + "\n")


def run_shadow_live_execution(
    *,
    opportunities: list[Opportunity],
    scan_time_utc: datetime | None,
    cfg: ScanConfig,
    mode: str,
) -> dict[str, Any]:
    exec_mode = os.getenv("WEATHER_BOT_EXECUTION_MODE", "shadow_submit").strip().lower()
    base_dir = Path(os.getenv("WEATHER_BOT_RUNNER_BASEDIR", "data/sample"))
    now_utc = datetime.now(UTC)
    guard = _execution_guard(base_dir=base_dir, scan_time_utc=scan_time_utc, cfg=cfg)
    attempt_log_path = Path(os.getenv("WEATHER_BOT_EXEC_ATTEMPT_LOG_PATH", str(base_dir / "live_execution_attempts.jsonl")))
    state_path = Path(os.getenv("WEATHER_BOT_EXEC_STATE_PATH", str(base_dir / "live_execution_state.json")))
    result_log_path = Path(os.getenv("WEATHER_BOT_EXEC_RESULT_LOG_PATH", str(base_dir / "live_execution_results.jsonl")))

    state = load_portfolio_state(Path(os.getenv("WEATHER_BOT_PORTFOLIO_STATE_PATH", str(base_dir / "portfolio_state.json"))))
    risk_engine = RiskEngine(cfg)
    plans = risk_engine.plan_orders(opportunities, state)
    opp_by_market = {o.market.market_id: o for o in opportunities}

    max_submits = int(os.getenv("WEATHER_BOT_EXEC_MAX_SUBMITS_PER_SCAN", "2"))
    canary_max_order_usd = float(os.getenv("WEATHER_BOT_EXEC_CANARY_MAX_ORDER_USD", "5"))
    canary_max_notional_per_scan = float(os.getenv("WEATHER_BOT_EXEC_CANARY_MAX_NOTIONAL_PER_SCAN_USD", "10"))
    submitted = 0
    accepted_notional = 0.0
    attempts: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    clob = PolymarketCLOBExecutor(exec_mode if exec_mode in {"dry_run", "live_canary"} else "dry_run")
    for plan in plans:
        opp = opp_by_market.get(plan.market_id)
        if opp is None:
            continue
        accepted_by_plan = bool(plan.accepted)
        accepted = guard.allowed and accepted_by_plan and submitted < max_submits
        reject_reason = None
        if accepted and plan.size_usd > canary_max_order_usd:
            accepted = False
            reject_reason = "canary max order notional exceeded"
        if accepted and (accepted_notional + plan.size_usd) > canary_max_notional_per_scan:
            accepted = False
            reject_reason = "canary max scan notional exceeded"
        intent = _build_shadow_intent(plan, opp, now_utc)
        if accepted and not str(intent.payload.get("token_id") or "").strip() and exec_mode in {"dry_run", "live_canary"}:
            accepted = False
            reject_reason = "missing token_id for clob execution"
        reason = "accepted-shadow-submit" if accepted else (
            reject_reason
            or ("max submits per scan reached" if guard.allowed and accepted_by_plan and submitted >= max_submits else (plan.reason if not accepted_by_plan else guard.reason))
        )
        attempts.append(
            {
                "event_type": "live_execution_attempt",
                "created_at_utc": now_utc,
                "mode": mode,
                "execution_mode": exec_mode,
                "strategy_id": cfg.strategy_id,
                "guard": asdict(guard),
                "accepted": accepted,
                "reason": reason,
                "plan": asdict(plan),
                "intent": asdict(intent),
            }
        )
        if accepted:
            submitted += 1
            accepted_notional += plan.size_usd
            if exec_mode in {"dry_run", "live_canary"}:
                submit_res = clob.submit_limit_order(intent.payload)
                results.append(
                    {
                        "event_type": "live_execution_result",
                        "created_at_utc": now_utc,
                        "mode": mode,
                        "execution_mode": exec_mode,
                        "market_id": intent.market_id,
                        "event_slug": intent.event_slug,
                        "city": intent.city,
                        "accepted_attempt": True,
                        "submit_ok": submit_res.ok,
                        "submit_error": submit_res.error,
                        "response": submit_res.response,
                    }
                )

    _append_attempts(attempt_log_path, attempts)
    _append_attempts(result_log_path, results)
    exec_state = {
        "as_of_utc": now_utc.isoformat(),
        "execution_mode": exec_mode,
        "guard": asdict(guard),
        "attempts_this_scan": len(attempts),
        "accepted_shadow_submits_this_scan": submitted,
        "accepted_notional_usd_this_scan": round(accepted_notional, 6),
        "max_submits_per_scan": max_submits,
        "canary_max_order_usd": canary_max_order_usd,
        "canary_max_notional_per_scan_usd": canary_max_notional_per_scan,
        "attempt_log_path": str(attempt_log_path),
        "result_log_path": str(result_log_path),
        "submit_results_this_scan": len(results),
        "submit_successes_this_scan": sum(1 for r in results if r.get("submit_ok")),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(exec_state, indent=2, sort_keys=True), encoding="utf-8")
    return exec_state
