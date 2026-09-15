import pytest

from ingest.evm import AlchemyClient
from tests.fake_rpc import FakeSession

ADDRESS = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
COUNTERPARTY = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
TOKEN = "0xcccccccccccccccccccccccccccccccccccccccc"
TX_HASH_NATIVE = "0x" + "1" * 64
TX_HASH_ERC20 = "0x" + "2" * 64

NATIVE_TRANSFER = {
    "uniqueId": "u1",
    "hash": TX_HASH_NATIVE,
    "from": ADDRESS,
    "to": COUNTERPARTY,
    "value": 1.5,
    "asset": "ETH",
    "category": "external",
    "blockNum": hex(100),
    "rawContract": {"value": hex(int(1.5 * 10**18)), "address": None, "decimal": hex(18)},
    "metadata": {"blockTimestamp": "2026-09-01T00:00:00.000Z"},
}

ERC20_TRANSFER = {
    "uniqueId": "u2",
    "hash": TX_HASH_ERC20,
    "from": COUNTERPARTY,
    "to": ADDRESS,
    "value": 42,
    "asset": "USDC",
    "category": "erc20",
    "blockNum": hex(101),
    "rawContract": {"value": hex(42_000_000), "address": TOKEN, "decimal": hex(6)},
    "metadata": {"blockTimestamp": "2026-09-01T00:05:00.000Z"},
}


def asset_transfers_handler(params):
    query = params[0]
    if query.get("fromAddress") == ADDRESS:
        return {"transfers": [NATIVE_TRANSFER]}
    if query.get("toAddress") == ADDRESS:
        return {"transfers": [ERC20_TRANSFER]}
    return {"transfers": []}


def tx_by_hash_handler(params):
    tx_hash = params[0]
    if tx_hash == TX_HASH_NATIVE:
        return {
            "hash": TX_HASH_NATIVE,
            "blockNumber": hex(100),
            "from": ADDRESS,
            "to": COUNTERPARTY,
            "value": hex(int(1.5 * 10**18)),
            "input": "0x",
            "gasPrice": hex(20_000_000_000),
        }
    if tx_hash == TX_HASH_ERC20:
        return {
            "hash": TX_HASH_ERC20,
            "blockNumber": hex(101),
            "from": COUNTERPARTY,
            "to": TOKEN,
            "value": hex(0),
            "input": "0xa9059cbb" + "0" * 128,
            "gasPrice": hex(30_000_000_000),
        }
    return None


def receipt_handler(params):
    tx_hash = params[0]
    if tx_hash == TX_HASH_NATIVE:
        return {"status": "0x1", "gasUsed": hex(21000), "effectiveGasPrice": hex(20_000_000_000)}
    if tx_hash == TX_HASH_ERC20:
        return {"status": "0x1", "gasUsed": hex(55000), "effectiveGasPrice": hex(30_000_000_000)}
    return None


def block_number_handler(_params):
    return hex(200)


@pytest.fixture
def fake_session():
    return FakeSession(
        {
            "eth_blockNumber": block_number_handler,
            "alchemy_getAssetTransfers": asset_transfers_handler,
            "eth_getTransactionByHash": tx_by_hash_handler,
            "eth_getTransactionReceipt": receipt_handler,
        }
    )


@pytest.fixture
def client(fake_session):
    return AlchemyClient(api_key="test-key", session=fake_session, sleep=lambda _s: None)
