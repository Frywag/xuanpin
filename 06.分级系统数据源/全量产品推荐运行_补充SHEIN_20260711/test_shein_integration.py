import json
from pathlib import Path


RUN_ROOT = Path(__file__).resolve().parent


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_all_shein_products_enter_scoring():
    envelope = json.loads(
        (RUN_ROOT / "normalized_inputs/frontend_shein.json").read_text(encoding="utf-8"))
    packets = read_jsonl(RUN_ROOT / "output/candidate_packets.jsonl")
    shein_packets = [packet for packet in packets if packet["platform"] == "shein"]

    assert len(envelope["records"]) == 200
    assert len(packets) == 2000
    assert len(shein_packets) == 200
    assert all(any(ref["source_id"] == "frontend_shein"
                   for ref in packet["source_refs"])
               for packet in shein_packets)


def test_shein_delivery_has_complete_canonical_keys_and_media():
    deliveries = read_jsonl(
        RUN_ROOT / "delivery/产品推荐_补充SHEIN_入库152键_20260711.jsonl")
    shein_deliveries = [item for item in deliveries if item["platform"] == "shein"]

    assert len(deliveries) == 353
    assert len(shein_deliveries) == 107
    assert all(item["canonical_key_count"] == 152 for item in deliveries)
    assert all(len(item["canonical_152"]) == 152 for item in deliveries)
    assert all(len(item["canonical_status"]) == 152 for item in deliveries)
    assert all(len(item["canonical_evidence"]) == 152 for item in deliveries)
    assert all(item["canonical_152"]["cp.gallery_images"] for item in shein_deliveries)
    assert all(item["canonical_evidence"]["cp.gallery_images"]
               for item in shein_deliveries)
