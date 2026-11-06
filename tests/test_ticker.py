import pytest
from fastapi.testclient import TestClient
from main import app, ticker, predictor
from zettle.purchases import extract_variant_sales

client = TestClient(app)

def test_extract_variant_sales():
    sample_purchases = [
        {
            "purchaseUUID": "p-1",
            "lines": [
                {"productUuid": "prod-1", "variantUuid": "var-1", "quantity": 3},
                {"productUuid": "prod-2", "variantUuid": "var-2", "quantity": 1},
            ],
        },
        {
            "purchaseUUID": "p-2",
            "lines": [
                {"productUuid": "prod-1", "variantUuid": "var-1", "quantity": 2},
            ],
        },
    ]
    sales = extract_variant_sales(sample_purchases)
    assert sales["var-1"] == 5.0
    assert sales["var-2"] == 1.0

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
