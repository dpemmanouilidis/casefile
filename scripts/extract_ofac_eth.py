"""One-off script: extract Ethereum digital-currency addresses from the
official OFAC SDN Advanced XML export into a plain CSV.

Run once against the downloaded file; not part of the ingest/enrich
pipeline. See enrich/labels/ofac_sdn_eth.csv for the header documenting
source and retrieval date.
"""
import csv
import sys
import xml.etree.ElementTree as ET

NS = "{https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML}"
ETH_FEATURE_TYPE_ID = "345"


def local(tag):
    return tag.split("}", 1)[-1]


def extract(xml_path, out_path):
    rows = []

    context = ET.iterparse(xml_path, events=("start", "end"))
    current_party_names = []
    current_party_features = []

    for event, elem in context:
        tag = local(elem.tag)
        if event == "start" and tag == "DistinctParty":
            current_party_names = []
            current_party_features = []
        elif event == "end" and tag == "DistinctParty":
            if current_party_features:
                name = current_party_names[0] if current_party_names else "(unnamed)"
                for addr in current_party_features:
                    rows.append((name, addr))
            elem.clear()
        elif event == "end" and tag == "NamePartValue":
            if elem.text:
                current_party_names.append(elem.text.strip())
        elif event == "end" and tag == "Feature":
            if elem.get("FeatureTypeID") == ETH_FEATURE_TYPE_ID:
                for fv in elem.iter():
                    if local(fv.tag) == "VersionDetail" and fv.text:
                        current_party_features.append(fv.text.strip())

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["entity_name", "address"])
        for name, addr in rows:
            writer.writerow([name, addr])

    print(f"wrote {len(rows)} address rows to {out_path}")


if __name__ == "__main__":
    extract(sys.argv[1], sys.argv[2])
