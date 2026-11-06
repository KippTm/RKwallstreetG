import numpy as np
import pandas as pd
from typing import List, Union, Dict, Any, Optional

_UNSET = object()


class DynamicPricingPredictor:
    """
    Closed-loop Wall Street pricing engine using a fully multiplicative
    (log-normal) model.

    All price forces operate as relative rates of return, so the update is:
        P_{t+1} = P_t * (1 + r_t)   then clipped to [min_price, max_price]

    The return r_t is composed of:
      - Dutch auction decay:  geometric decay towards the floor price
      - Stochastic noise:     Geometric Brownian Motion
      - Zero-sum impact:      purchase self-spike minus cross-penalty
      - PI bank feedback:     integral controller on realised profit deviation

    Profit per sale is defined as:  current_price - normal_price
    The House Bank accumulates how much the bar has earned above (positive)
    or below (negative) what a standard Friday bar would have made.

    Arguments to predict_prices are vectors of arbitrary (but consistent)
    ordering — each index corresponds to the same item across all vectors.
    """

    def __init__(
        self,
        delta_rel: float = 0.03,
        sigma: float = 0.015,
        lambda_rel: float = 0.04,
        omega_rel: float = None,
        gamma_rel: float = 0.0001,
        max_price_multiplier: float = 3.0,
    ):
        """
        Parameters
        ----------
        delta_rel : float
            Dutch auction decay rate. Each tick the price gap above the floor
            shrinks by this fraction: ΔP_decay = -δ_rel * (P - P_min).
        sigma : float
            Geometric Brownian Motion volatility coefficient.
        lambda_rel : float
            Self-impact: fractional price spike per unit sold of *this* item.
        omega_rel : float | None
            Cross-impact penalty fraction per unit sold of *any other* item.
            Defaults to λ / (N-1) for a zero-sum menu (set after N is known).
        gamma_rel : float
            Bank feedback gain. Scales accumulated profit-error Bt into a
            fractional correction applied uniformly across all items.
        max_price_multiplier : float
            Ceiling = this multiplier × min_price (the floor price).
        """
        self.delta_rel = float(delta_rel)
        self.sigma = float(sigma)
        self.lambda_rel = float(lambda_rel)
        self._omega_rel_init = omega_rel          # None → computed on first call
        self.gamma_rel = float(gamma_rel)
        self.max_price_multiplier = float(max_price_multiplier)

        # PI bank state — persists across ticks
        self.bank_balance: float = 0.0
        self.tick_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def predict_prices(
        self,
        current_prices: Union[List[float], np.ndarray],
        min_prices: Union[List[float], np.ndarray],
        normal_prices: Union[List[float], np.ndarray],
        quantities: Union[List[float], np.ndarray],
        uuids: Union[List[str], None] = None,
        commit: bool = True,
        include_noise: bool = True,
    ) -> dict:
        """
        Execute one pricing tick.

        Parameters
        ----------
        current_prices : array-like, shape (N,)
            Current price of each item (DKK). Arbitrary order.
        min_prices : array-like, shape (N,)
            Floor price of each item (DKK).
            Dutch auction decays asymptotically *towards* this value.
        normal_prices : array-like, shape (N,)
            Standard bar price of each item (DKK), i.e. what it would cost
            on a regular Friday night. Bank profit is tracked as
            current_price - normal_price per unit sold.
        quantities : array-like, shape (N,)
            Units sold during this tick interval for each item.
        uuids : list[str] | None
            Optional identifier for each item. If provided, the returned
            dict maps uuid → new_price. Otherwise uses integer indices.
        commit : bool, default True
            If True, permanently commits the profit error to bank_balance
            and increments tick_count. If False, runs as a dry-run preview.
        include_noise : bool, default True
            Whether to add stochastic Brownian noise (sigma * Z).

        Returns
        -------
        dict
            {uuid_or_index: new_price_rounded_to_2dp}
        """
        P      = np.array(current_prices, dtype=np.float64)
        P_min  = np.array(min_prices,     dtype=np.float64)
        P_norm = np.array(normal_prices,  dtype=np.float64)
        Q      = np.array(quantities,     dtype=np.float64)
        N      = len(P)

        keys = list(uuids) if uuids is not None else list(range(N))

        # Resolve omega: default to zero-sum (λ / (N-1))
        omega_rel = (
            self._omega_rel_init
            if self._omega_rel_init is not None
            else (self.lambda_rel / max(N - 1, 1))
        )

        # Ceiling prices (relative to floor, not normal price)
        P_max = 1000000000.0#P_min * self.max_price_multiplier

        # ── 1. Update House Bank ────────────────────────────────────────
        # Profit per sale = current_price - normal_price
        # e_t = Σ Q_i * (P_i - P_norm_i)
        # Positive → bar earned more than a normal Friday; negative → less.
        profit_error = np.sum(Q * (P - P_norm))
        effective_bank = self.bank_balance + profit_error

        # ── 2. Compute multiplicative return components ─────────────────

        # A. Dutch auction: geometric decay towards floor
        #    r_decay = -δ_rel * (P - P_min) / P
        #    So  ΔP ≈ -δ_rel * (P - P_min)   →  floor approached asymptotically
        headroom = np.maximum(P - P_min, 0.0)
        r_decay = -self.delta_rel * headroom / np.maximum(P, 1e-9)

        # B. Geometric Brownian noise
        if include_noise and self.sigma > 0:
            Z = np.random.randn(N)
            r_noise = self.sigma * Z
        else:
            r_noise = np.zeros(N)

        # C. Zero-sum market impact
        #    r_impact_i = λ * Q_i  -  ω * Σ_j Q_j
        Q_total = np.sum(Q)
        r_impact = self.lambda_rel * Q - omega_rel * Q_total

        # D. PI bank feedback (uniform across all items)
        #    r_bank = -γ_rel * B_t
        #    Positive bank (surplus) → mild price drop (give surplus back)
        #    Negative bank (deficit) → mild price rise (recover margin)
        r_bank = np.full(N, -self.gamma_rel * effective_bank)

        # ── 3. Total return and unconstrained update ────────────────────
        r_total = r_decay + r_noise + r_impact + r_bank
        P_raw = P * (1.0 + r_total)

        # ── 4. Project onto feasible bounds [P_min, P_max] ─────────────
        P_new = np.clip(P_raw, P_min, P_max)

        if commit:
            self.bank_balance = effective_bank
            self.tick_count += 1

        return {k: round(float(v), 2) for k, v in zip(keys, P_new)}

    def simulate_step(
        self,
        current_prices: Union[List[float], np.ndarray],
        min_prices: Union[List[float], np.ndarray],
        normal_prices: Union[List[float], np.ndarray],
        quantities: Union[List[float], np.ndarray],
        uuids: Union[List[str], None] = None,
        include_noise: bool = True,
    ) -> dict:
        """
        Simulate a pricing tick without mutating internal state.
        Returns predicted prices, delta from current, percent change,
        and projected bank impact.
        """
        P = np.array(current_prices, dtype=np.float64)
        keys = list(uuids) if uuids is not None else list(range(len(P)))

        predicted = self.predict_prices(
            current_prices=current_prices,
            min_prices=min_prices,
            normal_prices=normal_prices,
            quantities=quantities,
            uuids=keys,
            commit=False,
            include_noise=include_noise,
        )

        P_norm = np.array(normal_prices, dtype=np.float64)
        Q = np.array(quantities, dtype=np.float64)
        profit_error = float(np.sum(Q * (P - P_norm)))

        predictions = {}
        for idx, k in enumerate(keys):
            curr = float(P[idx])
            pred = float(predicted[k])
            diff = round(pred - curr, 2)
            pct = round((diff / curr) * 100, 2) if curr > 0 else 0.0
            predictions[k] = {
                "current_price": curr,
                "predicted_price": pred,
                "diff": diff,
                "pct_change": pct,
                "quantity": float(Q[idx]),
            }

        return {
            "predictions": predictions,
            "projected_profit_error": round(profit_error, 2),
            "current_bank_balance": round(float(self.bank_balance), 2),
            "projected_bank_balance": round(float(self.bank_balance + profit_error), 2),
            "tick": self.tick_count,
        }

    def get_hyperparameters(self) -> dict:
        """Return the current active hyperparameters."""
        return {
            "delta_rel": self.delta_rel,
            "sigma": self.sigma,
            "lambda_rel": self.lambda_rel,
            "omega_rel": self._omega_rel_init,
            "gamma_rel": self.gamma_rel,
            "max_price_multiplier": self.max_price_multiplier,
        }

    def update_hyperparameters(
        self,
        delta_rel: Union[float, object] = _UNSET,
        sigma: Union[float, object] = _UNSET,
        lambda_rel: Union[float, object] = _UNSET,
        omega_rel: Union[float, None, object] = _UNSET,
        gamma_rel: Union[float, object] = _UNSET,
        max_price_multiplier: Union[float, object] = _UNSET,
        **kwargs,
    ) -> dict:
        """
        Update model hyperparameters live without resetting internal state.

        Parameters
        ----------
        delta_rel : float | None
            Dutch auction decay rate toward floor price.
        sigma : float | None
            GBM volatility coefficient.
        lambda_rel : float | None
            Own-price spike per unit sold.
        omega_rel : float | None
            Cross-price penalty per unit sold (pass None to reset to auto zero-sum).
        gamma_rel : float | None
            House Bank PI feedback gain.
        max_price_multiplier : float | None
            Hard ceiling multiplier relative to min_price.
        **kwargs : dict
            Accepts any of the hyperparameter names as keyword arguments.

        Returns
        -------
        dict
            The complete updated hyperparameters dictionary.
        """
        if delta_rel is not _UNSET:
            self.delta_rel = float(delta_rel)
        if sigma is not _UNSET:
            self.sigma = float(sigma)
        if lambda_rel is not _UNSET:
            self.lambda_rel = float(lambda_rel)
        if omega_rel is not _UNSET:
            self._omega_rel_init = float(omega_rel) if omega_rel is not None else None
        if gamma_rel is not _UNSET:
            self.gamma_rel = float(gamma_rel)
        if max_price_multiplier is not _UNSET:
            self.max_price_multiplier = float(max_price_multiplier)

        for key, val in kwargs.items():
            if key == "delta_rel":
                self.delta_rel = float(val)
            elif key == "sigma":
                self.sigma = float(val)
            elif key == "lambda_rel":
                self.lambda_rel = float(val)
            elif key == "omega_rel":
                self._omega_rel_init = float(val) if val is not None else None
            elif key == "gamma_rel":
                self.gamma_rel = float(val)
            elif key == "max_price_multiplier":
                self.max_price_multiplier = float(val)
            else:
                raise ValueError(f"Unknown hyperparameter: '{key}'")

        return self.get_hyperparameters()

    def get_state(self) -> dict:
        """Diagnostic snapshot of internal engine state."""
        return {
            "tick": self.tick_count,
            "bank_balance_dkk": round(self.bank_balance, 2),
            "hyperparameters": self.get_hyperparameters(),
        }

    def reset_bank(self) -> None:
        """Hard-reset the House Bank (e.g. at start of a new event night)."""
        self.bank_balance = 0.0
        self.tick_count = 0

    # ------------------------------------------------------------------
    # Convenience wrapper: accepts DataFrames matching the old interface
    # ------------------------------------------------------------------

    def predict_prices_df(
        self,
        sales_df: pd.DataFrame,
        target_df: pd.DataFrame,
    ) -> dict:
        """
        DataFrame-based wrapper.

        target_df must contain columns:
            variant_uuid, current_price, min_price, normal_price

        sales_df must contain columns:
            variant_uuid, quantity   (may be empty)
        """
        if not sales_df.empty and "quantity" in sales_df.columns:
            volume = sales_df.groupby("variant_uuid")["quantity"].sum()
        else:
            volume = pd.Series(dtype=float)

        uuids          = target_df["variant_uuid"].tolist()
        current_prices = target_df["current_price"].tolist()
        min_prices     = target_df["min_price"].tolist()
        normal_prices  = target_df["normal_price"].tolist()
        quantities     = [float(volume.get(uid, 0)) for uid in uuids]

        return self.predict_prices(
            current_prices=current_prices,
            min_prices=min_prices,
            normal_prices=normal_prices,
            quantities=quantities,
            uuids=uuids,
        )
