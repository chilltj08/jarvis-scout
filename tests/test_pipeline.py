from pathlib import Path

from jarvis_scout.pipeline import discover


def test_demo_matches_only_exact_pack_sizes():
    matches = discover()
    assert len(matches) == 3
    assert all(x.supplier_offer.pack_quantity == x.amazon_listing.pack_quantity for x in matches)
    assert not any(x.supplier_offer.ean == "9000000000004" for x in matches)


def test_threshold_filters():
    matches = discover(minimum_profit=100)
    assert matches == []


def test_brand_mismatch_is_rejected(tmp_path: Path):
    supplier = tmp_path / "supplier.csv"
    amazon = tmp_path / "amazon.csv"
    supplier.write_text(
        "ean,name,brand,pack_quantity,supplier,buy_price,inbound_cost\n"
        "123,Example,BrandA,1,Test,2,0\n", encoding="utf-8"
    )
    amazon.write_text(
        "ean,name,brand,pack_quantity,selling_price,referral_rate,fulfilment_cost\n"
        "123,Example,BrandB,1,20,0.15,3\n", encoding="utf-8"
    )
    assert discover(supplier_path=supplier, amazon_path=amazon) == []
