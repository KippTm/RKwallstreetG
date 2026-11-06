import os
import uvicorn
import pandas as pd

from typing import Optional, Dict, List, Any
from pydantic import BaseModel
from predictor import DynamicPricingPredictor
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, BackgroundTasks, HTTPException
from zettle.products import update_product_price, get_all_products
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
TARGET_CSV_PATH = "target_products.csv"
SALES_DATA_PATH = "september_sales.csv"

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
    if not os.path.exists(TARGET_CSV_PATH):
        raise HTTPException(status_code=404, detail="Target CSV not found")
    
    target_df = pd.read_csv(TARGET_CSV_PATH)
    mask = target_df["variant_uuid"] == override.variant_uuid
    
    if not mask.any():
        raise HTTPException(status_code=404, detail="Variant not found in managed catalog")
    
    row = target_df[mask].iloc[0]
    
    try:
        update_product_price(
            product_uuid=row["product_uuid"],
            price=override.new_price,
            variant_uuid=override.variant_uuid,
            is_cents=False
        )
        target_df.loc[mask, "current_price"] = override.new_price
        target_df.to_csv(TARGET_CSV_PATH, index=False)
        return {"status": "success", "new_price": override.new_price}
    except ZettleError as e:
        raise HTTPException(status_code=500, detail=str(e))

def run_pricing_cycle():
    if not os.path.exists(TARGET_CSV_PATH):
        print(f"[{TARGET_CSV_PATH}] not found. Aborting cycle.")
        return
        
    target_df = pd.read_csv(TARGET_CSV_PATH)
    
    try:
        sales_df = pd.read_csv(SALES_DATA_PATH)
    except FileNotFoundError:
        sales_df = pd.DataFrame(columns=["variant_uuid", "quantity", "price", "timestamp"])

    # Build quantity vector aligned to target_df rows (zero if no sales data)
    if not sales_df.empty and "quantity" in sales_df.columns:
        volume = sales_df.groupby("variant_uuid")["quantity"].sum()
    else:
        volume = pd.Series(dtype=float)

    uuids          = target_df["variant_uuid"].tolist()
    current_prices = target_df["current_price"].tolist()
    min_prices     = target_df["min_price"].tolist()
    # normal_price = standard Friday bar price; profit tracked as current - normal
    # Falls back to original_price if normal_price column is absent
    if "normal_price" in target_df.columns:
        normal_prices = target_df["normal_price"].tolist()
    else:
        normal_prices = target_df["original_price"].tolist()
    quantities     = [float(volume.get(uid, 0)) for uid in uuids]

    # Predictor already clips to [min_price, max_price * multiplier]; the
    # CSV max_price is an independent hard ceiling applied below.
    predicted_prices = predictor.predict_prices(
        current_prices=current_prices,
        min_prices=min_prices,
        normal_prices=normal_prices,
        quantities=quantities,
        uuids=uuids,
    )
    updates_made = False

    for index, row in target_df.iterrows():
        var_uuid = row["variant_uuid"]
        prod_uuid = row["product_uuid"]

        if var_uuid in predicted_prices:
            # Enforce the CSV hard ceiling (max_price column)
            new_price = min(predicted_prices[var_uuid], row["max_price"])

            if new_price != row["current_price"]:
                try:
                    update_product_price(
                        product_uuid=prod_uuid,
                        price=new_price,
                        variant_uuid=var_uuid,
                        is_cents=False
                    )
                    target_df.at[index, "current_price"] = new_price
                    updates_made = True
                    print(f"Updated {row['product_name']}: -> {new_price}")
                except ZettleError as e:
                    print(f"Zettle update failed for {row['product_name']}: {e}")

    if updates_made:
        target_df.to_csv(TARGET_CSV_PATH, index=False)

def run_reset_cycle():
    if not os.path.exists(TARGET_CSV_PATH):
        print(f"[{TARGET_CSV_PATH}] not found. Aborting reset.")
        return
        
    target_df = pd.read_csv(TARGET_CSV_PATH)
    updates_made = False
    
    for index, row in target_df.iterrows():
        if row["current_price"] != row["original_price"]:
            try:
                update_product_price(
                    product_uuid=row["product_uuid"],
                    price=row["original_price"],
                    variant_uuid=row["variant_uuid"],
                    is_cents=False
                )
                target_df.at[index, "current_price"] = row["original_price"]
                updates_made = True
                print(f"Reset {row['product_name']} -> {row['original_price']}")
            except ZettleError as e:
                print(f"Zettle reset failed for {row['product_name']}: {e}")

    if updates_made:
        target_df.to_csv(TARGET_CSV_PATH, index=False)

@app.get("/api/products")
def get_products():
    try:
        return get_all_products()
    except ZettleError as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/catalog/managed")
def get_managed_products():
    if not os.path.exists(TARGET_CSV_PATH):
        return []
    return pd.read_csv(TARGET_CSV_PATH).to_dict(orient="records")

@app.post("/api/engine/emulate/simulate")
def emulate_simulate(req: EmulateRequest):
    if not os.path.exists(TARGET_CSV_PATH):
        raise HTTPException(status_code=404, detail="Target CSV not found")
    target_df = pd.read_csv(TARGET_CSV_PATH)
    uuids = target_df["variant_uuid"].tolist()
    current_prices = target_df["current_price"].tolist()
    min_prices = target_df["min_price"].tolist()
    normal_prices = (
        target_df["normal_price"].tolist()
        if "normal_price" in target_df.columns
        else target_df["original_price"].tolist()
    )
    quantities = [float(req.purchases.get(uid, 0.0)) for uid in uuids]

    sim_data = predictor.simulate_step(
        current_prices=current_prices,
        min_prices=min_prices,
        normal_prices=normal_prices,
        quantities=quantities,
        uuids=uuids,
        include_noise=req.include_noise,
    )

    name_map = dict(zip(target_df["variant_uuid"], target_df["product_name"]))
    min_map = dict(zip(target_df["variant_uuid"], target_df["min_price"]))
    max_map = dict(zip(target_df["variant_uuid"], target_df["max_price"]))
    norm_map = dict(zip(target_df["variant_uuid"], normal_prices))

    for uid, info in sim_data["predictions"].items():
        info["product_name"] = name_map.get(uid, uid)
        info["min_price"] = min_map.get(uid, 0.0)
        info["max_price"] = max_map.get(uid, 999.0)
        info["normal_price"] = norm_map.get(uid, info["current_price"])
        # CSV hard ceiling
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
    Apply a sales vector tick strictly to the local model and CSV.
    NEVER pushes to Zettle POS. Used for experimentation and observing
    how parameters and sales vectors affect prices.
    """
    if not os.path.exists(TARGET_CSV_PATH):
        raise HTTPException(status_code=404, detail="Target CSV not found")
    target_df = pd.read_csv(TARGET_CSV_PATH)
    uuids = target_df["variant_uuid"].tolist()
    current_prices = target_df["current_price"].tolist()
    min_prices = target_df["min_price"].tolist()
    normal_prices = (
        target_df["normal_price"].tolist()
        if "normal_price" in target_df.columns
        else target_df["original_price"].tolist()
    )
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

    for index, row in target_df.iterrows():
        var_uuid = row["variant_uuid"]

        if var_uuid in predicted_prices:
            new_price = min(predicted_prices[var_uuid], row["max_price"])
            old_price = row["current_price"]

            target_df.at[index, "current_price"] = new_price

            applied_changes.append({
                "product_name": row["product_name"],
                "variant_uuid": var_uuid,
                "old_price": old_price,
                "new_price": new_price,
                "diff": round(new_price - old_price, 2),
                "pct_change": round(((new_price - old_price) / old_price) * 100, 2) if old_price > 0 else 0.0,
                "quantity": float(req.purchases.get(var_uuid, 0.0)),
            })

    target_df.to_csv(TARGET_CSV_PATH, index=False)

    return {
        "status": "success",
        "applied_changes": applied_changes,
        "engine_state": predictor.get_state(),
        "catalog": target_df.to_dict(orient="records"),
    }

@app.post("/api/engine/emulate/reset")
def emulate_reset():
    """
    Reset local prices and House Bank state back to original baselines.
    NEVER touches Zettle POS.
    """
    if not os.path.exists(TARGET_CSV_PATH):
        raise HTTPException(status_code=404, detail="Target CSV not found")
    target_df = pd.read_csv(TARGET_CSV_PATH)
    target_df["current_price"] = target_df["original_price"]
    target_df.to_csv(TARGET_CSV_PATH, index=False)
    predictor.reset_bank()
    return {
        "status": "success",
        "engine_state": predictor.get_state(),
        "catalog": target_df.to_dict(orient="records"),
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
