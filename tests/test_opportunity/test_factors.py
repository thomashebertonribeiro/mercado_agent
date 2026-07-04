"""
tests/test_opportunity/test_factors.py

Testes unitários para cada fator do Opportunity Score.

Feature: opportunity-score
"""

import math
import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from opportunity.factors import (
    compute_competition_score,
    compute_competitor_entry_score,
    compute_demand_score,
    compute_growth_score,
    compute_margin_score,
    compute_price_score,
    compute_reviews_score,
    compute_seasonality_score,
    compute_shipping_score,
    compute_volatility_score,
    compute_volume_score,
    compute_weight_score,
)


# --------------------------------------------------------------------------- #
# 1. Demanda                                                                  #
# --------------------------------------------------------------------------- #


class TestDemandScore:
    def test_zero_reviews_returns_low_score(self) -> None:
        score = compute_demand_score(review_count=0, price_events_count=0)
        assert 0.0 <= score <= 0.3

    def test_high_reviews_returns_high_score(self) -> None:
        score = compute_demand_score(review_count=1000, price_events_count=50)
        assert score > 0.7

    def test_score_is_always_between_0_and_1(self) -> None:
        for reviews in [0, 1, 10, 100, 1000]:
            for events in [0, 5, 20, 100]:
                s = compute_demand_score(review_count=reviews, price_events_count=events)
                assert 0.0 <= s <= 1.0, f"review={reviews}, events={events} -> {s}"

    @given(
        reviews=st.integers(min_value=0, max_value=5000),
        events=st.integers(min_value=0, max_value=200),
    )
    @h_settings(max_examples=50)
    def test_property_output_range(self, reviews: int, events: int) -> None:
        score = compute_demand_score(review_count=reviews, price_events_count=events)
        assert 0.0 <= score <= 1.0


# --------------------------------------------------------------------------- #
# 2. Margem                                                                   #
# --------------------------------------------------------------------------- #


class TestMarginScore:
    def test_negative_margin_returns_zero(self) -> None:
        score = compute_margin_score(price=100, estimated_cost=150)
        assert score == 0.0

    def test_ideal_margin_returns_one(self) -> None:
        score = compute_margin_score(price=100, estimated_cost=60)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_no_cost_data_returns_neutral(self) -> None:
        score = compute_margin_score(price=100)
        assert score == 0.5

    def test_price_above_average_increases_score(self) -> None:
        score = compute_margin_score(price=200, category_avg_price=100)
        assert score > 0.5

    def test_price_below_average_decreases_score(self) -> None:
        score = compute_margin_score(price=50, category_avg_price=100)
        assert score < 0.5

    @given(
        price=st.floats(min_value=1, max_value=10000, allow_nan=False, allow_infinity=False),
        cost=st.floats(min_value=0, max_value=10000, allow_nan=False, allow_infinity=False),
    )
    @h_settings(max_examples=50)
    def test_property_output_range(self, price: float, cost: float) -> None:
        score = compute_margin_score(price=price, estimated_cost=cost)
        assert 0.0 <= score <= 1.0


# --------------------------------------------------------------------------- #
# 3. Competição                                                               #
# --------------------------------------------------------------------------- #


class TestCompetitionScore:
    def test_no_sellers_returns_low_score(self) -> None:
        score = compute_competition_score(seller_count=0)
        assert score == 0.0

    def test_few_sellers_returns_high_score(self) -> None:
        score = compute_competition_score(seller_count=5)
        assert score > 0.5

    def test_many_sellers_returns_low_score(self) -> None:
        score = compute_competition_score(seller_count=100)
        assert score == 0.0

    def test_bell_curve_with_optimal(self) -> None:
        score_at_optimal = compute_competition_score(
            seller_count=10, optimal_count=10, saturation=20
        )
        score_far = compute_competition_score(
            seller_count=50, optimal_count=10, saturation=20
        )
        assert score_at_optimal > score_far

    @given(
        count=st.integers(min_value=0, max_value=200),
    )
    @h_settings(max_examples=50)
    def test_property_output_range(self, count: int) -> None:
        score = compute_competition_score(seller_count=count)
        assert 0.0 <= score <= 1.0


# --------------------------------------------------------------------------- #
# 4. Crescimento                                                              #
# --------------------------------------------------------------------------- #


class TestGrowthScore:
    def test_positive_slope_returns_positive_score(self) -> None:
        score = compute_growth_score(price_slope=0.02, r_squared=0.8, mean_price=100)
        assert score > 0.0

    def test_negative_slope_returns_zero(self) -> None:
        score = compute_growth_score(price_slope=-0.02, r_squared=0.8, mean_price=100)
        assert score == 0.0

    def test_zero_slope_returns_zero(self) -> None:
        """Slope zero = sem crescimento = score 0."""
        score = compute_growth_score(price_slope=0.0, r_squared=0.8, mean_price=100)
        assert score == 0.0

    @given(
        slope=st.floats(min_value=-0.1, max_value=0.1, allow_nan=False, allow_infinity=False),
    )
    @h_settings(max_examples=50)
    def test_property_output_range(self, slope: float) -> None:
        score = compute_growth_score(
            price_slope=slope, r_squared=0.5, mean_price=100
        )
        assert 0.0 <= score <= 1.0


# --------------------------------------------------------------------------- #
# 5. Avaliações                                                               #
# --------------------------------------------------------------------------- #


class TestReviewsScore:
    def test_zero_reviews_returns_zero(self) -> None:
        assert compute_reviews_score(review_count=0) == 0.0

    def test_above_target_returns_one(self) -> None:
        assert compute_reviews_score(review_count=500) == 1.0

    def test_partial_reviews(self) -> None:
        assert compute_reviews_score(review_count=100) == pytest.approx(0.5)

    @given(
        count=st.integers(min_value=0, max_value=2000),
    )
    @h_settings(max_examples=50)
    def test_property_output_range(self, count: int) -> None:
        score = compute_reviews_score(review_count=count)
        assert 0.0 <= score <= 1.0


# --------------------------------------------------------------------------- #
# 6. Entrada de Concorrentes                                                  #
# --------------------------------------------------------------------------- #


class TestCompetitorEntryScore:
    def test_no_new_sellers_returns_one(self) -> None:
        assert compute_competitor_entry_score(new_sellers_per_month=0) == 1.0

    def test_above_max_returns_zero(self) -> None:
        assert compute_competitor_entry_score(new_sellers_per_month=20) == 0.0

    def test_partial_entry(self) -> None:
        score = compute_competitor_entry_score(new_sellers_per_month=5)
        assert score == pytest.approx(0.5)

    @given(
        entry=st.floats(min_value=0, max_value=50, allow_nan=False, allow_infinity=False),
    )
    @h_settings(max_examples=50)
    def test_property_output_range(self, entry: float) -> None:
        score = compute_competitor_entry_score(new_sellers_per_month=entry)
        assert 0.0 <= score <= 1.0


# --------------------------------------------------------------------------- #
# 7. Preço                                                                    #
# --------------------------------------------------------------------------- #


class TestPriceScore:
    def test_exact_optimal_returns_one(self) -> None:
        score = compute_price_score(price=100, optimal_price=100)
        assert score == 1.0

    def test_far_from_optimal_returns_low(self) -> None:
        score = compute_price_score(price=500, optimal_price=100)
        assert score < 0.3

    def test_mid_deviation(self) -> None:
        score = compute_price_score(price=75, optimal_price=100)
        assert 0.4 < score < 0.6

    @given(
        p=st.floats(min_value=1, max_value=1000, allow_nan=False, allow_infinity=False),
    )
    @h_settings(max_examples=50)
    def test_property_output_range(self, p: float) -> None:
        score = compute_price_score(price=p, optimal_price=100)
        assert 0.0 <= score <= 1.0


# --------------------------------------------------------------------------- #
# 8. Volatilidade                                                             #
# --------------------------------------------------------------------------- #


class TestVolatilityScore:
    def test_zero_volatility_returns_one(self) -> None:
        assert compute_volatility_score(coefficient_of_variation=0.0) == 1.0

    def test_high_volatility_returns_zero(self) -> None:
        assert compute_volatility_score(coefficient_of_variation=0.5) == 0.0

    @given(
        cv=st.floats(min_value=0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    @h_settings(max_examples=50)
    def test_property_output_range(self, cv: float) -> None:
        score = compute_volatility_score(coefficient_of_variation=cv)
        assert 0.0 <= score <= 1.0


# --------------------------------------------------------------------------- #
# 9. Sazonalidade                                                             #
# --------------------------------------------------------------------------- #


class TestSeasonalityScore:
    def test_no_seasonality_returns_one(self) -> None:
        assert compute_seasonality_score(seasonality_index=0.0) == 1.0

    def test_high_seasonality_returns_zero(self) -> None:
        assert compute_seasonality_score(seasonality_index=1.0) == 0.0

    @given(
        idx=st.floats(min_value=0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    @h_settings(max_examples=50)
    def test_property_output_range(self, idx: float) -> None:
        score = compute_seasonality_score(seasonality_index=idx)
        assert 0.0 <= score <= 1.0


# --------------------------------------------------------------------------- #
# 10. Frete                                                                   #
# --------------------------------------------------------------------------- #


class TestShippingScore:
    def test_free_shipping_returns_high(self) -> None:
        assert compute_shipping_score(shipping_cost=0, price=100) == 0.5

    def test_expensive_shipping_returns_low(self) -> None:
        score = compute_shipping_score(shipping_cost=50, price=100)
        assert score < 0.3

    def test_no_data_returns_neutral(self) -> None:
        assert compute_shipping_score(shipping_cost=0, price=0) == 0.5

    @given(
        cost=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
        p=st.floats(min_value=1, max_value=1000, allow_nan=False, allow_infinity=False),
    )
    @h_settings(max_examples=50)
    def test_property_output_range(self, cost: float, p: float) -> None:
        score = compute_shipping_score(shipping_cost=cost, price=p)
        assert 0.0 <= score <= 1.0


# --------------------------------------------------------------------------- #
# 11. Peso                                                                    #
# --------------------------------------------------------------------------- #


class TestWeightScore:
    def test_light_weight_returns_high(self) -> None:
        assert compute_weight_score(weight_kg=0.5) > 0.9

    def test_heavy_weight_returns_low(self) -> None:
        assert compute_weight_score(weight_kg=50) == 0.0

    def test_unknown_weight_returns_one(self) -> None:
        assert compute_weight_score(weight_kg=0) == 1.0

    @given(
        w=st.floats(min_value=0, max_value=100, allow_nan=False, allow_infinity=False),
    )
    @h_settings(max_examples=50)
    def test_property_output_range(self, w: float) -> None:
        score = compute_weight_score(weight_kg=w)
        assert 0.0 <= score <= 1.0


# --------------------------------------------------------------------------- #
# 12. Volume                                                                  #
# --------------------------------------------------------------------------- #


class TestVolumeScore:
    def test_small_volume_returns_high(self) -> None:
        assert compute_volume_score(volume_cm3=100) > 0.9

    def test_large_volume_returns_low(self) -> None:
        assert compute_volume_score(volume_cm3=200000) == 0.0

    def test_unknown_volume_returns_one(self) -> None:
        assert compute_volume_score(volume_cm3=0) == 1.0

    @given(
        v=st.floats(min_value=0, max_value=500000, allow_nan=False, allow_infinity=False),
    )
    @h_settings(max_examples=50)
    def test_property_output_range(self, v: float) -> None:
        score = compute_volume_score(volume_cm3=v)
        assert 0.0 <= score <= 1.0
