"""Alchemy-backed EVM chain client.

Transaction and transfer discovery both go through `alchemy_getAssetTransfers`.
This means the `transactions` table only contains transactions that produced at
least one native or token transfer involving the queried address — a plain
failed call with no value movement and no logs would not surface. [Unverified]
This is a known limitation of building on the asset-transfers endpoint rather
than walking every block; it is acceptable for milestone 1's scope (tracing
value flow) but should be called out if `gate/` ever needs "every tx sent by
this address" as a hard guarantee.
"""

from __future__ import annotations

import time
from typing import Any, Iterator

import requests

from ingest.models import Transaction, Transfer

ALCHEMY_URL = "https://eth-mainnet.g.alchemy.com/v2/{api_key}"

# Categories fed to alchemy_getAssetTransfers. "external" and "internal" are
# native ETH movements; the rest are token standards.
TRANSFER_CATEGORIES = ["external", "internal", "erc20", "erc721", "erc1155"]

MAX_RETRIES = 5  # enough to ride out a short burst of free-tier rate limiting
# without a single flaky call turning a whole address into a partial run.
INITIAL_BACKOFF_SECONDS = 1.0  # doubles each retry (1s, 2s, 4s, 8s, 16s ~31s total) —
# fast enough not to stall an interactive run, long enough that a real rate-limit
# window usually clears before MAX_RETRIES is exhausted.


class RateLimitExceeded(Exception):
    """Raised when the RPC backend keeps rate-limiting us past MAX_RETRIES."""


class RpcError(Exception):
    """Raised on a non-retryable JSON-RPC error."""


class AlchemyClient:
    chain_id = "ethereum"

    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self._url = ALCHEMY_URL.format(api_key=api_key)
        self._session = session or requests.Session()
        self._sleep = sleep
        self._id = 0

    def _rpc(self, method: str, params: list[Any]) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        backoff = INITIAL_BACKOFF_SECONDS
        for _attempt in range(MAX_RETRIES):
            response = self._session.post(self._url, json=payload, timeout=30)
            if response.status_code == 429:
                self._sleep(backoff)
                backoff *= 2
                continue
            response.raise_for_status()
            body = response.json()
            if "error" in body:
                error = body["error"]
                # Alchemy surfaces rate limiting as a JSON-RPC error too, not
                # just HTTP 429.
                if error.get("code") == 429 or "rate limit" in str(error.get("message", "")).lower():
                    self._sleep(backoff)
                    backoff *= 2
                    continue
                raise RpcError(f"{method} failed: {error}")
            return body["result"]
        raise RateLimitExceeded(f"{method} rate-limited after {MAX_RETRIES} retries")

    def latest_block(self) -> int:
        return int(self._rpc("eth_blockNumber", []), 16)

    def _get_asset_transfers(
        self, address: str, from_block: int, to_block: int, direction: str
    ) -> Iterator[dict]:
        key = "fromAddress" if direction == "from" else "toAddress"
        params: dict[str, Any] = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            key: address,
            "category": TRANSFER_CATEGORIES,
            "withMetadata": True,
            "excludeZeroValue": False,
            "order": "asc",
            "maxCount": hex(1000),
        }
        page_key = None
        while True:
            if page_key:
                params["pageKey"] = page_key
            result = self._rpc("alchemy_getAssetTransfers", [params])
            yield from result.get("transfers", [])
            page_key = result.get("pageKey")
            if not page_key:
                break

    def _all_asset_transfers(self, address: str, from_block: int, to_block: int) -> list[dict]:
        seen_ids: set[str] = set()
        merged: list[dict] = []
        for direction in ("from", "to"):
            for raw in self._get_asset_transfers(address, from_block, to_block, direction):
                unique_id = raw.get("uniqueId") or f"{raw['hash']}:{raw.get('category')}:{raw.get('to')}:{raw.get('value')}"
                if unique_id in seen_ids:
                    continue
                seen_ids.add(unique_id)
                merged.append(raw)
        # Deterministic order so re-running assigns the same transfer_index.
        merged.sort(key=lambda t: (int(t["blockNum"], 16), t["hash"], t.get("category", ""), str(t.get("uniqueId", ""))))
        return merged

    def transfers(
        self, address: str, from_block: int | None = None, to_block: int | None = None
    ) -> Iterator[Transfer]:
        from_block = 0 if from_block is None else from_block
        to_block = self.latest_block() if to_block is None else to_block
        raw_transfers = self._all_asset_transfers(address, from_block, to_block)

        index_by_tx: dict[str, int] = {}
        for raw in raw_transfers:
            tx_hash = raw["hash"]
            transfer_index = index_by_tx.get(tx_hash, 0)
            index_by_tx[tx_hash] = transfer_index + 1

            raw_contract = raw.get("rawContract") or {}
            asset_address = raw_contract.get("address")
            amount_raw = raw_contract.get("value")
            if amount_raw is None:
                amount_raw = "0x0"
            yield Transfer(
                chain_id=self.chain_id,
                tx_hash=tx_hash,
                transfer_index=transfer_index,
                asset_address=asset_address.lower() if asset_address else None,
                asset_symbol=raw.get("asset"),
                asset_decimals=raw_contract.get("decimal") and int(raw_contract["decimal"], 16),
                from_address=raw["from"].lower(),
                to_address=raw["to"].lower() if raw.get("to") else "",
                amount_raw=str(int(amount_raw, 16)),
            )

    def transactions(
        self, address: str, from_block: int | None = None, to_block: int | None = None
    ) -> Iterator[Transaction]:
        from_block = 0 if from_block is None else from_block
        to_block = self.latest_block() if to_block is None else to_block
        raw_transfers = self._all_asset_transfers(address, from_block, to_block)

        seen_hashes: set[str] = set()
        for raw in raw_transfers:
            tx_hash = raw["hash"]
            if tx_hash in seen_hashes:
                continue
            seen_hashes.add(tx_hash)

            tx = self._rpc("eth_getTransactionByHash", [tx_hash])
            receipt = self._rpc("eth_getTransactionReceipt", [tx_hash])
            if tx is None or receipt is None:
                # Fail closed: record nothing rather than guess at a tx we
                # could not actually fetch.
                continue

            block_time = raw.get("metadata", {}).get("blockTimestamp")

            status_code = receipt.get("status")
            if status_code == "0x1":
                status = "success"
            elif status_code == "0x0":
                status = "failed"
            else:
                status = "unknown"

            gas_used = receipt.get("gasUsed")
            gas_price = receipt.get("effectiveGasPrice") or tx.get("gasPrice")
            fee_raw = None
            if gas_used is not None and gas_price is not None:
                fee_raw = str(int(gas_used, 16) * int(gas_price, 16))

            input_data = tx.get("input", "0x")
            method_id = input_data[:10] if input_data and input_data != "0x" else None

            yield Transaction(
                chain_id=self.chain_id,
                tx_hash=tx_hash,
                block_number=int(tx["blockNumber"], 16),
                block_time=block_time,
                from_address=tx["from"].lower(),
                to_address=tx["to"].lower() if tx.get("to") else None,
                value_raw=str(int(tx.get("value", "0x0"), 16)),
                fee_raw=fee_raw,
                status=status,
                method_id=method_id,
            )

    def _boundary_block(
        self, address: str, direction: str, order: str, from_block: int, to_block: int
    ) -> int | None:
        """The block of the single earliest (`order="asc"`) or latest
        (`order="desc"`) transfer where `address` appears in `direction`
        role, within [from_block, to_block], or None if there is none.
        `maxCount=1` makes this a one-page, cheap existence probe.
        """
        key = "fromAddress" if direction == "from" else "toAddress"
        params: dict[str, Any] = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            key: address,
            "category": TRANSFER_CATEGORIES,
            "withMetadata": True,
            "excludeZeroValue": False,
            "order": order,
            "maxCount": hex(1),
        }
        result = self._rpc("alchemy_getAssetTransfers", [params])
        transfers = result.get("transfers", [])
        if not transfers:
            return None
        return int(transfers[0]["blockNum"], 16)

    def activity_bounds(self, address: str, to_block: int) -> tuple[int | None, int | None]:
        """Does `address` have any transfer activity at all in [0, to_block],
        and if so, the first and last block of it?

        Alchemy's `fromAddress`/`toAddress` filters can't be combined into a
        single OR query (undocumented, and the rest of this client already
        treats them as separate queries — see `_all_asset_transfers`), so
        this checks both roles. Two ascending `maxCount=1` calls (one per
        role) are enough to prove non-existence — a genuinely inactive
        address costs exactly those 2 calls. If either role has activity,
        two more descending calls find the last block, for 4 calls total.

        Exists to disambiguate a zero-transfer ingest window: "this address
        is quiet" and "this window missed its activity" must never look the
        same. Returns (None, None) only for the former.
        """
        first_blocks = [
            block
            for block in (
                self._boundary_block(address, direction, "asc", 0, to_block)
                for direction in ("from", "to")
            )
            if block is not None
        ]
        if not first_blocks:
            return None, None
        last_blocks = [
            block
            for block in (
                self._boundary_block(address, direction, "desc", 0, to_block)
                for direction in ("from", "to")
            )
            if block is not None
        ]
        return min(first_blocks), max(last_blocks)

    def is_contract(self, address: str) -> bool:
        code = self._rpc("eth_getCode", [address, "latest"])
        return code is not None and code != "0x"
