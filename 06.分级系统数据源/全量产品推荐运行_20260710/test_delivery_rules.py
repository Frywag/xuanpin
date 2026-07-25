import json

from augment_delivery import build_canonical_152, load_canonical_spec


def test_canonical_spec_contains_exactly_152_unique_keys():
    spec = load_canonical_spec()
    keys = [item["key"] for item in spec]

    assert len(keys) == 152
    assert len(set(keys)) == 152
    assert keys[0] == "cp.canonical_id"


def test_delivery_mapping_has_complete_key_status_and_evidence_sets():
    packet = {
        "candidate_id": "amazon_B0TEST1234",
        "platform": "amazon",
        "primary_product_id": "B0TEST1234",
        "canonical_url": "https://amazon.com/dp/B0TEST1234",
        "source_refs": [{"source_id": "seller_sprite_amazon", "envelope_id": "env1"}],
        "basic_facts": {
            "title": "Women Sleeveless V Neck A-Line Midi Summer Dress",
            "brand": "Example",
            "category_path": ["Women", "Dresses"],
            "image_url": "https://example.com/main.jpg",
            "price": {"amount": 29.99, "currency": "USD"},
            "original_price": None,
            "rating": 4.5,
            "review_count": 200,
            "in_stock": None,
        },
        "market_metrics": {},
        "competition_metrics": {},
        "product_opportunity": {},
        "cross_platform": {},
        "owned_supply_inputs": {},
        "context": {"category_name": "Women's Casual Dresses"},
        "evidence_pack": [{
            "evidence_id": "ev_title",
            "source_id": "seller_sprite_amazon",
            "source_type": "plugin",
            "tool_or_adapter": "test",
            "field_path": "basic_facts.title",
            "artifact_path": "seller.xlsx",
            "record_locator": "sheet=产品总表;row=2",
            "confidence": "high",
            "note": "",
        }],
    }
    records = [{
        "source_id": "seller_sprite_amazon",
        "source_type": "plugin",
        "fields": {
            "description_text": "Material: 95% Polyester. Sleeveless V Neck A-line Midi dress.",
            "material_text": "95% Polyester, 5% Spandex",
            "product_attributes_json": json.dumps({
                "STYLE": {
                    "Neck Style": "V-Neck",
                    "Apparel Silhouette": "A-line",
                    "Sleeve Type": "Sleeveless",
                },
                "MATERIALS & CARE": {"Material Type": "Polyester Blend"},
            }),
        },
        "evidence": {},
    }]

    canonical = build_canonical_152(packet, {}, records, load_canonical_spec())

    expected = {item["key"] for item in load_canonical_spec()}
    assert set(canonical["values"]) == expected
    assert set(canonical["statuses"]) == expected
    assert set(canonical["evidence"]) == expected
    assert canonical["values"]["cp.title_base"]["en"] == packet["basic_facts"]["title"]
    assert canonical["values"]["cp.brand"] == "Example"
    assert canonical["values"]["cp.your_price"] == 29.99
    assert canonical["values"]["cp.currency"] == "USD"
    assert canonical["values"]["cp.neckline"] == "V Neck"
    assert canonical["values"]["cp.sleeve_length"] == "Sleeveless"
    assert canonical["values"]["cp.silhouette"] == "A-line"
    assert canonical["evidence"]["cp.neckline"] == ["ev_title"]
    assert canonical["evidence"]["cp.sleeve_length"] == ["ev_title"]
    assert canonical["evidence"]["cp.silhouette"] == ["ev_title"]
