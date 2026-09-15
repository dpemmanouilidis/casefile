from dataclasses import dataclass


@dataclass(frozen=True)
class Transaction:
    chain_id: str
    tx_hash: str
    block_number: int
    block_time: str  # ISO 8601 UTC
    from_address: str
    to_address: str | None
    value_raw: str
    fee_raw: str | None
    status: str  # 'success' | 'failed' | 'unknown'
    method_id: str | None


@dataclass(frozen=True)
class Transfer:
    chain_id: str
    tx_hash: str
    transfer_index: int
    asset_address: str | None
    asset_symbol: str | None
    asset_decimals: int | None
    from_address: str
    to_address: str
    amount_raw: str
