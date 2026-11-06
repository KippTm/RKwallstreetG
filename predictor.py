import pandas as pd
import numpy as np

class DynamicPricingPredictor:
    def __init__(self, volatility_limit=0.10):
        self.volatility_limit = volatility_limit

    def predict_prices(self, sales_df: pd.DataFrame, target_df: pd.DataFrame) -> dict:
        predictions = {}
        
        if sales_df.empty:
            for _, row in target_df.iterrows():
                predictions[row['variant_uuid']] = round(row['current_price'] * 0.95, 2)
            return predictions

        if 'quantity' in sales_df.columns:
            volume = sales_df.groupby('variant_uuid')['quantity'].sum()
        else:
            volume = pd.Series(dtype=float)

        avg_vol = volume.mean() if not volume.empty else 0

        for _, row in target_df.iterrows():
            var_uuid = row['variant_uuid']
            curr_price = row['current_price']
            qty = volume.get(var_uuid, 0)
            
            if avg_vol > 0:
                shift_factor = ((qty - avg_vol) / avg_vol) * self.volatility_limit
            else:
                shift_factor = -self.volatility_limit
            
            shift_factor = np.clip(shift_factor, -self.volatility_limit, self.volatility_limit)
            predictions[var_uuid] = round(curr_price * (1 + shift_factor), 2)

        return predictions

