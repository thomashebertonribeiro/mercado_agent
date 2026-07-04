"""
tests/test_ai_analyst/test_analyst.py

Testes para o AI Analyst.
"""

from datetime import datetime, timezone

from ai_analyst import AIAnalyst
from ai_analyst.schemas import (
    AnalystInput,
    CategoryInfo,
    FactorData,
    PricePoint,
    ProductInfo,
    SignalData,
)


def _make_input(
    score: float = 0.5,
    review_count: int = 100,
    seller_count: int = 10,
    cv: float = 0.03,
    slope: float = 0.001,
    r_squared: float = 0.5,
    price_events: int = 30,
    days: int = 30,
    signals: list[SignalData] | None = None,
    factors: list[FactorData] | None = None,
) -> AnalystInput:
    base_factors = factors or [
        FactorData(name="demand", label="Demanda", weight=0.15, raw_score=0.5, contribution=0.075),
        FactorData(name="margin", label="Margem", weight=0.15, raw_score=0.5, contribution=0.075),
        FactorData(name="competition", label="Competição", weight=0.10, raw_score=0.5, contribution=0.05),
        FactorData(name="growth", label="Crescimento", weight=0.10, raw_score=0.5, contribution=0.05),
        FactorData(name="reviews", label="Reviews", weight=0.08, raw_score=0.5, contribution=0.04),
        FactorData(name="competitor_entry", label="Entrada Concorrentes", weight=0.10, raw_score=0.5, contribution=0.05),
        FactorData(name="price", label="Preço", weight=0.08, raw_score=0.5, contribution=0.04),
        FactorData(name="volatility", label="Volatilidade", weight=0.08, raw_score=0.5, contribution=0.04),
        FactorData(name="seasonality", label="Sazonalidade", weight=0.05, raw_score=0.5, contribution=0.025),
        FactorData(name="shipping", label="Frete", weight=0.05, raw_score=0.5, contribution=0.025),
        FactorData(name="weight", label="Peso", weight=0.03, raw_score=0.5, contribution=0.015),
        FactorData(name="volume", label="Volume", weight=0.03, raw_score=0.5, contribution=0.015),
    ]
    return AnalystInput(
        product=ProductInfo(
            id="MLB999",
            title="Produto Teste",
            category_id="MLB9999",
            seller_id=1,
            condition="new",
            listing_type="gold_special",
            initial_price=100.0,
        ),
        category=CategoryInfo(id="MLB9999", name="Categoria Teste"),
        opportunity_score=round(score * 100),
        opportunity_raw_score=score,
        factors=base_factors,
        signals=signals or [],
        price_history=[
            PricePoint(occurred_at=datetime.now(timezone.utc), price=100.0),
            PricePoint(occurred_at=datetime.now(timezone.utc), price=101.0),
            PricePoint(occurred_at=datetime.now(timezone.utc), price=102.0),
        ],
        price_slope=slope,
        price_r_squared=r_squared,
        mean_price=101.0,
        coefficient_of_variation=cv,
        review_count=review_count,
        price_events_count=price_events,
        seller_count=seller_count,
        days_of_data=days,
    )


class TestAIAnalyst:
    def test_report_has_all_sections(self):
        data = _make_input()
        analyst = AIAnalyst()
        report = analyst.analyze(data)
        d = report.to_dict()

        assert "executive_summary" in d
        assert "risks" in d
        assert "opportunities" in d
        assert "recommendation_reasons" in d
        assert "possible_actions" in d
        assert "confidence" in d
        assert d["product_id"] == "MLB999"

    def test_confidence_is_between_0_and_1(self):
        data = _make_input(price_events=10, days=10)
        analyst = AIAnalyst()
        report = analyst.analyze(data)
        assert 0.0 <= report.confidence_score <= 1.0

    def test_high_score_generates_opportunity(self):
        factors = [
            FactorData(name=n, label=n, weight=0.08, raw_score=0.9, contribution=0.072)
            for n in ["demand", "margin", "competition", "growth", "reviews",
                       "competitor_entry", "price", "volatility",
                       "seasonality", "shipping", "weight", "volume"]
        ]
        data = _make_input(score=0.85, factors=factors)
        analyst = AIAnalyst()
        report = analyst.analyze(data)

        has_high_opportunity = any(
            o.potential == "alto" and "alto score" in o.opportunity.lower()
            for o in report.opportunities
        )
        assert has_high_opportunity

    def test_product_abandoned_risk_is_high_severity(self):
        now = datetime.now(timezone.utc)
        signals = [
            SignalData(
                signal_type="product_abandoned",
                label="Produto abandonado",
                confidence=0.9,
                weight=0.8,
                value=45.0,
                explanation="Produto sem atualizações há 45 dias.",
                extra_data=None,
                computed_at=now,
            ),
        ]
        data = _make_input(signals=signals)
        analyst = AIAnalyst()
        report = analyst.analyze(data)

        abandoned_risks = [r for r in report.risks if "abandonado" in r.risk.lower()]
        assert len(abandoned_risks) >= 1
        assert abandoned_risks[0].severity == "alto"

    def test_high_volatility_risk(self):
        now = datetime.now(timezone.utc)
        signals = [
            SignalData(
                signal_type="high_volatility",
                label="Alta volatilidade",
                confidence=0.85,
                weight=0.7,
                value=0.12,
                explanation="CV de 12%, acima do limiar.",
                extra_data=None,
                computed_at=now,
            ),
        ]
        data = _make_input(signals=signals, cv=0.12)
        analyst = AIAnalyst()
        report = analyst.analyze(data)

        vol_risks = [r for r in report.risks if "volatilidade" in r.risk.lower()]
        assert len(vol_risks) >= 1
        assert vol_risks[0].severity == "alto"

    def test_every_justification_is_non_empty(self):
        data = _make_input()
        analyst = AIAnalyst()
        report = analyst.analyze(data)
        d = report.to_dict()

        for risk in d["risks"]:
            assert risk["justification"], f"Risco '{risk['risk']}' sem justificativa"
        for opp in d["opportunities"]:
            assert opp["justification"], f"Oportunidade '{opp['opportunity']}' sem justificativa"
        for action in d["possible_actions"]:
            assert action["reasoning"], f"Ação '{action['action']}' sem reasoning"

    def test_recommendation_reasons_mention_data(self):
        data = _make_input(r_squared=0.85, slope=0.002, cv=0.02)
        analyst = AIAnalyst()
        report = analyst.analyze(data)

        all_text = " ".join(report.recommendation_reasons)
        assert "R²" in all_text or "r_squared" in all_text
        assert "CV" in all_text or "volatilidade" in all_text

    def test_low_price_events_lowers_confidence(self):
        low_data = _make_input(price_events=3, days=5)
        high_data = _make_input(price_events=200, days=180)

        analyst = AIAnalyst()
        low_report = analyst.analyze(low_data)
        high_report = analyst.analyze(high_data)

        assert low_report.confidence_score < high_report.confidence_score

    def test_actions_have_priority(self):
        data = _make_input()
        analyst = AIAnalyst()
        report = analyst.analyze(data)
        d = report.to_dict()

        for action in d["possible_actions"]:
            assert action["priority"] in ("baixa", "média", "alta")

    def test_no_signals_generates_no_risk_signal(self):
        data = _make_input(signals=[])
        analyst = AIAnalyst()
        report = analyst.analyze(data)
        d = report.to_dict()

        for risk in d["risks"]:
            assert risk["source_signals"] == [] or "nenhum risco" in risk["risk"].lower()

    def test_competition_drop_creates_opportunity(self):
        now = datetime.now(timezone.utc)
        signals = [
            SignalData(
                signal_type="competition_drop",
                label="Queda de concorrência",
                confidence=0.8,
                weight=0.7,
                value=35.0,
                explanation="Concorrência caiu 35%.",
                extra_data=None,
                computed_at=now,
            ),
        ]
        data = _make_input(signals=signals)
        analyst = AIAnalyst()
        report = analyst.analyze(data)

        has_competition_opp = any(
            "concorrência" in o.opportunity.lower() or "competition_drop" in " ".join(o.source_signals)
            for o in report.opportunities
        )
        assert has_competition_opp

    def test_low_score_creates_risk(self):
        factors = [
            FactorData(name=n, label=n, weight=0.08, raw_score=0.1, contribution=0.008)
            for n in ["demand", "margin", "competition", "growth", "reviews",
                       "competitor_entry", "price", "volatility",
                       "seasonality", "shipping", "weight", "volume"]
        ]
        data = _make_input(score=0.15, factors=factors)
        analyst = AIAnalyst()
        report = analyst.analyze(data)

        has_low_score_risk = any(
            r.severity == "alto" and "baixa" in r.risk.lower()
            for r in report.risks
        )
        assert has_low_score_risk
