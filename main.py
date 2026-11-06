import os
import json
import uvicorn
import asyncio
import datetime
import pandas as pd

from typing import Optional, Dict, List, Any
from pydantic import BaseModel
from predictor import DynamicPricingPredictor
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, BackgroundTasks, HTTPException
from zettle.products import update_product_price, get_all_products
from zettle.purchases import get_purchases, extract_variant_sales
from zettle.exceptions import ZettleError

app = FastAPI(title="Wall Street Bar - Dynamic Pricing Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

predictor = DynamicPricingPredictor(
    delta_rel=0.03,      # 3% geometric decay toward floor per tick
    sigma=0.015,         # 1.5% GBM volatility
    lambda_rel=0.04,     # +4% self-price spike per unit sold
    gamma_rel=0.0001,    # Bank feedback gain (profit = current - normal price)
)
PRODUCTS_FILE = "products.json"
SALES_DATA_PATH = "september_sales.csv"

def load_products() -> List[Dict[str, Any]]:
    if not os.path.exists(PRODUCTS_FILE):
        return []
    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        products = json.load(f)
    for p in products:
        if "current_price" not in p:
            p["current_price"] = p.get("original_price", 20.0)
        if "previous_price" not in p:
            p["previous_price"] = p.get("current_price", p.get("original_price", 20.0))
        if "normal_price" not in p:
            p["normal_price"] = p.get("original_price", 20.0)
        if "min_price" not in p:
            p["min_price"] = 0.0
        if "max_price" not in p:
            p["max_price"] = round(p.get("original_price", 20.0) * 2.0, 2)
        if "product_uuid" not in p:
            p["product_uuid"] = p.get("product_uuids", [""])[0] if p.get("product_uuids") else ""
        p["tick_diff"] = round(p["current_price"] - p["previous_price"], 2)
    return products

def save_products(products: List[Dict[str, Any]]) -> None:
    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)

class TickerConfigRequest(BaseModel):
    interval_minutes: Optional[float] = None

class MarketTicker:
    """
    Automated market ticker that executes pricing ticks every N minutes.
    Fetches purchases from the Zettle library cursor, extracts sales vectors,
    loads products from products.json, calls the predictor model,
    updates prices, and persists changes.
    """
    def __init__(self, default_interval_minutes: float = 2.0):
        self.is_running: bool = False
        self.interval_minutes: float = float(default_interval_minutes)
        self.last_run: Optional[str] = None
        self.last_purchase_hash: Optional[str] = None
        self.last_purchases_count: int = 0
        self.last_sales_volume: Dict[str, float] = {}
        self.last_status: str = "paused"
        self.error_count: int = 0
        self.total_ticks: int = 0
        self._task: Optional[asyncio.Task] = None

    def start(self, interval_minutes: Optional[float] = None):
        if interval_minutes is not None and interval_minutes > 0:
            self.interval_minutes = float(interval_minutes)
        self.is_running = True
        self.last_status = "running"
        try:
            loop = asyncio.get_running_loop()
            if self._task is None or self._task.done():
                self._task = loop.create_task(self._run_loop())
        except RuntimeError:
            pass

    def stop(self):
        self.is_running = False
        self.last_status = "paused"
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

    def get_status(self) -> dict:
        return {
            "is_running": self.is_running,
            "interval_minutes": self.interval_minutes,
            "interval_seconds": self.interval_minutes * 60,
            "last_run": self.last_run,
            "last_purchases_count": self.last_purchases_count,
            "last_sales_volume": self.last_sales_volume,
            "last_purchase_hash": self.last_purchase_hash,
            "last_status": self.last_status,
            "total_ticks": self.total_ticks,
            "bank_balance": float(round(predictor.bank_balance, 2)),
            "predictor_tick": predictor.tick_count,
        }

    async def _run_loop(self):
        while self.is_running:
            try:
                await self.tick()
            except Exception as e:
                self.error_count += 1
                self.last_status = f"error: {str(e)}"
                print(f"[MarketTicker Error] {e}")

            try:
                await asyncio.sleep(self.interval_minutes * 60)
            except asyncio.CancelledError:
                break

    async def tick(self):
        """
        Executes one live market cycle:
        1. Fetches purchases from Zettle library using cursor
        2. Loads products from products.json and builds required vectors
        3. Calls predictor.predict_prices(..., commit=True)
        4. Updates prices, pushes to Zettle terminals, and saves products.json
        """
        products = load_products()
        if not products:
            print(f"[MarketTicker] No products found in {PRODUCTS_FILE}")
            return

        # 1. Fetch purchases from Zettle library using cursor
        purchases = []
        try:
            res = get_purchases(last_purchase_hash=self.last_purchase_hash, limit=100)
            purchases = res.get("purchases", [])
            new_hash = res.get("lastPurchaseHash")
            if new_hash:
                self.last_purchase_hash = new_hash
        except Exception as e:
            # If in demo/offline mode or credentials not configured yet
            print(f"[MarketTicker] Note on Zettle purchase fetch: {e}")

        # 2. Extract sales quantities — two dicts keyed by variant and product UUID
        sales_by_variant, sales_by_product = extract_variant_sales(purchases)
        self.last_purchases_count = len(purchases)
        self.last_sales_volume = {
            "by_variant": sales_by_variant,
            "by_product": sales_by_product,
        }

        # 3. Create corresponding vectors from load_products
        #    For each product, pick the right sales dict based on the "matching" field:
        #      "variant"  → look up by variant_uuid in sales_by_variant
        #      "product"  → sum across all product_uuids in sales_by_product
        uuids          = [p["variant_uuid"] for p in products]
        current_prices = [p["current_price"] for p in products]
        min_prices     = [p["min_price"] for p in products]
        normal_prices  = [p["normal_price"] for p in products]

        quantities = []
        for p in products:
            matching = p.get("matching", "variant")
            if matching == "product":
                # Sum quantity across every product UUID listed for this item
                prod_uuids = p.get("product_uuids") or (
                    [p["product_uuid"]] if p.get("product_uuid") else []
                )
                qty = sum(sales_by_product.get(uid, 0.0) for uid in prod_uuids)
            else:
                qty = sales_by_variant.get(p["variant_uuid"], 0.0)
            quantities.append(qty)

        # 4. Call the new predictor function
        predicted_prices = predictor.predict_prices(
            current_prices=current_prices,
            min_prices=min_prices,
            normal_prices=normal_prices,
            quantities=quantities,
            uuids=uuids,
            commit=True,
            include_noise=True,
        )

        # 5. Update prices in products.json and push to Zettle
        #
        #   matching="variant" → one push: specific product_uuid + variant_uuid
        #   matching="product" → one push per entry in product_uuids, no variant_uuid
        #                        (each product UUID is a separate Zettle product whose
        #                        first/only variant receives the new price)
        updates_made = False
        for p in products:
            var_uuid = p["variant_uuid"]
            matching = p.get("matching", "variant")

            if var_uuid not in predicted_prices:
                continue

            new_price = min(predicted_prices[var_uuid], p["max_price"])
            old_price = p["current_price"]

            if new_price != old_price:
                p["previous_price"] = old_price
                p["current_price"]  = new_price
                updates_made = True

            try:
                if matching == "product":
                    # Push to every product UUID that maps to this market item
                    prod_uuids = p.get("product_uuids") or (
                        [p["product_uuid"]] if p.get("product_uuid") else []
                    )
                    for prod_uuid in prod_uuids:
                        update_product_price(
                            product_uuid=prod_uuid,
                            price=new_price,
                            variant_uuid=None,   # target the first/only variant per product
                            is_cents=False,
                        )
                    print(
                        f"[MarketTicker] Pushed price update (product×{len(prod_uuids)}): "
                        f"{p['product_name']} -> {new_price} kr"
                    )
                else:
                    # matching == "variant": push to one product + explicit variant UUID
                    prod_uuid = p.get("product_uuid") or (p.get("product_uuids", [""])[0])
                    update_product_price(
                        product_uuid=prod_uuid,
                        price=new_price,
                        variant_uuid=var_uuid,
                        is_cents=False,
                    )
                    print(f"[MarketTicker] Pushed price update: {p['product_name']} -> {new_price} kr")
            except Exception as ze:
                print(f"[MarketTicker] Zettle push failed for {p['product_name']}: {ze}")

        if updates_made:
            save_products(products)

        self.last_run = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.total_ticks += 1
        self.last_status = "idle"

ticker = MarketTicker(default_interval_minutes=2.0)

class PriceOverride(BaseModel):
    variant_uuid: str
    new_price: float

class EmulateRequest(BaseModel):
    purchases: Dict[str, float] = {}
    include_noise: bool = True

class HyperparameterUpdate(BaseModel):
    delta_rel: Optional[float] = None
    sigma: Optional[float] = None
    lambda_rel: Optional[float] = None
    omega_rel: Optional[float] = None
    gamma_rel: Optional[float] = None
    max_price_multiplier: Optional[float] = None

@app.get("/api/engine/hyperparameters")
def get_engine_hyperparameters():
    return predictor.get_hyperparameters()

@app.post("/api/engine/hyperparameters")
def update_engine_hyperparameters(params: HyperparameterUpdate):
    try:
        updated = predictor.update_hyperparameters(
            **{k: v for k, v in params.model_dump().items() if v is not None}
        )
        return {"status": "success", "hyperparameters": updated}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/engine/state")
def get_engine_state():
    return predictor.get_state()

@app.get("/api/engine/ticker/status")
async def get_ticker_status():
    return ticker.get_status()

@app.post("/api/engine/ticker/start")
async def start_ticker(req: Optional[TickerConfigRequest] = None):
    mins = req.interval_minutes if req else None
    ticker.start(interval_minutes=mins)
    return {"status": "success", "ticker": ticker.get_status()}

@app.post("/api/engine/ticker/pause")
async def pause_ticker():
    ticker.stop()
    return {"status": "success", "ticker": ticker.get_status()}

@app.post("/api/engine/ticker/interval")
async def set_ticker_interval(req: TickerConfigRequest):
    if req and req.interval_minutes and req.interval_minutes > 0:
        ticker.interval_minutes = float(req.interval_minutes)
    return {"status": "success", "ticker": ticker.get_status()}

@app.post("/api/engine/ticker/tick")
async def trigger_manual_tick():
    await ticker.tick()
    return {"status": "success", "ticker": ticker.get_status()}

@app.on_event("shutdown")
async def shutdown_ticker():
    ticker.stop()

@app.post("/api/engine/trigger")
async def trigger_price_updates(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_pricing_cycle)
    return {"status": "Pricing cycle started"}

@app.post("/api/engine/reset")
async def reset_prices(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_reset_cycle)
    return {"status": "Reset cycle started"}

@app.post("/api/engine/override")
def override_price(override: PriceOverride):
    products = load_products()
    matched = None
    for p in products:
        if p["variant_uuid"] == override.variant_uuid:
            matched = p
            break
    
    if not matched:
        raise HTTPException(status_code=404, detail="Variant not found in managed catalog")
    
    try:
        prod_uuid = matched.get("product_uuid") or (matched.get("product_uuids", [""])[0])
        update_product_price(
            product_uuid=prod_uuid,
            price=override.new_price,
            variant_uuid=override.variant_uuid,
            is_cents=False
        )
        matched["previous_price"] = matched["current_price"]
        matched["current_price"] = override.new_price
        save_products(products)
        return {"status": "success", "new_price": override.new_price}
    except ZettleError as e:
        raise HTTPException(status_code=500, detail=str(e))

def run_pricing_cycle():
    products = load_products()
    if not products:
        print(f"[{PRODUCTS_FILE}] not found or empty. Aborting cycle.")
        return
        
    try:
        sales_df = pd.read_csv(SALES_DATA_PATH)
    except FileNotFoundError:
        sales_df = pd.DataFrame(columns=["variant_uuid", "quantity", "price", "timestamp"])

    # Build quantity vector aligned to products (zero if no sales data)
    if not sales_df.empty and "quantity" in sales_df.columns:
        volume = sales_df.groupby("variant_uuid")["quantity"].sum()
    else:
        volume = pd.Series(dtype=float)

    uuids          = [p["variant_uuid"] for p in products]
    current_prices = [p["current_price"] for p in products]
    min_prices     = [p["min_price"] for p in products]
    normal_prices  = [p["normal_price"] for p in products]
    quantities     = [float(volume.get(uid, 0.0)) for uid in uuids]

    predicted_prices = predictor.predict_prices(
        current_prices=current_prices,
        min_prices=min_prices,
        normal_prices=normal_prices,
        quantities=quantities,
        uuids=uuids,
    )
    updates_made = False

    for p in products:
        var_uuid = p["variant_uuid"]
        prod_uuid = p.get("product_uuid") or (p.get("product_uuids", [""])[0])

        if var_uuid in predicted_prices:
            new_price = min(predicted_prices[var_uuid], p["max_price"])

            if new_price != p["current_price"]:
                try:
                    update_product_price(
                        product_uuid=prod_uuid,
                        price=new_price,
                        variant_uuid=var_uuid,
                        is_cents=False
                    )
                    p["previous_price"] = p["current_price"]
                    p["current_price"] = new_price
                    updates_made = True
                    print(f"Updated {p['product_name']}: -> {new_price}")
                except ZettleError as e:
                    print(f"Zettle update failed for {p['product_name']}: {e}")

    if updates_made:
        save_products(products)

def run_reset_cycle():
    products = load_products()
    if not products:
        print(f"[{PRODUCTS_FILE}] not found or empty. Aborting reset.")
        return
        
    updates_made = False

    for p in products:
        if p["current_price"] != p["original_price"]:
            prod_uuid = p.get("product_uuid") or (p.get("product_uuids", [""])[0])
            try:
                update_product_price(
                    product_uuid=prod_uuid,
                    price=p["original_price"],
                    variant_uuid=p["variant_uuid"],
                    is_cents=False
                )
                p["previous_price"] = p["current_price"]
                p["current_price"] = p["original_price"]
                updates_made = True
                print(f"Reset {p['product_name']} -> {p['original_price']}")
            except ZettleError as e:
                print(f"Zettle reset failed for {p['product_name']}: {e}")

    if updates_made:
        save_products(products)

@app.get("/api/products")
def get_products():
    try:
        return get_all_products()
    except ZettleError as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/catalog/managed")
def get_managed_products():
    return load_products()

@app.post("/api/engine/emulate/simulate")
def emulate_simulate(req: EmulateRequest):
    products = load_products()
    if not products:
        raise HTTPException(status_code=404, detail="Products file not found or empty")
    uuids = [p["variant_uuid"] for p in products]
    current_prices = [p["current_price"] for p in products]
    min_prices = [p["min_price"] for p in products]
    normal_prices = [p["normal_price"] for p in products]
    quantities = [float(req.purchases.get(uid, 0.0)) for uid in uuids]

    sim_data = predictor.simulate_step(
        current_prices=current_prices,
        min_prices=min_prices,
        normal_prices=normal_prices,
        quantities=quantities,
        uuids=uuids,
        include_noise=req.include_noise,
    )

    name_map = {p["variant_uuid"]: p["product_name"] for p in products}
    min_map = {p["variant_uuid"]: p["min_price"] for p in products}
    max_map = {p["variant_uuid"]: p["max_price"] for p in products}
    norm_map = {p["variant_uuid"]: p["normal_price"] for p in products}

    for uid, info in sim_data["predictions"].items():
        info["product_name"] = name_map.get(uid, uid)
        info["min_price"] = min_map.get(uid, 0.0)
        info["max_price"] = max_map.get(uid, 999.0)
        info["normal_price"] = norm_map.get(uid, info["current_price"])
        info["predicted_price"] = min(info["predicted_price"], info["max_price"])
        info["diff"] = round(info["predicted_price"] - info["current_price"], 2)
        info["pct_change"] = (
            round((info["diff"] / info["current_price"]) * 100, 2)
            if info["current_price"] > 0
            else 0.0
        )

    return sim_data

@app.post("/api/engine/emulate/push")
def emulate_push(req: EmulateRequest):
    """
    Apply a sales vector tick strictly to the local model and products.json.
    NEVER pushes to Zettle POS. Used for experimentation and observing
    how parameters and sales vectors affect prices.
    """
    products = load_products()
    if not products:
        raise HTTPException(status_code=404, detail="Products file not found or empty")
    uuids = [p["variant_uuid"] for p in products]
    current_prices = [p["current_price"] for p in products]
    min_prices = [p["min_price"] for p in products]
    normal_prices = [p["normal_price"] for p in products]
    quantities = [float(req.purchases.get(uid, 0.0)) for uid in uuids]

    predicted_prices = predictor.predict_prices(
        current_prices=current_prices,
        min_prices=min_prices,
        normal_prices=normal_prices,
        quantities=quantities,
        uuids=uuids,
        commit=True,
        include_noise=req.include_noise,
    )

    applied_changes = []

    for p in products:
        var_uuid = p["variant_uuid"]

        if var_uuid in predicted_prices:
            new_price = min(predicted_prices[var_uuid], p["max_price"])
            old_price = p["current_price"]

            p["previous_price"] = old_price
            p["current_price"] = new_price

            applied_changes.append({
                "product_name": p["product_name"],
                "variant_uuid": var_uuid,
                "old_price": old_price,
                "new_price": new_price,
                "diff": round(new_price - old_price, 2),
                "pct_change": round(((new_price - old_price) / old_price) * 100, 2) if old_price > 0 else 0.0,
                "quantity": float(req.purchases.get(var_uuid, 0.0)),
            })

    save_products(products)

    return {
        "status": "success",
        "applied_changes": applied_changes,
        "engine_state": predictor.get_state(),
        "catalog": products,
    }

@app.post("/api/engine/emulate/reset")
def emulate_reset():
    """
    Reset local prices in products.json and House Bank state back to original baselines.
    NEVER touches Zettle POS.
    """
    products = load_products()
    if not products:
        raise HTTPException(status_code=404, detail="Products file not found or empty")
    for p in products:
        p["previous_price"] = p["current_price"]
        p["current_price"] = p["original_price"]
    save_products(products)
    predictor.reset_bank()
    return {
        "status": "success",
        "engine_state": predictor.get_state(),
        "catalog": products,
    }

@app.get("/")
def home_board():
    return FileResponse("templates/index.html")

@app.get("/emulator")
def emulator_page():
    return FileResponse("templates/emulator.html")

@app.get("/admin")
def admin_portal():
    return FileResponse("templates/admin.html")

if __name__ == "__main__":
    print("Starting Wall Street Bar - Dynamic Pricing Engine...")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
