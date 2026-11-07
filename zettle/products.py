"""Product operations for Zettle Product Library API."""

import uuid
from typing import Any, Dict, List, Optional, Union
from .client import ZettleClient
from .exceptions import ZettleNotFoundError, ZettleError

# Global singleton client instance (lazily initialized)
_default_client: Optional[ZettleClient] = None


def get_client() -> ZettleClient:
    """Get or initialize the default ZettleClient instance using credentials from .env."""
    global _default_client
    if _default_client is None:
        _default_client = ZettleClient()
    return _default_client


def reset_client() -> None:
    """Reset the default client instance (useful if .env or credentials change)."""
    global _default_client
    _default_client = None


def get_all_products(client: Optional[ZettleClient] = None) -> List[Dict[str, Any]]:
    """
    Fetch all products visible in the Zettle POS product library.
    
    Args:
        client: Optional custom ZettleClient. If None, default client is used.
        
    Returns:
        List of JSON-serializable product dictionaries.
    """
    api = client or get_client()
    result = api.request("GET", "/organizations/self/products/v2")
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "products" in result:
        return result["products"]
    return [result] if result else []


def get_product(product_uuid: str, client: Optional[ZettleClient] = None) -> Dict[str, Any]:
    """
    View current details of a specific product by its UUID.
    
    Args:
        product_uuid: Unique UUID of the product.
        client: Optional custom ZettleClient.
        
    Returns:
        JSON-serializable product dictionary.
        
    Raises:
        ZettleNotFoundError: If the product does not exist.
    """
    if not product_uuid:
        raise ValueError("product_uuid must be provided.")
    api = client or get_client()
    return api.request("GET", f"/organizations/self/products/{product_uuid.strip()}")


def find_products(
    query: Optional[str] = None,
    name: Optional[str] = None,
    category_uuid: Optional[str] = None,
    client: Optional[ZettleClient] = None,
) -> List[Dict[str, Any]]:
    """
    Find products matching search criteria (keyword, name, or category).
    
    Args:
        query: Generic search term matched against product name, description, variant names, and SKUs.
        name: Name substring or exact match to filter products.
        category_uuid: UUID of the category to filter by.
        client: Optional custom ZettleClient.
        
    Returns:
        List of JSON-serializable matching product dictionaries.
    """
    all_prods = get_all_products(client=client)
    filtered = []

    query_lower = query.lower().strip() if query else None
    name_lower = name.lower().strip() if name else None
    cat_target = category_uuid.strip().lower() if category_uuid else None

    for prod in all_prods:
        prod_name = str(prod.get("name", "")).lower()
        prod_desc = str(prod.get("description", "")).lower()
        
        # Check category match
        if cat_target:
            prod_cat = prod.get("category", {})
            prod_cat_uuid = prod_cat.get("uuid") if isinstance(prod_cat, dict) else prod.get("categoryUuid")
            if not prod_cat_uuid or str(prod_cat_uuid).lower() != cat_target:
                continue

        # Check name filter
        if name_lower and name_lower not in prod_name:
            continue

        # Check broad query
        if query_lower:
            # Check in product name & description
            matches_name = query_lower in prod_name
            matches_desc = query_lower in prod_desc
            
            # Check in variants
            matches_variant = False
            for v in prod.get("variants", []):
                v_name = str(v.get("name", "")).lower()
                v_sku = str(v.get("sku", "")).lower()
                v_barcode = str(v.get("barcode", "")).lower()
                if query_lower in v_name or query_lower in v_sku or query_lower in v_barcode:
                    matches_variant = True
                    break

            if not (matches_name or matches_desc or matches_variant):
                continue

        filtered.append(prod)

    return filtered


def find_product_by_name(
    name: str,
    exact: bool = False,
    client: Optional[ZettleClient] = None,
) -> Optional[Dict[str, Any]]:
    """
    Find a single product by name.
    
    Args:
        name: Product name to search for.
        exact: If True, requires exact case-insensitive match; otherwise allows substring.
        client: Optional custom ZettleClient.
        
    Returns:
        JSON-serializable product dictionary, or None if not found.
    """
    target = name.strip().lower()
    for prod in get_all_products(client=client):
        prod_name = str(prod.get("name", "")).strip().lower()
        if (target == prod_name) if exact else (target in prod_name):
            return prod
    return None

def update_product(
    product_uuid: str,
    product_data: Dict[str, Any],
    etag: Optional[str] = None,
    client: Optional[ZettleClient] = None,
) -> Dict[str, Any]:
    """
    Change/update an existing product using JSON merge patch.
    Only specified fields will be changed. Explicit None values remove fields.
    """
    if not product_uuid:
        raise ValueError("product_uuid must be provided.")
    api = client or get_client()

    payload = dict(product_data)
    if "uuid" not in payload:
        payload["uuid"] = product_uuid

    headers = {}
    if etag:
        # HTTP RFC 7232 requires ETags in If-Match to be quoted strings
        clean_etag = str(etag).strip()
        if not (clean_etag.startswith('"') and clean_etag.endswith('"')) and not clean_etag.startswith("W/"):
            clean_etag = f'"{clean_etag}"'
        headers["If-Match"] = clean_etag

    return api.request(
        "PUT",
        f"/organizations/self/products/v2/{product_uuid.strip()}",
        json_data=payload,
        headers=headers if headers else None,
    )


def update_product_price(
    product_uuids: Union[str, List[str]],
    price: Union[float, int],
    matching: str = "variant",
    currency: Optional[str] = None,
    variant_uuids: Optional[Union[str, List[str]]] = None,
    is_cents: bool = False,
    client: Optional[ZettleClient] = None,
) -> List[Dict[str, Any]]:
    """
    Convenience helper to change the price of products based on the matching strategy.
    If matching='product', updates ALL variants within the given product_uuids.
    If matching='variant', updates ONLY variants that match the provided variant_uuids.
    """
    api = client or get_client()
    
    # Normalize inputs to lists for easier iteration
    p_uuids = [product_uuids] if isinstance(product_uuids, str) else product_uuids
    v_uuids = []
    if variant_uuids:
        v_uuids = [variant_uuids] if isinstance(variant_uuids, str) else variant_uuids

    amount_int = int(round(price if is_cents else price * 100))
    results = []

    for p_uuid in p_uuids:
        current_prod = get_product(p_uuid, client=api)
        variants = current_prod.get("variants", [])
        
        if not variants:
            continue

        updated_variants = False
        for v in variants:
            # Apply matching strategy
            if matching == "product" or (matching == "variant" and v.get("uuid") in v_uuids):
                curr = currency or v.get("price", {}).get("currencyId", "SEK")
                v["price"] = {
                    "amount": amount_int,
                    "currencyId": curr.upper(),
                }
                updated_variants = True

        if updated_variants:
            patch_payload = {
                "uuid": p_uuid,
                "variants": variants,
            }
            etag = current_prod.get("etag")
            # Defer to update_product which now handles the ETag formatting safely
            res = update_product(p_uuid, patch_payload, etag=etag, client=api)
            results.append(res)

    return results


def create_product(
    product_data: Dict[str, Any],
    client: Optional[ZettleClient] = None,
) -> Dict[str, Any]:
    """
    Create a new product in the Zettle Product Library.
    
    Args:
        product_data: Product details dictionary (name, variants, etc.).
        client: Optional custom ZettleClient.
        
    Returns:
        JSON-serializable created product dictionary.
    """
    api = client or get_client()
    payload = dict(product_data)
    if "uuid" not in payload:
        payload["uuid"] = str(uuid.uuid1())

    # Ensure variants have UUIDs if provided
    if "variants" in payload:
        for var in payload["variants"]:
            if "uuid" not in var:
                var["uuid"] = str(uuid.uuid1())

    return api.request("POST", "/organizations/self/products/v2", json_data=payload)


def delete_product(
    product_uuid: str,
    client: Optional[ZettleClient] = None,
) -> Dict[str, Any]:
    """
    Delete a product from the Zettle Product Library.
    
    Args:
        product_uuid: UUID of the product to delete.
        client: Optional custom ZettleClient.
        
    Returns:
        JSON-serializable dictionary indicating success.
    """
    if not product_uuid:
        raise ValueError("product_uuid must be provided.")
    api = client or get_client()
    api.request("DELETE", f"/organizations/self/products/{product_uuid.strip()}")
    return {"success": True, "deleted_uuid": product_uuid}


def get_categories(client: Optional[ZettleClient] = None) -> List[Dict[str, Any]]:
    """
    Fetch all product categories.
    
    Args:
        client: Optional custom ZettleClient.
        
    Returns:
        List of JSON-serializable category dictionaries.
    """
    api = client or get_client()
    result = api.request("GET", "/organizations/self/categories/v2")
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "categories" in result:
        return result["categories"]
    return [result] if result else []


def get_token_info(client: Optional[ZettleClient] = None) -> Dict[str, Any]:
    """
    Get persistent token status and expiration info.
    
    Args:
        client: Optional custom ZettleClient.
        
    Returns:
        JSON-serializable dictionary with token status.
    """
    api = client or get_client()
    return api.get_token_info()


def clear_token_cache(client: Optional[ZettleClient] = None) -> Dict[str, Any]:
    """
    Clear in-memory and on-disk persistent token cache.
    
    Args:
        client: Optional custom ZettleClient.
        
    Returns:
        Confirmation dictionary.
    """
    api = client or get_client()
    api.clear_token_cache()
    return {"success": True, "message": "Persistent token cache cleared."}

