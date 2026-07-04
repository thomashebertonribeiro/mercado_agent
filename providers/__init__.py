"""
providers — Interface abstrata para marketplaces.

Cada marketplace (Mercado Livre, Amazon, Shopee, AliExpress) implementa
a mesma interface `MarketplaceProvider` permitindo que o núcleo do
sistema funcione com qualquer fonte sem alteração de código.
"""

from providers.base import (
    CategoryTree,
    MarketplaceProvider,
    ProductListing,
    ProductPage,
    ProviderConfig,
    SearchResultPage,
    SellerPage,
)
from providers.mercadolivre import MercadoLivreProvider

__all__ = [
    "MarketplaceProvider",
    "MercadoLivreProvider",
    "CategoryTree",
    "ProductListing",
    "ProductPage",
    "SellerPage",
    "SearchResultPage",
    "ProviderConfig",
]
