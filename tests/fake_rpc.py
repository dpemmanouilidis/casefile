"""A fake Alchemy JSON-RPC backend for offline tests.

No test in this suite ever touches the network — every AlchemyClient in the
tests is constructed with a FakeSession that answers from an in-memory
fixture keyed by method name.
"""

from __future__ import annotations

from typing import Any, Callable


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class FakeSession:
    """Routes JSON-RPC calls to handler functions keyed by method name.

    A handler is `(params) -> result`. Set `fail_after` on a method name to
    make the Nth call raise a rate-limit-shaped error (used to test partial
    runs).
    """

    def __init__(self, handlers: dict[str, Callable[[list[Any]], Any]]):
        self._handlers = handlers
        self.call_log: list[tuple[str, list[Any]]] = []
        self.rate_limit_countdown: dict[str, int] = {}

    def post(self, url: str, json: dict, timeout: int) -> FakeResponse:
        method = json["method"]
        params = json["params"]
        self.call_log.append((method, params))

        if method in self.rate_limit_countdown:
            self.rate_limit_countdown[method] -= 1
            if self.rate_limit_countdown[method] >= 0:
                return FakeResponse({"jsonrpc": "2.0", "id": json["id"], "error": {"code": 429, "message": "rate limited"}})

        handler = self._handlers.get(method)
        if handler is None:
            raise AssertionError(f"no fake handler registered for RPC method {method}")
        result = handler(params)
        return FakeResponse({"jsonrpc": "2.0", "id": json["id"], "result": result})
