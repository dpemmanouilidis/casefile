from ingest.__main__ import load_addresses


def test_load_addresses_skips_comments_and_blank_lines(tmp_path):
    path = tmp_path / "addresses.txt"
    path.write_text(
        "# a comment\n"
        "0xAAAA000000000000000000000000000000000A  # inline comment\n"
        "\n"
        "0xBBBB000000000000000000000000000000000B\n"
    )
    addresses = load_addresses(str(path))
    assert addresses == [
        "0xaaaa000000000000000000000000000000000a",
        "0xbbbb000000000000000000000000000000000b",
    ]


def test_repo_addresses_file_parses_to_nine_addresses():
    addresses = load_addresses("addresses.txt")
    assert len(addresses) == 9
    assert all(a.startswith("0x") for a in addresses)
