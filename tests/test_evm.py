from tests.conftest import ADDRESS, COUNTERPARTY, TOKEN, TX_HASH_ERC20, TX_HASH_NATIVE


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
