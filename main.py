import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
import requests
from dotenv import load_dotenv

load_dotenv()

ZETTLE_API_KEY = os.getenv("ZETTLE_API_KEY")
CLIENT_ID = os.getenv("ZETTLE_CLIENT_ID", "your-client-id")

AUTH_URL = "https://oauth.zettle.com/token"
PURCHASE_URL = "https://purchase.izettle.com/purchases/v2"
PRODUCTS_URL = "https://products.izettle.com/organizations/self/products"


def get_headers() -> Dict[str, str]:
    """Exchanges the API key (assertion) for a Bearer token and returns headers."""
    resp = requests.post(
        AUTH_URL,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "client_id": CLIENT_ID,
            "assertion": ZETTLE_API_KEY,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def fetch_recent_sales(minutes: int) -> List[Dict[str, Any]]:
    """Fetches the newest sales made in the last `minutes` time period."""
    headers = get_headers()
    start_date = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    params = {
        "startDate": start_date,
        "descending": "true",
    }
    resp = requests.get(PURCHASE_URL, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json().get("purchases", [])


def _update_single_product(
    session: requests.Session,
    uuid: str,
    new_price_minor: int,
    currency: str,
) -> str:
    """Helper to fetch the latest ETag and PUT the updated price for one product."""
    prod_resp = session.get(f"{PRODUCTS_URL}/{uuid}", timeout=10)
    prod_resp.raise_for_status()

    etag = prod_resp.headers.get("ETag")
    product_data = prod_resp.json()

    for variant in product_data.get("variants", []):
        variant["price"] = {"amount": new_price_minor, "currencyId": currency}

    put_resp = session.put(
        f"{PRODUCTS_URL}/v2/{uuid}",
        headers={"If-Match": etag},
        json=product_data,
        timeout=10,
    )
    put_resp.raise_for_status()
    return product_data.get("name", uuid)


def update_item_prices(
    price_updates: Dict[str, float],
    currency: str = "DKK",
    max_workers: int = 10,
) -> List[str]:
    """
    Batch-updates prices concurrently for the items specified in `price_updates`.
    - Items NOT in `price_updates` are left untouched.
    - Items already at the target price are skipped to save API calls.
    Returns a list of item names that were actually updated.
    """
    if not price_updates:
        return []

    headers = get_headers()

    with requests.Session() as session:
        session.headers.update(headers)

        # 1. Fetch full catalog once to map item names -> UUIDs and check current prices
        resp = session.get(f"{PRODUCTS_URL}/v2", timeout=10)
        resp.raise_for_status()
        catalog = {p.get("name"): p for p in resp.json() if p.get("name")}

        tasks = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for item_name, new_price in price_updates.items():
                product = catalog.get(item_name)
                if not product:
                    print(f"Warning: '{item_name}' not found in Zettle catalog. Skipping.")
                    continue

                new_price_minor = int(round(new_price * 100))

                # Check if all variants already have this exact price
                variants = product.get("variants", [])
                if variants and all(
                    v.get("price", {}).get("amount") == new_price_minor
                    for v in variants
                ):
                    continue  # Price hasn't changed; skip network call

                future = executor.submit(
                    _update_single_product,
                    session,
                    product["uuid"],
                    new_price_minor,
                    currency,
                )
                tasks[future] = item_name

            updated_items = []
            for future in as_completed(tasks):
                item_name = tasks[future]
                try:
                    updated_items.append(future.result())
                except requests.HTTPError as err:
                    print(f"Failed to update '{item_name}': {err}")

    return updated_items


def predict_prices(sales: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Dummy function to predict item prices based on recent sales.
    Takes a list of sales and returns a dict mapping {item_name: price}.
    Only needs to return items whose prices you want to set/change.
    """
    pass


def reset_all_prices(standard_prices: Dict[str, float]) -> None:
    """
    Resets the price of all items in `standard_prices` back to their baseline.
    Since `update_item_prices` already takes a {name: price} dict, this can
    delegate directly to `update_item_prices(standard_prices)` when ready.
    """
    pass
