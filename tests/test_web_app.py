"""Unit tests for the web interface endpoints."""

import json
import pytest
from web_app import app, DEMO_PRODUCTS


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"WallStreetBar" in response.data
    assert b"Zettle Helper" in response.data


def test_api_status(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.get_json()
    assert "demo_mode" in data or "is_authenticated" in data


def test_api_get_products_demo(client):
    response = client.get("/api/products?demo=1")
    assert response.status_code == 200
    products = response.get_json()
    assert isinstance(products, list)
    assert len(products) >= 1
    # Check JSON serializable
    assert json.loads(json.dumps(products)) == products


def test_api_search_products_demo(client):
    response = client.get("/api/products?demo=1&query=IPA")
    assert response.status_code == 200
    products = response.get_json()
    assert any("IPA" in p["name"] for p in products)


def test_api_get_single_product_demo(client):
    prod_uuid = DEMO_PRODUCTS[0]["uuid"]
    response = client.get(f"/api/products/{prod_uuid}?demo=1")
    assert response.status_code == 200
    data = response.get_json()
    assert data["uuid"] == prod_uuid
    assert json.loads(json.dumps(data)) == data


def test_api_update_price_demo(client):
    prod_uuid = DEMO_PRODUCTS[0]["uuid"]
    response = client.post(
        f"/api/products/{prod_uuid}/price?demo=1",
        json={"price": 82.50},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    # Verify the updated variant price in cents
    assert data["product"]["variants"][0]["price"]["amount"] == 8250


def test_api_create_and_delete_product_demo(client):
    # Create
    create_resp = client.post(
        "/api/products?demo=1",
        json={"name": "Test Cider", "price": 55.0, "currency": "SEK"},
    )
    assert create_resp.status_code == 201
    created = create_resp.get_json()
    assert created["name"] == "Test Cider"

    # Delete
    del_resp = client.delete(f"/api/products/{created['uuid']}?demo=1")
    assert del_resp.status_code == 200
    assert del_resp.get_json()["success"] is True

