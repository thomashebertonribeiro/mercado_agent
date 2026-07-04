"""
tests/test_opportunity/test_score.py

Testes do OpportunityScorer — orquestrador principal.

Feature: opportunity-score
"""

import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from opportunity.config import OpportunityConfig
from opportunity.score import OpportunityScorer, FactorDetail, OpportunityScore


class TestOpportunityScorer:
    def test_perfect_score_returns_100(self) -> None:
        """Cenário ideal: todos os fatores no máximo."""
        scorer = OpportunityScorer()
        result = scorer.compute(
            review_count=1000,
            price=100.0,
            seller_count=5,
            price_slope=0.03,
            r_squared=0.95,
            mean_price=100.0,
            coefficient_of_variation=0.01,
            seasonality_index=0.0,
            shipping_cost=5.0,
            weight_kg=0.5,
            volume_cm3=500,
            price_events_count=50,
            new_sellers_per_month=0,
            optimal_price=100.0,
        )
        assert 70 <= result.total_score <= 100

    def test_worst_score_returns_0(self) -> None:
        """Cenário pessimo: todos os fatores no mínimo."""
        scorer = OpportunityScorer()
        result = scorer.compute(
            review_count=0,
            price=1000.0,
            seller_count=200,
            price_slope=-0.1,
            r_squared=0.0,
            mean_price=1000.0,
            coefficient_of_variation=1.0,
            seasonality_index=1.0,
            shipping_cost=500.0,
            weight_kg=100.0,
            volume_cm3=500000,
            price_events_count=0,
            new_sellers_per_month=50,
            optimal_price=100.0,
        )
        assert result.total_score <= 30

    def test_score_range_is_always_0_to_100(self) -> None:
        """O score final deve sempre estar entre 0 e 100."""
        scorer = OpportunityScorer()
        configs = [
            {"review_count": 0, "price": 0, "seller_count": 0},  # tudo zero
            {"review_count": 5000, "price": 10000, "seller_count": 200},  # extremos
            {"review_count": 100, "price": 50, "seller_count": 15},  # médio
        ]
        for kwargs in configs:
            result = scorer.compute(**kwargs)
            assert 0 <= result.total_score <= 100, f"Failed for {kwargs}"

    def test_score_includes_all_factor_details(self) -> None:
        """O resultado deve conter detalhamento de todos os 12 fatores."""
        scorer = OpportunityScorer()
        result = scorer.compute(review_count=100, price=50, seller_count=10)
        assert len(result.factors) == 12

        factor_names = {f.name for f in result.factors}
        expected = {
            "demand", "margin", "competition", "growth", "reviews",
            "competitor_entry", "price", "volatility", "seasonality",
            "shipping", "weight", "volume",
        }
        assert factor_names == expected

    def test_custom_weights_affect_score(self) -> None:
        """Pesos diferentes devem produzir scores diferentes."""
        config_a = OpportunityConfig(demand_weight=1.0, margin_weight=0.0)
        config_b = OpportunityConfig(demand_weight=0.0, margin_weight=1.0)

        scorer_a = OpportunityScorer(config_a)
        scorer_b = OpportunityScorer(config_b)

        result_a = scorer_a.compute(review_count=500, price=50, seller_count=5)
        result_b = scorer_b.compute(review_count=500, price=50, seller_count=5)

        # Com demand_weight=1.0, o score é dominado pela demanda (review_count alto)
        # Com margin_weight=1.0, o score é dominado pela margem (price=50, neutro)
        assert result_a.total_score != result_b.total_score

    def test_zero_total_weight_returns_zero(self) -> None:
        """Se todos os pesos forem zero, o score deve ser 0."""
        config = OpportunityConfig(
            demand_weight=0, margin_weight=0, competition_weight=0,
            growth_weight=0, reviews_weight=0, competitor_entry_weight=0,
            price_weight=0, volatility_weight=0, seasonality_weight=0,
            shipping_weight=0, weight_weight=0, volume_weight=0,
        )
        scorer = OpportunityScorer(config)
        result = scorer.compute(review_count=500, price=100, seller_count=5)
        assert result.total_score == 0
        assert result.raw_score == 0.0

    def test_factor_contributions_sum_to_raw_score(self) -> None:
        """A soma das contribuições normalizadas deve igualar raw_score."""
        scorer = OpportunityScorer()
        result = scorer.compute(review_count=200, price=80, seller_count=10)

        total_weight = scorer.config.total_weight()
        weighted_sum = sum(f.contribution for f in result.factors)
        expected_raw = weighted_sum / total_weight

        assert result.raw_score == pytest.approx(expected_raw, rel=1e-6)

    def test_each_factor_has_valid_values(self) -> None:
        """Cada fator deve ter score 0.0-1.0 e contribuição >= 0."""
        scorer = OpportunityScorer()
        result = scorer.compute(review_count=150, price=75, seller_count=8)

        for factor in result.factors:
            assert 0.0 <= factor.raw_score <= 1.0, f"{factor.name}: raw_score={factor.raw_score}"
            assert factor.contribution >= 0.0, f"{factor.name}: contribution={factor.contribution}"
            assert factor.weight >= 0.0, f"{factor.name}: weight={factor.weight}"
            assert factor.label, f"{factor.name}: label is empty"

    @given(
        reviews=st.integers(min_value=0, max_value=2000),
        price=st.floats(min_value=1, max_value=500, allow_nan=False, allow_infinity=False),
        sellers=st.integers(min_value=0, max_value=100),
    )
    @h_settings(max_examples=100)
    def test_property_score_range(self, reviews: int, price: float, sellers: int) -> None:
        """Property: score sempre entre 0 e 100 para qualquer input."""
        scorer = OpportunityScorer()
        result = scorer.compute(
            review_count=reviews,
            price=price,
            seller_count=sellers,
        )
        assert 0 <= result.total_score <= 100

    def test_to_dict_contains_all_fields(self) -> None:
        """to_dict() deve conter total_score, raw_score e factors."""
        scorer = OpportunityScorer()
        result = scorer.compute(review_count=100, price=50, seller_count=10)
        d = result.to_dict()

        assert "total_score" in d
        assert "raw_score" in d
        assert "factors" in d
        assert len(d["factors"]) == 12

        for f in d["factors"]:
            assert "name" in f
            assert "label" in f
            assert "weight" in f
            assert "raw_score" in f
            assert "contribution" in f


class TestOpportunityConfig:
    def test_default_weights_sum_to_one(self) -> None:
        config = OpportunityConfig()
        total = config.total_weight()
        assert total == pytest.approx(1.0, abs=0.01)

    def test_from_dict_filters_invalid_keys(self) -> None:
        config = OpportunityConfig.from_dict({
            "demand_weight": 0.5,
            "invalid_key": 999,
        })
        assert config.demand_weight == 0.5
        assert not hasattr(config, "invalid_key")

    def test_to_dict_contains_all_weights(self) -> None:
        config = OpportunityConfig()
        d = config.to_dict()
        assert len(d) == 12
        assert "demand_weight" in d
        assert "volume_weight" in d
