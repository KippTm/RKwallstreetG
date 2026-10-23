#!/usr/bin/env python3
"""
Zettle Interactive Testing Web Interface.

A lightweight Flask web application to test, view, find, and update Zettle products
using the python functions in the zettle package.
"""

import os
import json
import time
from typing import Any, Dict, List
from flask import Flask, jsonify, render_template_string, request

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
    get_token_info,
    clear_token_cache,
    reset_client,
    get_client,
    ZettleError,
    ZettleAuthError,
    ZettleNotFoundError,
)

app = Flask(__name__)

# In-memory mock store for Demo Mode (when real credentials are not yet configured)
DEMO_PRODUCTS: List[Dict[str, Any]] = [
    {
        "uuid": "demo-beer-ipa-001",
        "name": "Wall Street IPA (Draft)",
        "description": "Hoppy craft IPA, 6.2% ABV",
        "etag": "demo-etag-1",
        "variants": [
            {
                "uuid": "demo-var-pint-1",
                "name": "Pint (50cl)",
                "price": {"amount": 7500, "currencyId": "SEK"},
            },
            {
                "uuid": "demo-var-half-1",
                "name": "Half Pint (25cl)",
                "price": {"amount": 4500, "currencyId": "SEK"},
            },
        ],
    },
    {
        "uuid": "demo-beer-lager-002",
        "name": "Bull Market Pilsner",
        "description": "Crisp German-style pilsner, 5.0% ABV",
        "etag": "demo-etag-2",
        "variants": [
            {
                "uuid": "demo-var-pint-2",
                "name": "Pint (50cl)",
                "price": {"amount": 6500, "currencyId": "SEK"},
            }
        ],
    },
    {
        "uuid": "demo-wine-merlot-003",
        "name": "Bear Market Red (Merlot)",
        "description": "Rich French Merlot 2021",
        "etag": "demo-etag-3",
        "variants": [
            {
                "uuid": "demo-var-glass-3",
                "name": "Glass",
                "price": {"amount": 9000, "currencyId": "SEK"},
            },
            {
                "uuid": "demo-var-bottle-3",
                "name": "Bottle (75cl)",
                "price": {"amount": 38000, "currencyId": "SEK"},
            },
        ],
    },
    {
        "uuid": "demo-cocktail-004",
        "name": "Short Squeeze Espresso Martini",
        "description": "Vodka, fresh espresso, Kahlúa",
        "etag": "demo-etag-4",
        "variants": [
            {
                "uuid": "demo-var-cocktail-4",
                "name": "Standard",
                "price": {"amount": 13500, "currencyId": "SEK"},
            }
        ],
    },
]


def is_demo_mode() -> bool:
    """Check if demo mode should be used (e.g. if real credentials are not set)."""
    if request.args.get("demo") == "1" or request.headers.get("X-Demo-Mode") == "true":
        return True
    api_key = os.getenv("ZETTLE_API_KEY", "").strip()
    return not api_key or api_key == "your_zettle_api_key_here" or api_key == "your_api_key_or_jwt_assertion_here"


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.route("/api/status", methods=["GET"])
def api_status():
    """Return auth status and persistent token information."""
    demo = is_demo_mode()
    if demo:
        return jsonify({
            "demo_mode": True,
            "is_authenticated": True,
            "has_api_key": False,
            "client_id": "demo-client-id",
            "persisted_on_disk": True,
            "cache_file": ".zettle_token_cache.json (Demo)",
            "expires_in_seconds": 6800,
            "expires_in_minutes": 113.3,
            "token_masked": "eyDemoTok...enValid",
            "message": "Running in Demo Mode. Set real ZETTLE_API_KEY in .env to connect to live Zettle.",
        })

    try:
        info = get_token_info()
        info["demo_mode"] = False
        return jsonify(info)
    except Exception as e:
        return jsonify({
            "demo_mode": False,
            "is_authenticated": False,
            "error": str(e),
            "has_api_key": bool(os.getenv("ZETTLE_API_KEY")),
        })


@app.route("/api/token/clear", methods=["POST"])
def api_clear_token():
    """Clear token cache."""
    clear_token_cache()
    reset_client()
    return jsonify({"success": True, "message": "Persistent token cache cleared."})


@app.route("/api/token/refresh", methods=["POST"])
def api_refresh_token():
    """Force refresh OAuth token."""
    if is_demo_mode():
        return jsonify({"success": True, "message": "Demo token refreshed (simulated)."})
    try:
        client = get_client()
        token = client.get_access_token(force_refresh=True)
        return jsonify({
            "success": True,
            "message": "Token refreshed successfully.",
            "token_masked": f"{token[:8]}...{token[-8:]}",
            "token_info": client.get_token_info(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/products", methods=["GET"])
def api_get_products():
    """Fetch all products or filtered products."""
    query = request.args.get("query")
    name = request.args.get("name")

    if is_demo_mode():
        results = DEMO_PRODUCTS
        if query:
            q = query.lower()
            results = [
                p for p in results
                if q in p["name"].lower() or q in p.get("description", "").lower()
                or any(q in v["name"].lower() for v in p.get("variants", []))
            ]
        elif name:
            results = [p for p in results if name.lower() in p["name"].lower()]
        return jsonify(results)

    try:
        if query or name:
            products = find_products(query=query, name=name)
        else:
            products = get_all_products()
        return jsonify(products)
    except ZettleAuthError as e:
        return jsonify({"error": str(e), "auth_error": True}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/products/<product_uuid>", methods=["GET"])
def api_get_single_product(product_uuid):
    """View details of a single product."""
    if is_demo_mode():
        for p in DEMO_PRODUCTS:
            if p["uuid"] == product_uuid:
                return jsonify(p)
        return jsonify({"error": "Product not found"}), 404

    try:
        product = get_product(product_uuid)
        return jsonify(product)
    except ZettleNotFoundError:
        return jsonify({"error": f"Product '{product_uuid}' not found."}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/products/<product_uuid>/price", methods=["POST"])
def api_update_price(product_uuid):
    """Change product price."""
    data = request.get_json() or {}
    new_price = data.get("price")
    variant_uuid = data.get("variant_uuid")
    currency = data.get("currency")
    is_cents = data.get("is_cents", False)

    if new_price is None:
        return jsonify({"error": "Missing 'price' parameter."}), 400

    try:
        new_price = float(new_price)
    except ValueError:
        return jsonify({"error": "Invalid price format."}), 400

    if is_demo_mode():
        for p in DEMO_PRODUCTS:
            if p["uuid"] == product_uuid:
                v = p["variants"][0]
                if variant_uuid:
                    for var in p["variants"]:
                        if var["uuid"] == variant_uuid:
                            v = var
                            break
                amt = int(round(new_price if is_cents else new_price * 100))
                v["price"]["amount"] = amt
                if currency:
                    v["price"]["currencyId"] = currency.upper()
                p["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                return jsonify({"success": True, "product": p, "demo": True})
        return jsonify({"error": "Product not found"}), 404

    try:
        res = update_product_price(
            product_uuid=product_uuid,
            price=new_price,
            currency=currency,
            variant_uuid=variant_uuid,
            is_cents=is_cents,
        )
        return jsonify({"success": True, "result": res})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/products/<product_uuid>", methods=["PUT"])
def api_update_product(product_uuid):
    """Update product fields using JSON merge patch."""
    data = request.get_json() or {}
    etag = request.headers.get("If-Match")

    if is_demo_mode():
        for p in DEMO_PRODUCTS:
            if p["uuid"] == product_uuid:
                p.update({k: v for k, v in data.items() if v is not None})
                return jsonify(p)
        return jsonify({"error": "Product not found"}), 404

    try:
        res = update_product(product_uuid, data, etag=etag)
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/products", methods=["POST"])
def api_create_product():
    """Create a new product."""
    data = request.get_json() or {}
    if not data.get("name"):
        return jsonify({"error": "Product name is required."}), 400

    if is_demo_mode():
        import uuid
        new_prod = {
            "uuid": f"demo-{uuid.uuid4().hex[:8]}",
            "name": data["name"],
            "description": data.get("description", ""),
            "variants": data.get("variants") or [
                {
                    "uuid": f"demo-var-{uuid.uuid4().hex[:8]}",
                    "name": "Standard",
                    "price": {"amount": int(data.get("price", 50.0) * 100), "currencyId": data.get("currency", "SEK")},
                }
            ],
        }
        DEMO_PRODUCTS.append(new_prod)
        return jsonify(new_prod), 201

    try:
        res = create_product(data)
        return jsonify(res), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/products/<product_uuid>", methods=["DELETE"])
def api_delete_product(product_uuid):
    """Delete a product."""
    if is_demo_mode():
        global DEMO_PRODUCTS
        DEMO_PRODUCTS = [p for p in DEMO_PRODUCTS if p["uuid"] != product_uuid]
        return jsonify({"success": True, "deleted_uuid": product_uuid})

    try:
        res = delete_product(product_uuid)
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/categories", methods=["GET"])
def api_categories():
    """Fetch categories."""
    if is_demo_mode():
        return jsonify([
            {"uuid": "cat-beers", "name": "Draft & Craft Beers"},
            {"uuid": "cat-wines", "name": "Wines"},
            {"uuid": "cat-cocktails", "name": "Cocktails & Spirits"},
        ])
    try:
        return jsonify(get_categories())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# HTML Frontend
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Zettle Products & Continuous Pricing Lab</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    pre { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
  </style>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen flex flex-col font-sans antialiased">

  <!-- Header -->
  <header class="border-b border-slate-800 bg-slate-950/80 backdrop-blur sticky top-0 z-40">
    <div class="max-w-7xl mx-auto px-4 py-3 flex flex-wrap items-center justify-between gap-4">
      <div class="flex items-center space-x-3">
        <div class="w-9 h-9 rounded-lg bg-emerald-500/20 text-emerald-400 flex items-center justify-center font-bold text-lg border border-emerald-500/30">
          <i class="fa-solid fa-chart-line"></i>
        </div>
        <div>
          <h1 class="text-base font-semibold text-white tracking-wide flex items-center gap-2">
            WallStreetBar <span class="text-xs px-2 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800 font-mono">Zettle Helper</span>
          </h1>
          <p class="text-xs text-slate-400">View, find, change products & continuous dynamic pricing</p>
        </div>
      </div>

      <!-- Auth & Persistence Badge -->
      <div id="authStatusBadge" class="flex items-center gap-3 text-xs bg-slate-800/80 px-3 py-1.5 rounded-lg border border-slate-700">
        <span class="inline-block w-2 h-2 rounded-full bg-yellow-400 animate-pulse" id="statusDot"></span>
        <span id="statusText" class="font-medium text-slate-300">Checking auth...</span>
        <span id="tokenExpiry" class="text-slate-400 hidden"></span>
        <button onclick="refreshAuthStatus()" title="Refresh Status" class="text-slate-400 hover:text-white transition">
          <i class="fa-solid fa-rotate"></i>
        </button>
      </div>

      <!-- Action Buttons -->
      <div class="flex items-center gap-2">
        <button onclick="openCreateModal()" class="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-md text-xs font-medium transition flex items-center gap-1.5 shadow-sm">
          <i class="fa-solid fa-plus"></i> New Product
        </button>
        <button onclick="openRawTester()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-md text-xs font-medium transition border border-slate-700 flex items-center gap-1.5">
          <i class="fa-solid fa-code"></i> Function Runner
        </button>
      </div>
    </div>
  </header>

  <!-- Banner for Demo / Real Mode -->
  <div id="demoBanner" class="hidden bg-amber-950/40 border-b border-amber-800/60 px-4 py-2.5 text-xs text-amber-200">
    <div class="max-w-7xl mx-auto flex items-center justify-between gap-4">
      <div class="flex items-center gap-2">
        <i class="fa-solid fa-circle-info text-amber-400"></i>
        <span><strong>Demo Mode Active:</strong> No live Zettle API key configured in <code>.env</code>. You can test all features (search, price updates, JSON inspection) with mock bar data!</span>
      </div>
      <span class="text-amber-400 font-mono text-[11px]">Edit .env to switch to live</span>
    </div>
  </div>

  <!-- Main Content -->
  <main class="max-w-7xl mx-auto px-4 py-6 flex-1 w-full space-y-6">

    <!-- Persistent Token Status Card -->
    <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
      <div class="bg-slate-800/60 border border-slate-800 rounded-xl p-4 flex flex-col justify-between">
        <span class="text-xs text-slate-400 uppercase tracking-wider font-semibold">Persistent Token</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span id="cardPersistStatus" class="text-sm font-semibold text-white">Checking...</span>
          <span class="text-xs text-emerald-400 font-mono"><i class="fa-solid fa-database mr-1"></i>Disk Cache</span>
        </div>
        <p class="text-[11px] text-slate-500 mt-1 truncate" id="cardCachePath">.zettle_token_cache.json</p>
      </div>

      <div class="bg-slate-800/60 border border-slate-800 rounded-xl p-4 flex flex-col justify-between">
        <span class="text-xs text-slate-400 uppercase tracking-wider font-semibold">Token Lifetime</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span id="cardExpiryMins" class="text-sm font-semibold text-white">--</span>
          <span class="text-xs text-slate-400 font-mono">2h refresh</span>
        </div>
        <div class="flex items-center gap-2 mt-2">
          <button onclick="forceRefreshToken()" class="text-[11px] text-emerald-400 hover:text-emerald-300 underline font-medium">Force Refresh</button>
          <span class="text-slate-600">•</span>
          <button onclick="clearToken()" class="text-[11px] text-rose-400 hover:text-rose-300 underline font-medium">Clear Cache</button>
        </div>
      </div>

      <div class="bg-slate-800/60 border border-slate-800 rounded-xl p-4 flex flex-col justify-between">
        <span class="text-xs text-slate-400 uppercase tracking-wider font-semibold">Products Loaded</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span id="cardProductCount" class="text-2xl font-bold text-white">0</span>
          <span class="text-xs text-slate-400">items in POS</span>
        </div>
        <p class="text-[11px] text-slate-500 mt-1">Direct from Product Library v2</p>
      </div>

      <div class="bg-slate-800/60 border border-slate-800 rounded-xl p-4 flex flex-col justify-between">
        <span class="text-xs text-slate-400 uppercase tracking-wider font-semibold">Serialisation</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-sm font-semibold text-emerald-400 flex items-center gap-1.5">
            <i class="fa-solid fa-check"></i> JSON-ready
          </span>
          <span class="text-xs text-slate-400 font-mono">dict / list</span>
        </div>
        <p class="text-[11px] text-slate-500 mt-1">100% Python native objects</p>
      </div>
    </div>

    <!-- Product Explorer Section -->
    <div class="bg-slate-950/60 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
      <!-- Toolbar -->
      <div class="p-4 border-b border-slate-800 flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3 flex-1 min-w-[280px]">
          <div class="relative flex-1">
            <i class="fa-solid fa-magnifying-glass absolute left-3 top-2.5 text-slate-500 text-xs"></i>
            <input type="text" id="searchInput" placeholder="Search by name, description, variant or SKU..."
                   oninput="handleSearch(this.value)"
                   class="w-full bg-slate-900 border border-slate-800 rounded-lg pl-8 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-emerald-500 transition">
          </div>
          <button onclick="loadProducts()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium border border-slate-700 flex items-center gap-1.5 transition">
            <i class="fa-solid fa-rotate-right text-slate-400"></i> Refresh
          </button>
        </div>
      </div>

      <!-- Table -->
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs border-collapse">
          <thead>
            <tr class="bg-slate-900/60 border-b border-slate-800 text-slate-400 font-medium">
              <th class="py-3 px-4">Product Name & Info</th>
              <th class="py-3 px-4">UUID</th>
              <th class="py-3 px-4">Variants & Pricing</th>
              <th class="py-3 px-4 text-right">Actions</th>
            </tr>
          </thead>
          <tbody id="productTableBody" class="divide-y divide-slate-800/60 text-slate-300">
            <tr>
              <td colspan="4" class="py-8 text-center text-slate-500">
                <i class="fa-solid fa-spinner fa-spin text-base mr-2 text-emerald-500"></i> Loading products...
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </main>

  <!-- Modal: Quick Price Update -->
  <div id="priceModal" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-800 rounded-xl w-full max-w-md p-5 shadow-2xl space-y-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <h3 class="font-semibold text-white text-sm flex items-center gap-2">
          <i class="fa-solid fa-tag text-emerald-400"></i> Update Product Price
        </h3>
        <button onclick="closePriceModal()" class="text-slate-400 hover:text-white"><i class="fa-solid fa-xmark"></i></button>
      </div>

      <div class="space-y-3 text-xs">
        <div>
          <label class="block text-slate-400 mb-1">Product</label>
          <p id="modalProdName" class="font-semibold text-white text-sm"></p>
          <p id="modalProdUuid" class="font-mono text-[11px] text-slate-500 truncate"></p>
        </div>

        <div>
          <label class="block text-slate-400 mb-1">Select Variant</label>
          <select id="modalVariantSelect" class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 focus:outline-none focus:border-emerald-500"></select>
        </div>

        <div>
          <label class="block text-slate-400 mb-1">New Price (<span id="modalCurrency">SEK</span>)</label>
          <div class="relative">
            <input type="number" step="0.5" id="modalNewPrice" placeholder="e.g. 75.00"
                   class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-white font-mono focus:outline-none focus:border-emerald-500">
          </div>
          <p class="text-[11px] text-slate-500 mt-1">Converts automatically to minor currency units (cents) for Zettle.</p>
        </div>
      </div>

      <div class="flex items-center justify-end gap-2 pt-2 border-t border-slate-800">
        <button onclick="closePriceModal()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium">Cancel</button>
        <button onclick="submitPriceUpdate()" id="btnSubmitPrice" class="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-medium flex items-center gap-1.5 shadow-sm">
          <span>Apply Price Change</span>
        </button>
      </div>
    </div>
  </div>

  <!-- Modal: View Raw JSON -->
  <div id="jsonModal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-800 rounded-xl w-full max-w-2xl max-h-[85vh] flex flex-col shadow-2xl">
      <div class="flex items-center justify-between p-4 border-b border-slate-800">
        <h3 class="font-semibold text-white text-sm flex items-center gap-2">
          <i class="fa-solid fa-code text-indigo-400"></i> JSON Object Inspector
        </h3>
        <div class="flex items-center gap-2">
          <button onclick="copyJsonModalContent()" class="text-xs px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded border border-slate-700">
            <i class="fa-regular fa-copy mr-1"></i> Copy
          </button>
          <button onclick="closeJsonModal()" class="text-slate-400 hover:text-white"><i class="fa-solid fa-xmark"></i></button>
        </div>
      </div>
      <div class="p-4 overflow-auto flex-1 bg-slate-950/80">
        <pre id="jsonModalContent" class="text-xs text-emerald-300 leading-relaxed"></pre>
      </div>
    </div>
  </div>

  <!-- Modal: Function Runner (Playground) -->
  <div id="runnerModal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-800 rounded-xl w-full max-w-3xl max-h-[90vh] flex flex-col shadow-2xl">
      <div class="flex items-center justify-between p-4 border-b border-slate-800">
        <h3 class="font-semibold text-white text-sm flex items-center gap-2">
          <i class="fa-solid fa-terminal text-emerald-400"></i> Python Function Interactive Runner
        </h3>
        <button onclick="closeRawTester()" class="text-slate-400 hover:text-white"><i class="fa-solid fa-xmark"></i></button>
      </div>

      <div class="p-4 space-y-4 overflow-auto flex-1 text-xs">
        <div>
          <label class="block text-slate-400 mb-1">Select Function to Call</label>
          <select id="runnerFuncSelect" onchange="updateRunnerParams()" class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 focus:outline-none focus:border-emerald-500 font-mono">
            <option value="get_all_products">get_all_products()</option>
            <option value="get_product">get_product(product_uuid)</option>
            <option value="find_products">find_products(query, name)</option>
            <option value="find_product_by_name">find_product_by_name(name, exact)</option>
            <option value="get_categories">get_categories()</option>
            <option value="get_token_info">get_token_info()</option>
          </select>
        </div>

        <div id="runnerParamInputs" class="space-y-2 hidden">
          <label class="block text-slate-400">Parameters</label>
          <input type="text" id="runnerParam1" placeholder="e.g. product_uuid or query" class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs focus:outline-none focus:border-emerald-500">
        </div>

        <button onclick="executeFunctionRunner()" class="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg font-medium transition flex items-center gap-2">
          <i class="fa-solid fa-play"></i> Run Function
        </button>

        <div>
          <label class="block text-slate-400 mb-1">Output (JSON-serialised object)</label>
          <pre id="runnerOutput" class="bg-slate-950 border border-slate-800 rounded-lg p-3 text-emerald-300 font-mono text-xs max-h-64 overflow-auto">// Ready to execute</pre>
        </div>
      </div>
    </div>
  </div>

  <!-- Modal: New Product Creator -->
  <div id="createModal" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-800 rounded-xl w-full max-w-md p-5 shadow-2xl space-y-4 text-xs">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <h3 class="font-semibold text-white text-sm flex items-center gap-2">
          <i class="fa-solid fa-plus-circle text-emerald-400"></i> Create New Product
        </h3>
        <button onclick="closeCreateModal()" class="text-slate-400 hover:text-white"><i class="fa-solid fa-xmark"></i></button>
      </div>

      <div class="space-y-3">
        <div>
          <label class="block text-slate-400 mb-1">Product Name</label>
          <input type="text" id="createName" placeholder="e.g. Guinness Draft" class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200">
        </div>
        <div>
          <label class="block text-slate-400 mb-1">Description</label>
          <input type="text" id="createDesc" placeholder="e.g. Classic Irish Stout" class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200">
        </div>
        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="block text-slate-400 mb-1">Price</label>
            <input type="number" step="0.5" id="createPrice" placeholder="68.00" class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono">
          </div>
          <div>
            <label class="block text-slate-400 mb-1">Currency</label>
            <input type="text" id="createCurr" value="SEK" class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono">
          </div>
        </div>
      </div>

      <div class="flex items-center justify-end gap-2 pt-2 border-t border-slate-800">
        <button onclick="closeCreateModal()" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg">Cancel</button>
        <button onclick="submitCreateProduct()" class="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg font-medium">Create</button>
      </div>
    </div>
  </div>

  <!-- Scripts -->
  <script>
    let productsList = [];
    let activePriceProduct = null;

    async function refreshAuthStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        
        const dot = document.getElementById('statusDot');
        const txt = document.getElementById('statusText');
        const expiry = document.getElementById('tokenExpiry');
        const demoBanner = document.getElementById('demoBanner');

        if (data.demo_mode) {
          demoBanner.classList.remove('hidden');
          dot.className = 'inline-block w-2 h-2 rounded-full bg-amber-400';
          txt.textContent = 'Demo Mode (Simulation)';
          document.getElementById('cardPersistStatus').textContent = 'Simulated';
          document.getElementById('cardExpiryMins').textContent = 'Active';
          return;
        } else {
          demoBanner.classList.add('hidden');
        }

        if (data.is_authenticated) {
          dot.className = 'inline-block w-2 h-2 rounded-full bg-emerald-400';
          txt.textContent = 'Connected (Zettle Live)';
          document.getElementById('cardPersistStatus').textContent = data.persisted_on_disk ? 'Cached on Disk' : 'In Memory';
          document.getElementById('cardCachePath').textContent = data.cache_file || '.zettle_token_cache.json';
          document.getElementById('cardExpiryMins').textContent = data.expires_in_minutes ? `${data.expires_in_minutes}m remaining` : 'Active';
          if (data.expires_in_minutes) {
            expiry.classList.remove('hidden');
            expiry.textContent = `(${data.expires_in_minutes}m)`;
          }
        } else {
          dot.className = 'inline-block w-2 h-2 rounded-full bg-rose-500';
          txt.textContent = data.has_api_key ? 'Auth Expired' : 'No API Key';
          document.getElementById('cardPersistStatus').textContent = 'Not Authenticated';
          document.getElementById('cardExpiryMins').textContent = 'Expired / Missing';
        }
      } catch (err) {
        console.error('Status fetch error:', err);
      }
    }

    async function loadProducts(query = '') {
      const tbody = document.getElementById('productTableBody');
      tbody.innerHTML = `<tr><td colspan="4" class="py-8 text-center text-slate-500"><i class="fa-solid fa-spinner fa-spin text-base mr-2 text-emerald-500"></i> Fetching products from Zettle...</td></tr>`;

      try {
        const url = query ? `/api/products?query=${encodeURIComponent(query)}` : '/api/products';
        const res = await fetch(url);
        const data = await res.json();

        if (data.error) {
          tbody.innerHTML = `<tr><td colspan="4" class="py-6 text-center text-rose-400 font-mono text-xs">Error: ${data.error}</td></tr>`;
          return;
        }

        productsList = data;
        document.getElementById('cardProductCount').textContent = productsList.length;
        renderProducts(productsList);
      } catch (err) {
        tbody.innerHTML = `<tr><td colspan="4" class="py-6 text-center text-rose-400">Failed to connect to backend server.</td></tr>`;
      }
    }

    function renderProducts(items) {
      const tbody = document.getElementById('productTableBody');
      if (!items || items.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" class="py-8 text-center text-slate-500">No products found.</td></tr>`;
        return;
      }

      tbody.innerHTML = items.map(p => {
        const variants = p.variants || [];
        const variantChips = variants.map(v => {
          const priceObj = v.price || {};
          const amt = (priceObj.amount !== undefined) ? (priceObj.amount / 100).toFixed(2) : '--';
          const curr = priceObj.currencyId || '';
          return `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-slate-900 border border-slate-700/80 font-mono text-[11px] text-slate-300">
            <span class="text-slate-400">${v.name || 'Standard'}:</span>
            <span class="text-emerald-400 font-semibold">${amt} ${curr}</span>
          </span>`;
        }).join(' ');

        return `
          <tr class="hover:bg-slate-900/40 transition">
            <td class="py-3 px-4">
              <div class="font-medium text-white text-xs">${escapeHtml(p.name || 'Unnamed')}</div>
              <div class="text-[11px] text-slate-400 truncate max-w-xs mt-0.5">${escapeHtml(p.description || 'No description')}</div>
            </td>
            <td class="py-3 px-4 font-mono text-[11px] text-slate-500">
              <span class="select-all">${p.uuid}</span>
            </td>
            <td class="py-3 px-4">
              <div class="flex flex-wrap gap-1.5">${variantChips || '<span class="text-slate-600">No variants</span>'}</div>
            </td>
            <td class="py-3 px-4 text-right space-x-1.5 whitespace-nowrap">
              <button onclick="openPriceModal('${p.uuid}')" class="px-2.5 py-1 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-400 border border-emerald-500/30 rounded text-[11px] font-medium transition" title="Change Price">
                <i class="fa-solid fa-tag mr-1"></i> Update Price
              </button>
              <button onclick="viewProductJson('${p.uuid}')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded border border-slate-700 text-[11px] transition" title="Inspect JSON">
                <i class="fa-solid fa-code"></i> JSON
              </button>
              <button onclick="deleteProductPrompt('${p.uuid}')" class="px-2 py-1 bg-rose-950/30 hover:bg-rose-900/50 text-rose-400 rounded border border-rose-800/40 text-[11px] transition" title="Delete">
                <i class="fa-regular fa-trash-can"></i>
              </button>
            </td>
          </tr>
        `;
      }).join('');
    }

    let searchTimeout = null;
    function handleSearch(val) {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(() => {
        loadProducts(val);
      }, 250);
    }

    function openPriceModal(uuid) {
      const prod = productsList.find(p => p.uuid === uuid);
      if (!prod) return;
      activePriceProduct = prod;

      document.getElementById('modalProdName').textContent = prod.name;
      document.getElementById('modalProdUuid').textContent = prod.uuid;

      const varSelect = document.getElementById('modalVariantSelect');
      varSelect.innerHTML = (prod.variants || []).map((v, idx) => {
        const amt = v.price?.amount ? (v.price.amount / 100).toFixed(2) : '0';
        return `<option value="${v.uuid}">${v.name || 'Default'} (${amt} ${v.price?.currencyId || 'SEK'})</option>`;
      }).join('');

      const curr = prod.variants?.[0]?.price?.currencyId || 'SEK';
      document.getElementById('modalCurrency').textContent = curr;
      const initialPrice = prod.variants?.[0]?.price?.amount ? (prod.variants[0].price.amount / 100).toFixed(2) : '';
      document.getElementById('modalNewPrice').value = initialPrice;

      document.getElementById('priceModal').classList.remove('hidden');
    }

    function closePriceModal() {
      document.getElementById('priceModal').classList.add('hidden');
      activePriceProduct = null;
    }

    async function submitPriceUpdate() {
      if (!activePriceProduct) return;
      const priceVal = parseFloat(document.getElementById('modalNewPrice').value);
      const varUuid = document.getElementById('modalVariantSelect').value;
      if (isNaN(priceVal)) {
        alert('Please enter a valid numeric price.');
        return;
      }

      const btn = document.getElementById('btnSubmitPrice');
      btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin mr-1"></i> Saving...`;
      btn.disabled = true;

      try {
        const res = await fetch(`/api/products/${activePriceProduct.uuid}/price`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ price: priceVal, variant_uuid: varUuid }),
        });
        const data = await res.json();
        if (data.error) {
          alert('Error updating price: ' + data.error);
        } else {
          closePriceModal();
          await loadProducts(document.getElementById('searchInput').value);
        }
      } catch (err) {
        alert('Failed to execute update: ' + err);
      } finally {
        btn.innerHTML = `<span>Apply Price Change</span>`;
        btn.disabled = false;
      }
    }

    async function viewProductJson(uuid) {
      try {
        const res = await fetch(`/api/products/${uuid}`);
        const data = await res.json();
        document.getElementById('jsonModalContent').textContent = JSON.stringify(data, null, 2);
        document.getElementById('jsonModal').classList.remove('hidden');
      } catch (err) {
        alert('Failed to load JSON: ' + err);
      }
    }

    function closeJsonModal() {
      document.getElementById('jsonModal').classList.add('hidden');
    }

    function copyJsonModalContent() {
      const text = document.getElementById('jsonModalContent').textContent;
      navigator.clipboard.writeText(text);
      alert('JSON copied to clipboard!');
    }

    async function deleteProductPrompt(uuid) {
      if (!confirm(`Are you sure you want to delete product '${uuid}'?`)) return;
      try {
        const res = await fetch(`/api/products/${uuid}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.error) {
          alert('Delete error: ' + data.error);
        } else {
          loadProducts();
        }
      } catch (err) {
        alert('Delete failed: ' + err);
      }
    }

    function openCreateModal() {
      document.getElementById('createModal').classList.remove('hidden');
    }

    function closeCreateModal() {
      document.getElementById('createModal').classList.add('hidden');
    }

    async function submitCreateProduct() {
      const name = document.getElementById('createName').value.trim();
      const desc = document.getElementById('createDesc').value.trim();
      const price = parseFloat(document.getElementById('createPrice').value || 50.0);
      const curr = document.getElementById('createCurr').value.trim() || 'SEK';

      if (!name) {
        alert('Product name is required.');
        return;
      }

      try {
        const res = await fetch('/api/products', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ name, description: desc, price, currency: curr }),
        });
        const data = await res.json();
        if (data.error) {
          alert('Error: ' + data.error);
        } else {
          closeCreateModal();
          loadProducts();
        }
      } catch (err) {
        alert('Creation failed: ' + err);
      }
    }

    function openRawTester() {
      document.getElementById('runnerModal').classList.remove('hidden');
      updateRunnerParams();
    }

    function closeRawTester() {
      document.getElementById('runnerModal').classList.add('hidden');
    }

    function updateRunnerParams() {
      const func = document.getElementById('runnerFuncSelect').value;
      const paramContainer = document.getElementById('runnerParamInputs');
      const paramInput = document.getElementById('runnerParam1');

      if (func === 'get_product') {
        paramContainer.classList.remove('hidden');
        paramInput.placeholder = 'Enter product_uuid (e.g. demo-beer-ipa-001)';
      } else if (func === 'find_products' || func === 'find_product_by_name') {
        paramContainer.classList.remove('hidden');
        paramInput.placeholder = 'Enter search query / name (e.g. IPA or Pilsner)';
      } else {
        paramContainer.classList.add('hidden');
      }
    }

    async function executeFunctionRunner() {
      const func = document.getElementById('runnerFuncSelect').value;
      const param = document.getElementById('runnerParam1').value.trim();
      const output = document.getElementById('runnerOutput');
      output.textContent = '// Calling ' + func + '...';

      let endpoint = '';
      if (func === 'get_all_products') endpoint = '/api/products';
      else if (func === 'get_product') endpoint = `/api/products/${encodeURIComponent(param)}`;
      else if (func === 'find_products') endpoint = `/api/products?query=${encodeURIComponent(param)}`;
      else if (func === 'find_product_by_name') endpoint = `/api/products?name=${encodeURIComponent(param)}`;
      else if (func === 'get_categories') endpoint = '/api/categories';
      else if (func === 'get_token_info') endpoint = '/api/status';

      try {
        const res = await fetch(endpoint);
        const data = await res.json();
        output.textContent = JSON.stringify(data, null, 2);
      } catch (err) {
        output.textContent = '// Execution failed: ' + err;
      }
    }

    async function forceRefreshToken() {
      if (!confirm('Force request a new OAuth token from Zettle?')) return;
      try {
        const res = await fetch('/api/token/refresh', { method: 'POST' });
        const data = await res.json();
        alert(data.message || (data.error ? 'Error: ' + data.error : 'Refreshed!'));
        refreshAuthStatus();
      } catch (err) {
        alert('Refresh failed: ' + err);
      }
    }

    async function clearToken() {
      if (!confirm('Clear token cache from disk and memory?')) return;
      try {
        await fetch('/api/token/clear', { method: 'POST' });
        refreshAuthStatus();
        alert('Persistent token cache cleared.');
      } catch (err) {
        alert('Clear failed: ' + err);
      }
    }

    function escapeHtml(str) {
      return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // Initialize
    refreshAuthStatus();
    loadProducts();
  </script>
</body>
</html>
"""

@app.route("/", methods=["GET"])
def index():
    """Serve the single-page testing dashboard."""
    return render_template_string(HTML_TEMPLATE)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"\n=======================================================")
    print(f"🚀 Zettle Testing Web Interface running at:")
    print(f"   http://127.0.0.1:{port}")
    print(f"=======================================================\n")
    app.run(host="127.0.0.1", port=port, debug=True)

