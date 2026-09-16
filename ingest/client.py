from typing import Iterator, Protocol

from ingest.models import Transaction, Transfer


class ChainClient(Protocol):
    chain_id: str

    def latest_block(self) -> int: ...

    def transactions(
        self, address: str, from_block: int | None = None, to_block: int | None = None
    ) -> Iterator[Transaction]: ...

    def transfers(
        self, address: str, from_block: int | None = None, to_block: int | None = None
    ) -> Iterator[Transfer]: ...

    def is_contract(self, address: str) -> bool: ...
