import pytest
from fastapi.testclient import TestClient
from main import app, ticker, predictor
from zettle.purchases import extract_variant_sales

client = TestClient(app)

def test_extract_variant_sales():
    sample_purchases = [
        {
            "purchaseUUID": "p-1",
            "products": [
                {"productUuid": "prod-1", "variantUuid": "var-1", "quantity": 3},
                {"productUuid": "prod-2", "variantUuid": "var-2", "quantity": 1},
            ],
        },
        {
            "purchaseUUID": "p-2",
            "products": [
                {"productUuid": "prod-1", "variantUuid": "var-1", "quantity": 2},
            ],
        },
    ]
    by_variant, by_product = extract_variant_sales(sample_purchases)

    # variant matching
    assert by_variant["var-1"] == 5.0
    assert by_variant["var-2"] == 1.0

    # product matching
    assert by_product["prod-1"] == 5.0
    assert by_product["prod-2"] == 1.0

def test_extract_variant_sales_date_filter():
    # Purchases before and after Saturday Nov 7 13:00 CET (12:00 UTC)
    purchases = [
        {
            "purchaseUUID": "old-1",
            "timestamp": "2026-11-07T11:59:59Z", # Before 13:00 CET
            "products": [{"variantUuid": "var-old", "productUuid": "prod-old", "quantity": 10}],
        },
        {
            "purchaseUUID": "valid-1",
            "timestamp": "2026-11-07T13:00:01+01:00", # After 13:00 CET
            "products": [{"variantUuid": "var-new", "productUuid": "prod-new", "quantity": 4}],
        },
    ]
    by_variant, by_product = extract_variant_sales(purchases)
    assert "var-old" not in by_variant
    assert "prod-old" not in by_product
    assert by_variant.get("var-new") == 4.0
    assert by_product.get("prod-new") == 4.0

def test_ticker_status_and_controls():
    # Check status endpoint
    res = client.get("/api/engine/ticker/status")
    assert res.status_code == 200
    data = res.json()
    assert "is_running" in data
    assert "interval_minutes" in data
    assert "bank_balance" in data

    # Update interval
    res = client.post("/api/engine/ticker/interval", json={"interval_minutes": 4.5})
    assert res.status_code == 200
    assert res.json()["ticker"]["interval_minutes"] == 4.5

    # Pause ticker
    res = client.post("/api/engine/ticker/pause")
    assert res.status_code == 200
    assert res.json()["ticker"]["is_running"] is False

    # Start ticker
    res = client.post("/api/engine/ticker/start", json={"interval_minutes": 2.0})
    assert res.status_code == 200
    assert res.json()["ticker"]["is_running"] is True
    assert res.json()["ticker"]["interval_minutes"] == 2.0

    # Clean up (pause)
    client.post("/api/engine/ticker/pause")

def test_manual_tick():
    res = client.post("/api/engine/ticker/tick")
    assert res.status_code == 200
    ticker_data = res.json()["ticker"]
    assert ticker_data["total_ticks"] >= 1

def test_crash_predictor_math():
    from predictor import DynamicPricingPredictor
    p = DynamicPricingPredictor(delta_rel=0.0, sigma=0.0, lambda_rel=0.0)

    # Tick 1: Normal (proper price = 100)
    prices_normal = p.predict_prices(
        current_prices=[100.0],
        min_prices=[10.0],
        normal_prices=[100.0],
        quantities=[0.0],
        uuids=["item1"],
        commit=True,
    )
    assert prices_normal["item1"] == 100.0
    assert p.proper_prices["item1"] == 100.0

    # Turn crash ON
    p.crash = True

    # Tick 2: Crash active -> returns 70% (70.0)
    prices_crashed_1 = p.predict_prices(
        current_prices=[100.0],
        min_prices=[10.0],
        normal_prices=[100.0],
        quantities=[0.0],
        uuids=["item1"],
        commit=True,
    )
    assert prices_crashed_1["item1"] == 70.0
    assert p.proper_prices["item1"] == 100.0

    # Tick 3: Second tick while crashed. Even if 70.0 is passed in, it should NOT do 70 * 0.7 = 49!
    prices_crashed_2 = p.predict_prices(
        current_prices=[70.0],
        min_prices=[10.0],
        normal_prices=[100.0],
        quantities=[0.0],
        uuids=["item1"],
        commit=True,
    )
    assert prices_crashed_2["item1"] == 70.0
    assert p.proper_prices["item1"] == 100.0

    # Turn crash OFF -> immediately reverts to 100.0
    p.crash = False
    prices_reverted = p.predict_prices(
        current_prices=[70.0],
        min_prices=[10.0],
        normal_prices=[100.0],
        quantities=[0.0],
        uuids=["item1"],
        commit=True,
    )
    assert prices_reverted["item1"] == 100.0
    assert p.proper_prices["item1"] == 100.0

def test_crash_api_endpoints():
    # 1. Check crash status endpoint
    res = client.get("/api/engine/crash")
    assert res.status_code == 200
    assert "crash" in res.json()

    # 2. Get baseline catalog
    cat_res = client.get("/api/catalog/managed")
    base_prod = cat_res.json()[0]
    uncrashed_val = base_prod.get("uncrashed_price", base_prod["current_price"])

    # 3. Activate crash via POST /api/engine/crash
    res = client.post("/api/engine/crash", json={"crash": True})
    assert res.status_code == 200
    assert res.json()["crash"] is True

    # Verify catalog prices are discounted by 30%
    cat_res_crashed = client.get("/api/catalog/managed")
    crashed_prod = cat_res_crashed.json()[0]
    expected_crashed = round(uncrashed_val * 0.7, 2)
    assert abs(crashed_prod["current_price"] - expected_crashed) < 0.05
    assert crashed_prod["uncrashed_price"] == uncrashed_val

    # 4. Revert crash via POST /api/engine/crash
    res = client.post("/api/engine/crash", json={"crash": False})
    assert res.status_code == 200
    assert res.json()["crash"] is False

    # Verify catalog prices are restored
    cat_res_reverted = client.get("/api/catalog/managed")
    reverted_prod = cat_res_reverted.json()[0]
    assert reverted_prod["current_price"] == uncrashed_val
