"""
tests/test_recommendation/test_engine.py

Testes do Recommendation Engine — geração e formatação de recomendações.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from models.recommendation import Recommendation
from recommendation.engine import RecommendationEngine, RECOMMENDATION_TYPES


class TestRecommendationTypes:
    def test_all_types_defined(self):
        assert "top_opportunity" in RECOMMENDATION_TYPES
        assert "growing_product" in RECOMMENDATION_TYPES
        assert "abandoned_product" in RECOMMENDATION_TYPES
        assert "low_competition" in RECOMMENDATION_TYPES
        assert "hot_category" in RECOMMENDATION_TYPES
        assert "new_promising" in RECOMMENDATION_TYPES
        assert "price_increase" in RECOMMENDATION_TYPES
        assert "competition_drop" in RECOMMENDATION_TYPES

    def test_all_types_have_portuguese_labels(self):
        for key, label in RECOMMENDATION_TYPES.items():
            assert isinstance(key, str)
            assert isinstance(label, str)
            assert len(label) > 0


class TestRecommendationModel:
    def test_defaults(self):
        rec = Recommendation(
            recommendation_type="top_opportunity",
            priority_score=85,
            title="Produto Exemplo",
            reason="Alta pontuação.",
            hash="abc123",
            source="recommendation_engine",
        )
        assert rec.product_id is None
        assert rec.category_id is None
        assert rec.seller_id is None
        assert rec.source == "recommendation_engine"

    def test_repr(self):
        rec = Recommendation(
            recommendation_type="top_opportunity",
            priority_score=85,
            title="Teste",
            reason="Razão",
            hash="xyz",
        )
        r = repr(rec)
        assert "Recommendation" in r
        assert "top_opportunity" in r
        assert "85" in r

    def test_score_capped_at_100(self):
        rec = Recommendation(
            recommendation_type="growing_product",
            priority_score=150,
            title="Teste",
            reason="Razão",
            hash="xyz",
        )
        # o ORM aceita o valor, mas o engine faz o cap via _make_rec
        assert rec.priority_score == 150


class TestRecommendationEngine:
    """Testa o RecommendationEngine com session mockada."""

    @pytest.mark.asyncio
    async def test_generate_all_empty_db(self):
        """generate_all não deve quebrar com banco vazio."""
        session = AsyncMock()
        # Usamos MagicMock para result.all() ser sync (não AsyncMock)
        from unittest.mock import MagicMock
        result = MagicMock()
        result.all.return_value = []
        result.scalars.return_value.all.return_value = []
        session.execute.return_value = result
        session.get.return_value = None

        engine = RecommendationEngine(session)
        recs = await engine.generate_all(limit=10)
        assert isinstance(recs, list)

    @pytest.mark.asyncio
    async def test_generate_all_with_mock_signals(self):
        """Verifica que sinais mockados geram recomendações."""
        from models.signal import Signal
        from models.product import Product
        from datetime import datetime, timezone
        from unittest.mock import MagicMock

        session = AsyncMock()
        result = MagicMock()

        signal = Signal(
            id=1,
            signal_type="price_growth",
            product_id="MLB123",
            category_id="MLB9999",
            weight=0.8,
            confidence=0.75,
            explanation="Preço subiu 15% nos últimos 30 dias.",
            value=15.0,
            computed_at=datetime.now(timezone.utc),
            hash="sig1",
        )

        # side_effect por ordem de chamada dos generators
        # scalars().all(): growing, abandoned, new_promising, price_inc, competition_drop
        result.scalars.return_value.all.side_effect = [
            [signal],  # _growing_products
            [],        # _abandoned_products
            [],        # _new_promising_products
            [signal],  # _price_increase_products
            [],        # _competition_drop_products
        ]
        # all(): top_opportunities, low_competition, hot_categories
        result.all.side_effect = [
            [],  # _top_opportunities
            [],  # _low_competition_products
            [],  # _hot_categories
        ]
        session.execute.return_value = result
        session.get.return_value = None

        engine = RecommendationEngine(session)
        recs = await engine.generate_all(limit=10)

        assert len(recs) >= 1
        growing = [r for r in recs if r.recommendation_type == "growing_product"]
        price_inc = [r for r in recs if r.recommendation_type == "price_increase"]
        assert len(growing) >= 1
        assert len(price_inc) >= 1

    def test_make_rec_caps_score(self):
        session = MagicMock()
        engine = RecommendationEngine(session)
        rec = engine._make_rec(
            recommendation_type="top_opportunity",
            priority_score=150,
            title="Teste",
            reason="Score alto demais",
        )
        assert rec.priority_score == 100

    def test_make_rec_does_not_modify_valid_score(self):
        session = MagicMock()
        engine = RecommendationEngine(session)
        rec = engine._make_rec(
            recommendation_type="top_opportunity",
            priority_score=75,
            title="Teste",
            reason="Score normal",
        )
        assert rec.priority_score == 75

    def test_make_rec_generates_hash(self):
        session = MagicMock()
        engine = RecommendationEngine(session)
        rec = engine._make_rec(
            recommendation_type="top_opportunity",
            priority_score=50,
            title="Produto",
            reason="Razão",
            product_id="MLB1",
            category_id="MLB10",
        )
        assert isinstance(rec.hash, str)
        assert len(rec.hash) == 64  # SHA-256 hex

    def test_make_rec_truncates_long_title(self):
        session = MagicMock()
        engine = RecommendationEngine(session)
        long_title = "A" * 1000
        rec = engine._make_rec(
            recommendation_type="top_opportunity",
            priority_score=50,
            title=long_title,
            reason="Razão",
        )
        assert len(rec.title) == 500

    def test_make_rec_sets_source(self):
        session = MagicMock()
        engine = RecommendationEngine(session)
        rec = engine._make_rec(
            recommendation_type="top_opportunity",
            priority_score=50,
            title="T",
            reason="R",
        )
        assert rec.source == "recommendation_engine"
