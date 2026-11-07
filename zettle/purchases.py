"""
Zettle Purchases API Client functions.

Provides helper functions to fetch purchases chronologically using cursor-based
pagination (lastPurchaseHash), extract drink sales quantities per variant UUID,
and integrate with the continuous pricing engine.
"""

from typing import Any, Dict, List, Optional
import datetime
from .client import ZettleClient
from .products import get_client

PURCHASES_API_URL = "https://purchase.izettle.com/purchases/v2"

# Saturday 7 November 2026, 13:00:00 CET (UTC+1) = 12:00:00 UTC
DEFAULT_START_TIME_UTC = "2026-11-07T12:00:00.000Z"
DEFAULT_START_DATETIME = datetime.datetime(2026, 11, 7, 12, 0, 0, tzinfo=datetime.timezone.utc)


def get_purchases(
    last_purchase_hash: Optional[str] = None,
    start_date: Optional[str] = DEFAULT_START_TIME_UTC,
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
        ISO-8601 formatted UTC timestamp (defaults to 2026-11-07T12:00:00.000Z, i.e. 13:00 CET).
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


def extract_variant_sales(
    purchases: List[Dict[str, Any]],
    cutoff_time: Optional[datetime.datetime] = DEFAULT_START_DATETIME,
) -> tuple[Dict[str, float], Dict[str, float]]:
    """
    Aggregate units sold per variant UUID and per product UUID from a list of
    Zettle purchases.

    Filters out purchases occurring before cutoff_time (defaults to Saturday 7 Nov 13:00 CET).

    Some products.json entries use ``"matching": "variant"`` and should be
    looked up by their ``variantUuid``; others use ``"matching": "product"``
    and should be summed across *all* variants belonging to the same
    ``productUuid`` (covering products whose Zettle catalogue entry has
    multiple variants / multiple product UUIDs that all count as the same
    market item).

    Parameters
    ----------
    purchases : list of dict
        Raw purchase objects from Zettle Purchases API.
    cutoff_time : datetime.datetime | None
        Earliest purchase time to process. Earlier purchases are ignored.

    Returns
    -------
    by_variant : dict[str, float]
        variant_uuid -> total units sold.
    by_product : dict[str, float]
        product_uuid -> total units sold (all variant lines summed).
    """
    by_variant: Dict[str, float] = {}
    by_product: Dict[str, float] = {}

    for purchase in purchases:
        # Client-side timestamp check against cutoff time
        if cutoff_time is not None:
            ts_str = purchase.get("timestamp") or purchase.get("created") or purchase.get("purchaseDate")
            if ts_str:
                try:
                    dt = datetime.datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    if dt < cutoff_time:
                        continue
                except Exception:
                    pass

        lines = (
            purchase.get("products")
            or purchase.get("items")
            or purchase.get("lines")
            or []
        )
        for item in lines:
            var_uuid = (
                item.get("variantUuid")
                or item.get("variant_uuid")
                or item.get("uuid")
            )
            prod_uuid = (
                item.get("productUuid")
                or item.get("product_uuid")
            )

            try:
                qty = float(item.get("quantity", 1.0))
            except (ValueError, TypeError):
                qty = 1.0

            if var_uuid:
                by_variant[var_uuid] = by_variant.get(var_uuid, 0.0) + qty

            if prod_uuid:
                by_product[prod_uuid] = by_product.get(prod_uuid, 0.0) + qty

    return by_variant, by_product
