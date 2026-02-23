from __future__ import annotations

import json
import ssl
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class HttpClientError(RuntimeError):
    """Raised when an HTTP request fails or returns invalid JSON."""


@dataclass(frozen=True)
class JsonHttpClient:
    user_agent: str = "weather-polymarket-bot/0.1 (+local research)"
    timeout_seconds: float = 10.0
    verify_ssl: bool = True
    retries: int = 1
    backoff_seconds: float = 0.5

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        req_headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if headers:
            req_headers.update(headers)
        request = Request(url=url, headers=req_headers, method="GET")
        context = None
        if not self.verify_ssl:
            context = ssl._create_unverified_context()
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds, context=context) as response:
                    raw = response.read().decode("utf-8")
                break
            except HTTPError as exc:
                # Retry 5xx only.
                last_error = exc
                if 500 <= exc.code < 600 and attempt < self.retries:
                    time.sleep(self.backoff_seconds * (attempt + 1))
                    continue
                raise HttpClientError(f"HTTP {exc.code} for {url}") from exc
            except URLError as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.backoff_seconds * (attempt + 1))
                    continue
                raise HttpClientError(f"Network error for {url}: {exc.reason}") from exc
            except Exception as exc:  # pragma: no cover
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.backoff_seconds * (attempt + 1))
                    continue
                raise HttpClientError(f"Unexpected error for {url}: {exc}") from exc
        else:  # pragma: no cover
            raise HttpClientError(f"Request failed for {url}: {last_error}")

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HttpClientError(f"Invalid JSON from {url}") from exc
