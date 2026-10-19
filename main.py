import os
import csv
import json
import base64
import socket
import requests
import urllib3.util.connection as urllib3_cn

from dotenv import load_dotenv
from typing import Any, Dict, List
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

load_dotenv()

ZETTLE_API_KEY = os.getenv("ZETTLE_API_KEY")
CLIENT_ID = os.getenv("ZETTLE_CLIENT_ID")

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


def download_september_sales_csv(
    output_file: str = "september_sales.csv",
    year: int = 2026,
) -> int:
    """
    Fetches all sales for the month of September (paginating through all batches)
    and writes the original purchase records to a CSV file.
    Returns the total number of sales saved.
    """
    headers = get_headers()
    start_date = f"{year}-09-01T00:00:00Z"
    end_date = f"{year}-10-01T00:00:00Z"

    all_purchases: List[Dict[str, Any]] = []
    last_hash = None

    with requests.Session() as session:
        session.headers.update(headers)

        while True:
            params: Dict[str, Any] = {
                "startDate": start_date,
                "endDate": end_date,
                "limit": 1000,
                "descending": "false",
            }
            if last_hash:
                params["lastPurchaseHash"] = last_hash

            resp = session.get(PURCHASE_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            purchases = data.get("purchases", [])
            if not purchases:
                break

            all_purchases.extend(purchases)
            last_hash = data.get("lastPurchaseHash")

            # If fewer than 1000 items were returned or no next cursor exists, we hit the end
            if not last_hash or len(purchases) < 1000:
                break

    if not all_purchases:
        return 0

    # Collect all top-level keys across all purchase records to form the CSV header
    fieldnames = list(dict.fromkeys(k for p in all_purchases for k in p.keys()))

    with open(output_file, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for purchase in all_purchases:
            # Keep scalar values as-is; serialize nested dicts/lists as JSON strings
            row = {
                k: json.dumps(v, ensure_ascii=False)
                if isinstance(v, (dict, list))
                else v
                for k, v in purchase.items()
            }
            writer.writerow(row)

    return len(all_purchases)


def print_catalog() -> None:
    """
    Fetches and pretty-prints the Zettle product catalog with names,
    UUIDs, variants, and prices.
    Requires scope: READ:PRODUCT
    """
    headers = get_headers()
    resp = requests.get(PRODUCTS_URL, headers=headers, timeout=10)
    resp.raise_for_status()
    products = resp.json()

    if not products:
        print("Catalog is empty.")
        return

    print(f"\n{'='*60}")
    print(f"ZETTLE PRODUCT CATALOG ({len(products)} items found)")
    print(f"{'='*60}")

    for p in products:
        name = p.get("name", "Unnamed Product")
        product_uuid = p.get("uuid", "N/A")
        print(f"\n📌 Product: {name}")
        print(f"   Product UUID: {product_uuid}")

        variants = p.get("variants", [])
        for v in variants:
            v_name = v.get("name", "Default Variant")
            v_uuid = v.get("uuid", "N/A")
            price_obj = v.get("price", {})
            amount_minor = price_obj.get("amount", 0)
            currency = price_obj.get("currencyId", "DKK")
            price_major = amount_minor / 100.0

            print(f"     ├── Variant: {v_name}")
            print(f"     │   Variant UUID: {v_uuid}")
            print(f"     └── Price: {price_major:.2f} {currency}")

    print(f"\n{'='*60}")


#download_september_sales_csv()
update_item_prices({"Flaske Spiritus": 1.0})
#print_catalog()
