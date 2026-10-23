"""Unit tests for the zettle package."""

import json
import base64
import pytest
from unittest.mock import patch, MagicMock

import zettle
from zettle import (
    get_all_products,
    get_product,
    find_products,
    find_product_by_name,
    update_product,
    update_product_price,
    create_product,
    delete_product,
    get_categories,
    ZettleClient,
    ZettleAuthError,
    ZettleNotFoundError,
    ZettleRequestError,
)


def _make_dummy_jwt(payload: dict) -> str:
    """Helper to generate a mock JWT for testing."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode("utf-8").rstrip("=")
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8").rstrip("=")
    return f"{header}.{payload_b64}.dummy_signature"


@pytest.fixture
def mock_jwt():
    return _make_dummy_jwt({"client_id": "test-client-id-123", "iss": "test-client-id-123"})


@pytest.fixture
def sample_product():
    return {
        "uuid": "prod-uuid-1",
        "name": "IPA Draft Beer",
        "description": "Crisp IPA on tap",
        "etag": "etag-v1",
        "variants": [
            {
                "uuid": "var-uuid-1",
                "name": "Pint (50cl)",
                "price": {
                    "amount": 7500,
                    "currencyId": "SEK"
                }
            },
            {
                "uuid": "var-uuid-2",
                "name": "Half Pint (25cl)",
                "price": {
                    "amount": 4500,
                    "currencyId": "SEK"
                }
            }
        ]
    }


def test_jwt_client_id_extraction(mock_jwt):
    extracted = ZettleClient._extract_client_id_from_jwt(mock_jwt)
    assert extracted == "test-client-id-123"


def test_missing_api_key_raises_auth_error():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ZettleAuthError):
            ZettleClient(api_key=None, client_id=None)


def test_token_acquisition_and_caching(mock_jwt, tmp_path):
    cache_file = tmp_path / "cache.json"
    client = ZettleClient(api_key=mock_jwt, client_id="test-client-id-123", token_cache_file=cache_file)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": "mock-access-token", "expires_in": 7200}

    with patch("requests.post", return_value=mock_resp) as mock_post:
        token = client.get_access_token()
        assert token == "mock-access-token"
        assert mock_post.call_count == 1

        # Second call should use cached token
        token2 = client.get_access_token()
        assert token2 == "mock-access-token"
        assert mock_post.call_count == 1


def test_persistent_token_cache_across_instances(mock_jwt, tmp_path):
    cache_file = tmp_path / "test_token_cache.json"

    # Instance 1: acquires token from OAuth and persists to disk
    client1 = ZettleClient(
        api_key=mock_jwt,
        client_id="test-client-id-123",
        token_cache_file=cache_file,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": "persistent-access-token", "expires_in": 7200}

    with patch("requests.post", return_value=mock_resp) as mock_post:
        token1 = client1.get_access_token()
        assert token1 == "persistent-access-token"
        assert mock_post.call_count == 1
        assert cache_file.exists()

    # Instance 2: completely new client instance with no in-memory cache
    client2 = ZettleClient(
        api_key=mock_jwt,
        client_id="test-client-id-123",
        token_cache_file=cache_file,
    )

    # Calling get_access_token on client2 should load from disk WITHOUT calling requests.post
    with patch("requests.post") as mock_post2:
        token2 = client2.get_access_token()
        assert token2 == "persistent-access-token"
        mock_post2.assert_not_called()

    # Verify token info reflects persistent disk cache
    info = client2.get_token_info()
    assert info["is_authenticated"] is True
    assert info["persisted_on_disk"] is True
    assert info["expires_in_seconds"] > 6000

    # Test clearing token cache
    client2.clear_token_cache()
    assert not cache_file.exists()
    assert client2.get_token_info()["is_authenticated"] is False


def test_get_all_products(mock_jwt, sample_product):
    client = ZettleClient(api_key=mock_jwt, client_id="test-client-id-123")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.ok = True
    mock_resp.json.return_value = [sample_product]

    with patch.object(client, "get_access_token", return_value="dummy-token"):
        with patch("requests.request", return_value=mock_resp):
            products = get_all_products(client=client)
            assert len(products) == 1
            assert products[0]["name"] == "IPA Draft Beer"
            # Verify JSON serializability
            assert json.loads(json.dumps(products)) == products


def test_get_product(mock_jwt, sample_product):
    client = ZettleClient(api_key=mock_jwt, client_id="test-client-id-123")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.ok = True
    mock_resp.json.return_value = sample_product

    with patch.object(client, "get_access_token", return_value="dummy-token"):
        with patch("requests.request", return_value=mock_resp):
            prod = get_product("prod-uuid-1", client=client)
            assert prod["uuid"] == "prod-uuid-1"
            assert prod["name"] == "IPA Draft Beer"
            assert json.loads(json.dumps(prod)) == prod


def test_find_products(mock_jwt, sample_product):
    client = ZettleClient(api_key=mock_jwt, client_id="test-client-id-123")
    wine_prod = {
        "uuid": "prod-uuid-2",
        "name": "House Red Wine",
        "description": "Glass of Merlot",
        "variants": [{"uuid": "var-2", "name": "Glass", "price": {"amount": 8000, "currencyId": "SEK"}}]
    }

    with patch("zettle.products.get_all_products", return_value=[sample_product, wine_prod]):
        # Search for 'beer'
        beers = find_products(query="beer", client=client)
        assert len(beers) == 1
        assert beers[0]["name"] == "IPA Draft Beer"

        # Search for 'wine'
        wines = find_products(name="Wine", client=client)
        assert len(wines) == 1
        assert wines[0]["name"] == "House Red Wine"

        # Search by variant name
        pints = find_products(query="Pint", client=client)
        assert len(pints) == 1
        assert pints[0]["name"] == "IPA Draft Beer"

        # Find by name
        found = find_product_by_name("IPA Draft Beer", exact=True, client=client)
        assert found is not None
        assert found["uuid"] == "prod-uuid-1"

        not_found = find_product_by_name("Nonexistent", exact=True, client=client)
        assert not_found is None


def test_update_product(mock_jwt, sample_product):
    client = ZettleClient(api_key=mock_jwt, client_id="test-client-id-123")

    updated_product = dict(sample_product)
    updated_product["name"] = "IPA Draft Beer (Updated)"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.ok = True
    mock_resp.json.return_value = updated_product

    with patch.object(client, "get_access_token", return_value="dummy-token"):
        with patch("requests.request", return_value=mock_resp) as mock_req:
            res = update_product("prod-uuid-1", {"name": "IPA Draft Beer (Updated)"}, etag="etag-v1", client=client)
            assert res["name"] == "IPA Draft Beer (Updated)"
            assert mock_req.call_args[1]["headers"]["If-Match"] == "etag-v1"
            assert json.loads(json.dumps(res)) == res


def test_update_product_price(mock_jwt, sample_product):
    client = ZettleClient(api_key=mock_jwt, client_id="test-client-id-123")

    with patch("zettle.products.get_product", return_value=sample_product):
        with patch("zettle.products.update_product") as mock_update:
            mock_update.return_value = {"success": True, "updated": True}
            
            # Update price of first variant to 85.50 SEK
            res = update_product_price("prod-uuid-1", price=85.50, client=client)
            assert res == {"success": True, "updated": True}

            mock_update.assert_called_once()
            call_args = mock_update.call_args
            payload = call_args[0][1]
            assert payload["variants"][0]["price"]["amount"] == 8550
            assert payload["variants"][0]["price"]["currencyId"] == "SEK"
            assert call_args[1]["etag"] == "etag-v1"


def test_create_and_delete_product(mock_jwt):
    client = ZettleClient(api_key=mock_jwt, client_id="test-client-id-123")

    # Test create
    new_prod_data = {
        "name": "Cocktail Old Fashioned",
        "variants": [{"name": "Standard", "price": {"amount": 12000, "currencyId": "SEK"}}]
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.ok = True
    mock_resp.json.return_value = {"uuid": "new-uuid", **new_prod_data}

    with patch.object(client, "get_access_token", return_value="dummy-token"):
        with patch("requests.request", return_value=mock_resp):
            created = create_product(new_prod_data, client=client)
            assert created["uuid"] == "new-uuid"
            assert created["name"] == "Cocktail Old Fashioned"
            assert json.loads(json.dumps(created)) == created

    # Test delete
    mock_del_resp = MagicMock()
    mock_del_resp.status_code = 204
    mock_del_resp.ok = True
    mock_del_resp.content = b""

    with patch.object(client, "get_access_token", return_value="dummy-token"):
        with patch("requests.request", return_value=mock_del_resp):
            res = delete_product("prod-uuid-1", client=client)
            assert res["success"] is True
            assert res["deleted_uuid"] == "prod-uuid-1"
            assert json.loads(json.dumps(res)) == res


def test_error_handling(mock_jwt):
    client = ZettleClient(api_key=mock_jwt, client_id="test-client-id-123")

    # 404 Not Found
    mock_resp_404 = MagicMock()
    mock_resp_404.status_code = 404
    mock_resp_404.ok = False

    with patch.object(client, "get_access_token", return_value="dummy-token"):
        with patch("requests.request", return_value=mock_resp_404):
            with pytest.raises(ZettleNotFoundError):
                get_product("missing-uuid", client=client)

