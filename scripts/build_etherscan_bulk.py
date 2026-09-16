"""One-off script: concatenate every per-entity CSV from a local clone of
github.com/brianleect/etherscan-labels (data/etherscan/accounts/*.csv) into
a single bulk labels file, one row per address, carrying the entity name
(the source filename, human-readable) but NOT a category — category
membership is resolved separately, at load time, against the small
hand-maintained enrich/labels/entity_categories.csv. This keeps the
third-party scrape and our own confident classification decisions in two
separate, independently auditable files.

Usage: python scripts/build_etherscan_bulk.py <repo_clone_dir> <out_csv>
"""
import csv
import sys
from pathlib import Path


def entity_name_from_filename(path: Path) -> str:
    return path.stem


def build(repo_dir: Path, out_path: Path) -> None:
    accounts_dir = repo_dir / "data" / "etherscan" / "accounts"
    csv_files = sorted(p for p in accounts_dir.glob("*.csv"))

    rows = []
    seen = set()  # (address, entity) - a few entities may list the same address twice
    for path in csv_files:
        entity = entity_name_from_filename(path)
        with open(path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                address = raw.get("Address", "").strip().lower()
                if not address.startswith("0x"):
                    continue
                key = (address, entity)
                if key in seen:
                    continue
                seen.add(key)
                rows.append((address, entity))

    rows.sort()
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        f.write("# source: github.com/brianleect/etherscan-labels\n")
        f.write("# commit: 923aba72c7e2d0682f7ae6194b6140bd90668dc9\n")
        f.write("# retrieved: 2026-09-16\n")
        f.write("# THIRD-PARTY ETHERSCAN SCRAPE, UNVERIFIED. Not hand-sourced.\n")
        f.write("# category is resolved at load time from enrich/labels/entity_categories.csv;\n")
        f.write("# an entity with no mapping there loads as 'unknown', never a guess.\n")
        writer = csv.writer(f)
        writer.writerow(["chain_id", "address", "entity", "source", "retrieved"])
        for address, entity in rows:
            writer.writerow(
                [
                    "ethereum",
                    address,
                    entity,
                    "github.com/brianleect/etherscan-labels@923aba7 (third-party Etherscan scrape, unverified)",
                    "2026-09-16",
                ]
            )
    print(f"wrote {len(rows)} rows from {len(csv_files)} entity files to {out_path}")


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
