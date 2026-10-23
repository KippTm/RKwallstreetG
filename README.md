# Zettle Helper Package for WallStreetBar

A lightweight, robust Python helper for integrating with the **Zettle Product Library API**.

This package provides python-callable functions to:
* **Find all products** in the merchant's Zettle product library.
* **View current product details** by UUID or name.
* **Change / update product details and prices** (including JSON merge patch and concurrency control).
* Output all results strictly as **JSON-serializable Python objects** (`dict` and `list`).

---

## 1. Setup & Credentials

In the project root, a [`.env`](file:///Users/suneg/Dev/WallStreetBar/.env) file contains your credentials:

```env
# Zettle API Credentials
# Obtain your API key and Client ID from https://my.zettle.com/apps/api-keys
# Required scopes: READ:PRODUCT, WRITE:PRODUCT

ZETTLE_API_KEY=your_api_key_or_jwt_assertion_here
ZETTLE_CLIENT_ID=your_client_id_here
```

> **Note on Token Persistence:**
> * When an access token is acquired, it is **persisted to disk** in `.zettle_token_cache.json` (with `0600` permissions).
> * Future function calls across separate Python script executions will reuse this valid token without making extra OAuth network calls until it nears expiration (2 hours).
> * You can inspect the persistent token state at any time via `zettle.get_token_info()`, or invalidate it using `zettle.clear_token_cache()`.

---

## 2. Interactive Testing Web Interface

We have provided a testing web interface to visually inspect products, test live price updates, and execute raw python functions with instant JSON feedback:

```bash
python3 web_app.py
```
Open **`http://127.0.0.1:5050`** in your browser.

Features included:
* **Product Explorer & Search:** Browse products and search by name, variant, or SKU.
* **Instant Price Changer:** One-click price change modal for any product/variant.
* **Raw JSON Inspector:** Inspect full payload returned by `get_product`.
* **Function Runner Playground:** Test calls to `get_all_products`, `find_products`, `update_product_price`, and view execution times and raw JSON outputs.
* **Persistent Token Status Badge:** Shows token lifetime countdown, disk cache status, and force-refresh button.
* **Demo Simulation Mode:** If no live `.env` credentials are set yet, the UI provides a simulated bar catalog (IPAs, Pilsners, Merlot, Espresso Martinis) to test all functions safely.

---

## 3. Quickstart Python Usage

```python
import json
import zettle

# Check persistent token status
print(zettle.get_token_info())

# 1. Fetch all products
products = zettle.get_all_products()
print(f"Loaded {len(products)} products.")

# 2. Find products by search term or name
beers = zettle.find_products(query="beer")
ipa = zettle.find_product_by_name("IPA Draft", exact=False)

# 3. View current details of a specific product
if ipa:
    product_uuid = ipa["uuid"]
    current_product = zettle.get_product(product_uuid)
    print("Current Product:", json.dumps(current_product, indent=2))

    # 4. Change / update product price (ideal for continuous bar pricing)
    # Sets price to 65.00 (automatically converted to minor currency units/cents: 6500)
    updated = zettle.update_product_price(product_uuid, price=65.00)
    print("New Price Confirmed:", updated)

    # 5. Change custom product fields via JSON merge patch
    zettle.update_product(product_uuid, {
        "description": "Fresh cold IPA on tap - Happy Hour!"
    })
```

---

## 4. Available Functions

All functions return **JSON-serializable Python objects** (`dict` or `list`):

| Function | Description | Return Type |
| :--- | :--- | :--- |
| `get_all_products()` | Fetch all products in the POS product library | `list[dict]` |
| `get_product(product_uuid)` | View current details of a specific product | `dict` |
| `find_products(query=None, name=None, category_uuid=None)` | Search products by text, name substring, or category | `list[dict]` |
| `find_product_by_name(name, exact=False)` | Find a single product by name | `dict` or `None` |
| `update_product(product_uuid, product_data, etag=None)` | Update product fields using JSON merge patch | `dict` |
| `update_product_price(product_uuid, price, currency=None, variant_uuid=None, is_cents=False)` | Update variant price (supports decimals or minor units) | `dict` |
| `create_product(product_data)` | Create a new product in the library | `dict` |
| `delete_product(product_uuid)` | Delete a product from the library | `dict` |
| `get_categories()` | Retrieve all product categories | `list[dict]` |
| `get_token_info()` | View persistent token status and time to expiry | `dict` |
| `clear_token_cache()` | Invalidate in-memory and on-disk persistent token | `dict` |

---

## 5. Running Tests

Run the test suite with `pytest`:

```bash
python3 -m pytest tests/ -v
```

