from agent.skills.deal_finder import DealFinderEngine, Deal

def test_deal_deduplication_and_drop_detection():
    engine = DealFinderEngine()

    # Run 1: Initial discovery of mouse at $25.00
    deal_run1 = [
        Deal(
            title="Logitech Wireless Mouse",
            price=25.00,
            original_price=35.00,
            url="http://mockshop.local/item/1"
        )
    ]
    res1 = engine.process_findings(deal_run1)
    assert res1["total_deals"] == 1
    assert len(res1["flagged_drops"]) == 0, "First observation shouldn't flag a price drop"

    # Run 2: Price drop to $18.00 on the same mouse
    deal_run2 = [
        Deal(
            title="Logitech Wireless Mouse",
            price=18.00,
            original_price=35.00,
            url="http://mockshop.local/item/1"
        )
    ]
    res2 = engine.process_findings(deal_run2)
    assert res2["total_deals"] == 1
    assert len(res2["flagged_drops"]) == 1, "Price drop from $25 to $18 should be flagged!"
    drop_item = res2["flagged_drops"][0]
    assert drop_item["price"] == 18.00
    assert drop_item["savings"] == 7.00
    print("✔ M5 Deal Finder drop detection verified: saved $7.00!")

    # Verify CSV Export
    csv_str = engine.export_csv(res2["deals"])
    assert "Logitech Wireless Mouse" in csv_str
    assert "18.0" in csv_str
    print("✔ M5 Deal Finder CSV export format verified!")

if __name__ == "__main__":
    test_deal_deduplication_and_drop_detection()
