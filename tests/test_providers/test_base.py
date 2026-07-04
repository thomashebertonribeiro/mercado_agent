"""
tests/test_providers/test_base.py

Testes para a interface abstrata MarketplaceProvider.
"""

from providers.base import (
    CategoryTree,
    Currency,
    ListingType,
    MarketplaceProvider,
    ProductCondition,
    ProductListing,
    ProductPage,
    ProviderConfig,
    SearchResultPage,
    SellerPage,
)


class TestProviderSchemas:
    def test_category_tree_defaults(self):
        c = CategoryTree(id="MLB1234", name="Teste")
        assert c.parent_id is None
        assert c.children == []
        assert c.total_items is None

    def test_product_listing_defaults(self):
        p = ProductListing(id="MLB1", title="Produto", price=99.9)
        assert p.currency == Currency.BRL
        assert p.available_quantity == 0
        assert p.condition == ProductCondition.NEW

    def test_product_page_defaults(self):
        p = ProductPage(id="MLB1", title="Produto", price=99.9)
        assert p.pictures == []
        assert p.tags == []
        assert p.questions_count == 0

    def test_seller_page_defaults(self):
        s = SellerPage(id=123, nickname="vendedor")
        assert s.registration_date is None
        assert s.tags == []

    def test_search_result_page(self):
        r = SearchResultPage(query="teste", results=[], total=0, page=1, page_size=50)
        assert r.filters is None

    def test_provider_config_defaults(self):
        c = ProviderConfig()
        assert c.max_retries == 5
        assert c.backoff_base == 2.0
        assert c.max_concurrency == 5

    def test_currency_enum_values(self):
        assert Currency.BRL.value == "BRL"
        assert Currency.ARS.value == "ARS"
        assert Currency.MXN.value == "MXN"

    def test_listing_type_enum(self):
        assert ListingType.GOLD_SPECIAL.value == "gold_special"
        assert ListingType.FREE.value == "free"

    def test_product_condition_enum(self):
        assert ProductCondition.NEW.value == "new"
        assert ProductCondition.REFURBISHED.value == "refurbished"


class TestMarketplaceProviderABC:
    def test_cannot_instantiate_abc(self):
        try:
            MarketplaceProvider()
            assert False, "Deveria levantar TypeError"
        except TypeError:
            pass

    def test_concrete_subclass_must_implement_abstract_methods(self):
        class Incomplete(MarketplaceProvider):
            pass

        try:
            Incomplete()
            assert False, "Deveria levantar TypeError"
        except TypeError:
            pass

    def test_concrete_subclass_compiles(self):
        class FullProvider(MarketplaceProvider):
            @property
            def name(self) -> str:
                return "test"

            @property
            def supported_currencies(self) -> list[Currency]:
                return [Currency.BRL]

            async def get_root_categories(self) -> list[CategoryTree]:
                return []

            async def get_subcategories(self, category_id: str) -> list[CategoryTree]:
                return []

            async def get_category_path(self, category_id: str) -> list[dict]:
                return []

            async def get_products_by_category(
                self, category_id: str, offset: int = 0, limit: int = 50
            ) -> list[ProductListing]:
                return []

            async def get_product_detail(self, product_id: str) -> ProductPage | None:
                return None

            async def get_product_description(self, product_id: str) -> str | None:
                return None

            async def get_seller_detail(self, seller_id: int) -> SellerPage | None:
                return None

            async def search(
                self, query: str, category_id: str | None = None,
                offset: int = 0, limit: int = 50,
            ) -> SearchResultPage:
                return SearchResultPage(query=query, results=[], total=0, page=1, page_size=limit)

        import asyncio
        provider = FullProvider()
        assert provider.name == "test"
        assert Currency.BRL in provider.supported_currencies

        async def _run():
            roots = await provider.get_root_categories()
            assert roots == []

        asyncio.run(_run())
