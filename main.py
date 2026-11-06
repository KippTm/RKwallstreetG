import os
import uvicorn
import pandas as pd

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

predictor = DynamicPricingPredictor()
TARGET_CSV_PATH = "target_products.csv"
SALES_DATA_PATH = "september_sales.csv"

class PriceOverride(BaseModel):
    variant_uuid: str
    new_price: float

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

    predicted_prices = predictor.predict_prices(sales_df, target_df)
    updates_made = False
    
    for index, row in target_df.iterrows():
        var_uuid = row["variant_uuid"]
        prod_uuid = row["product_uuid"]
        
        if var_uuid in predicted_prices:
            new_price = predicted_prices[var_uuid]
            clamped_price = max(row["min_price"], min(row["max_price"], new_price))
            
            if clamped_price != row["current_price"]:
                try:
                    update_product_price(
                        product_uuid=prod_uuid,
                        price=clamped_price,
                        variant_uuid=var_uuid,
                        is_cents=False
                    )
                    target_df.at[index, "current_price"] = clamped_price
                    updates_made = True
                    print(f"Updated {row['product_name']}: -> {clamped_price}")
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

@app.get("/admin")
def admin_portal():
    return FileResponse("templates/admin.html")

if __name__ == "__main__":
    print("Starting Wall Street Bar - Dynamic Pricing Engine...")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
