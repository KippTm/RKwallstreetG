"""
Zettle Purchases API Client functions.

Provides helper functions to fetch purchases chronologically using cursor-based
pagination (lastPurchaseHash), extract drink sales quantities per variant UUID,
and integrate with the continuous pricing engine.
"""

from typing import Any, Dict, List, Optional
from .client import ZettleClient
from .products import get_client

PURCHASES_API_URL = "https://purchase.izettle.com/purchases/v2"


def get_purchases(
    last_purchase_hash: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
    descending: bool = False,
    client: Optional[ZettleClient] = None,
) -> Dict[str, Any]:
    """
    Fetch purchases from Zettle Purchase API v2.

    Parameters
    ----------
    last_purchase_hash : str | None
        Cursor from the previous API call. Only returns purchases after this hash.
    start_date : str | None
        ISO-8601 formatted UTC timestamp (e.g., '2026-11-06T15:00:00.000Z').
    end_date : str | None
        ISO-8601 formatted UTC timestamp.
    limit : int, default 100
        Maximum number of purchases to fetch per request.
    descending : bool, default False
        If False, returns purchases in ascending chronological order (recommended for cursor polling).
    client : ZettleClient | None
        Client instance. If None, default shared client is used.

    Returns
    -------
    dict
        {
            "purchases": List[dict],
            "lastPurchaseHash": str | None,
            "linkHeader": str | None
        }
    """
    if client is None:
        client = get_client()

    params: Dict[str, Any] = {
        "limit": limit,
        "descending": str(descending).lower(),
    }

    if last_purchase_hash:
        params["lastPurchaseHash"] = last_purchase_hash
    elif start_date:
        params["startDate"] = start_date

    if end_date:
        params["endDate"] = end_date

    result = client.request("GET", PURCHASES_API_URL, params=params)
    if isinstance(result, dict):
        return {
            "purchases": result.get("purchases", []),
            "lastPurchaseHash": result.get("lastPurchaseHash"),
            "linkHeader": result.get("linkHeader"),
        }
    elif isinstance(result, list):
        return {"purchases": result, "lastPurchaseHash": None, "linkHeader": None}
    return {"purchases": [], "lastPurchaseHash": None, "linkHeader": None}


def extract_variant_sales(purchases: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Aggregate units sold per variant UUID from a list of Zettle purchases.

    Parameters
    ----------
    purchases : list of dict
        Raw purchase objects from Zettle Purchases API.

    Returns
    -------
    dict
        Mapping of variant_uuid (str) -> total units sold (float).
    """
    volume: Dict[str, float] = {}

    for purchase in purchases:
        # Zettle purchases v2 includes a 'products' list per purchase
        products = purchase.get("products") or purchase.get("items") or purchase.get("lines") or []
        for item in products:
            var_uuid = (
                item.get("variantUuid")
                or item.get("variant_uuid")
                or item.get("uuid")
            )
            if not var_uuid:
                continue

            try:
                qty = float(item.get("quantity", 1.0))
            except (ValueError, TypeError):
                qty = 1.0

            volume[var_uuid] = volume.get(var_uuid, 0.0) + qty

    return volume
