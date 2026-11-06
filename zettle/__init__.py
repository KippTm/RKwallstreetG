"""
Zettle Product Library API Helper Package.

This package provides python-callable functions to view current products,
find products, and change/update products in Zettle, outputting JSON-serializable
Python objects.
"""

from .client import ZettleClient
from .exceptions import (
    ZettleError,
    ZettleAuthError,
    ZettleNotFoundError,
    ZettleRequestError,
)
from .products import (
    get_all_products,
    get_product,
    find_products,
    find_product_by_name,
    update_product,
    update_product_price,
    create_product,
    delete_product,
    get_categories,
    get_client,
    reset_client,
    get_token_info,
    clear_token_cache,
)
from .purchases import (
    get_purchases,
    extract_variant_sales,
)

__all__ = [
    "ZettleClient",
    "ZettleError",
    "ZettleAuthError",
    "ZettleNotFoundError",
    "ZettleRequestError",
    "get_all_products",
    "get_product",
    "find_products",
    "find_product_by_name",
    "update_product",
    "update_product_price",
    "create_product",
    "delete_product",
    "get_categories",
    "get_client",
    "reset_client",
    "get_token_info",
    "clear_token_cache",
    "get_purchases",
    "extract_variant_sales",
]

