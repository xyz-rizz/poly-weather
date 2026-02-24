from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CLOBSubmitResult:
    ok: bool
    mode: str
    error: str | None
    response: dict[str, Any] | None


class PolymarketCLOBExecutor:
    """
    Thin wrapper around py-clob-client.
    - Works in dry-run without SDK installed.
    - In live_canary mode requires py-clob-client and env credentials.
    """

    def __init__(self, mode: str) -> None:
        self.mode = mode

    def submit_limit_order(self, payload: dict[str, Any]) -> CLOBSubmitResult:
        if self.mode == "dry_run":
            return CLOBSubmitResult(ok=True, mode=self.mode, error=None, response={"dry_run": True, "payload": payload})
        if self.mode != "live_canary":
            return CLOBSubmitResult(ok=False, mode=self.mode, error=f"unsupported mode {self.mode}", response=None)

        try:
            from py_clob_client.client import ClobClient  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on optional package
            return CLOBSubmitResult(ok=False, mode=self.mode, error=f"py-clob-client unavailable: {exc}", response=None)

        host = os.getenv("POLY_CLOB_HOST", "https://clob.polymarket.com").strip()
        chain_id = int(os.getenv("POLY_CHAIN_ID", "137"))
        private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
        if not private_key:
            return CLOBSubmitResult(ok=False, mode=self.mode, error="POLYMARKET_PRIVATE_KEY missing", response=None)

        # NOTE: API shapes vary by SDK versions. We keep this defensive and return a clear error on incompatibility.
        try:  # pragma: no cover - optional integration path
            client = ClobClient(host, key=private_key, chain_id=chain_id)
            # Newer SDKs may support create_order/post_order; others use typed params.
            if hasattr(client, "create_order") and hasattr(client, "post_order"):
                order_req = {
                    "token_id": payload["token_id"],
                    "side": payload["side"],
                    "price": payload["price_limit"],
                    "size": payload["shares_estimate"],
                }
                if "post_only" in payload:
                    order_req["post_only"] = payload["post_only"]
                created = client.create_order(order_req)
                posted = client.post_order(created)
                return CLOBSubmitResult(ok=True, mode=self.mode, error=None, response={"created": created, "posted": posted})
            return CLOBSubmitResult(ok=False, mode=self.mode, error="Unsupported py-clob-client API version (missing create_order/post_order)", response=None)
        except Exception as exc:
            return CLOBSubmitResult(ok=False, mode=self.mode, error=f"CLOB submit failed: {exc}", response=None)

