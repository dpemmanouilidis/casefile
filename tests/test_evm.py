from ingest.evm import AlchemyClient
from tests.conftest import ADDRESS, COUNTERPARTY, TOKEN, TX_HASH_ERC20, TX_HASH_NATIVE
from tests.fake_rpc import FakeSession


def test_transactions_normalises_native_and_erc20(client):
    txs = {tx.tx_hash: tx for tx in client.transactions(ADDRESS, 0, 200)}

    assert set(txs) == {TX_HASH_NATIVE, TX_HASH_ERC20}

    native = txs[TX_HASH_NATIVE]
    assert native.chain_id == "ethereum"
    assert native.from_address == ADDRESS.lower()
    assert native.to_address == COUNTERPARTY.lower()
    assert native.value_raw == str(int(1.5 * 10**18))
    assert native.fee_raw == str(21000 * 20_000_000_000)
    assert native.status == "success"
    assert native.method_id is None

    erc20 = txs[TX_HASH_ERC20]
    assert erc20.method_id == "0xa9059cbb"
    assert erc20.to_address == TOKEN.lower()


def test_transfers_normalises_amounts_as_strings(client):
    transfers = {t.tx_hash: t for t in client.transfers(ADDRESS, 0, 200)}

    native = transfers[TX_HASH_NATIVE]
    assert native.asset_address is None
    assert native.amount_raw == str(int(1.5 * 10**18))
    assert isinstance(native.amount_raw, str)

    token = transfers[TX_HASH_ERC20]
    assert token.asset_address == TOKEN.lower()
    assert token.asset_symbol == "USDC"
    assert token.asset_decimals == 6
    assert token.amount_raw == "42000000"


def test_transfers_and_transactions_are_deterministic_across_calls(client):
    first = list(client.transfers(ADDRESS, 0, 200))
    second = list(client.transfers(ADDRESS, 0, 200))
    assert first == second


def test_get_asset_transfers_follows_pageKey_across_multiple_pages():
    """alchemy_getAssetTransfers caps each response at maxCount and returns a
    pageKey when more results exist. If the client stopped after the first
    page it would silently truncate history — this proves it does not.
    """
    pages = {
        None: {
            "transfers": [
                {
                    "uniqueId": "p1",
                    "hash": "0x" + "a" * 64,
                    "from": ADDRESS,
                    "to": COUNTERPARTY,
                    "value": 1,
                    "asset": "ETH",
                    "category": "external",
                    "blockNum": hex(10),
                    "rawContract": {"value": hex(10**18), "address": None, "decimal": hex(18)},
                    "metadata": {"blockTimestamp": "2026-09-01T00:00:00.000Z"},
                }
            ],
            "pageKey": "next-page",
        },
        "next-page": {
            "transfers": [
                {
                    "uniqueId": "p2",
                    "hash": "0x" + "b" * 64,
                    "from": ADDRESS,
                    "to": COUNTERPARTY,
                    "value": 2,
                    "asset": "ETH",
                    "category": "external",
                    "blockNum": hex(20),
                    "rawContract": {"value": hex(2 * 10**18), "address": None, "decimal": hex(18)},
                    "metadata": {"blockTimestamp": "2026-09-01T00:10:00.000Z"},
                }
            ],
            # no pageKey: this is the last page
        },
    }
    seen_page_keys = []

    def paginated_asset_transfers(params):
        query = params[0]
        if query.get("fromAddress") != ADDRESS:
            return {"transfers": []}
        page_key = query.get("pageKey")
        seen_page_keys.append(page_key)
        return pages[page_key]

    session = FakeSession(
        {
            "eth_blockNumber": lambda _params: hex(200),
            "alchemy_getAssetTransfers": paginated_asset_transfers,
            "eth_getTransactionByHash": lambda params: {
                "hash": params[0],
                "blockNumber": hex(10),
                "from": ADDRESS,
                "to": COUNTERPARTY,
                "value": hex(10**18),
                "input": "0x",
                "gasPrice": hex(10**10),
            },
            "eth_getTransactionReceipt": lambda params: {
                "status": "0x1",
                "gasUsed": hex(21000),
                "effectiveGasPrice": hex(10**10),
            },
        }
    )
    paginating_client = AlchemyClient(api_key="test-key", session=session, sleep=lambda _s: None)

    transfers = list(paginating_client.transfers(ADDRESS, 0, 200))

    # Both pages' results made it through: the client followed pageKey rather
    # than stopping at the first page's maxCount.
    assert {t.tx_hash for t in transfers} == {"0x" + "a" * 64, "0x" + "b" * 64}
    assert seen_page_keys == [None, "next-page"]
