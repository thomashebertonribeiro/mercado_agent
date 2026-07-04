"""
ai_analyst/analyst.py

AIAnalyst — orquestrador principal.

Fluxo:
  1. Recebe dados brutos do Intelligence Engine e Opportunity Score
  2. Converte para AnalystInput (schemas)
  3. Executa regras de análise (rules.py)
  4. Monta relatório (report.py)
  5. Retorna AnalystReport

A IA nunca pesquisa. A IA apenas interpreta dados produzidos pelo sistema.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ai_analyst.report import build_report
from ai_analyst.schemas import (
    AnalystInput,
    AnalystReport,
    CategoryInfo,
    FactorData,
    PricePoint,
    ProductInfo,
    SignalData,
)
from intelligence.registry import get_definition, SignalType


class AIAnalyst:
    """Analisador determinístico de produtos.

    Uso:
        analyst = AIAnalyst()
        report = analyst.analyze(input_data)
        print(report.to_dict())
    """

    def analyze(self, data: AnalystInput) -> AnalystReport:
        """Executa a análise completa e retorna o relatório."""
        return build_report(data)


# ─────────────────────────────────────────────────────────────────────────────
# Função de conveniência: recebe dados do sistema e produz relatório
# ─────────────────────────────────────────────────────────────────────────────


async def analyze_product(
    product_id: str,
    product_title: str,
    category_id: Optional[str],
    category_name: Optional[str],
    seller_id: int,
    product_condition: Optional[str],
    listing_type: Optional[str],
    initial_price: Optional[float],
    signals_raw: list[dict],
    opportunity_score: Optional[int],
    opportunity_raw_score: Optional[float],
    factors_raw: list[dict],
    price_history: list[tuple[datetime, float]],
    price_slope: float,
    price_r_squared: float,
    mean_price: float,
    coefficient_of_variation: float,
    review_count: int,
    price_events_count: int,
    seller_count: int,
    days_of_data: int,
) -> AnalystReport:
    """Função de conveniência que constrói o AnalystInput e executa a análise.

    Aceita dados no formato que o sistema produz (dicts, tuplas) e
    converte internamente para os schemas tipados.

    Args:
        signals_raw: list[dict] com keys: signal_type, confidence, weight, value,
                     explanation, extra_data, computed_at
        factors_raw: list[dict] com keys: name, label, weight, raw_score, contribution

    Returns:
        AnalystReport completo.
    """
    # Converte signals
    signals: list[SignalData] = []
    for s in signals_raw:
        try:
            signal_type = SignalType(s["signal_type"])
            definition = get_definition(signal_type)
            label = definition.label
        except (ValueError, KeyError):
            label = s.get("signal_type", "desconhecido")

        signals.append(SignalData(
            signal_type=s["signal_type"],
            label=label,
            confidence=s.get("confidence", 0.0),
            weight=s.get("weight", 0.5),
            value=s.get("value"),
            explanation=s.get("explanation", ""),
            extra_data=s.get("extra_data"),
            computed_at=s.get("computed_at", datetime.now(timezone.utc)),
        ))

    # Converte factors
    factors = [
        FactorData(
            name=f["name"],
            label=f.get("label", f["name"]),
            weight=f.get("weight", 0.0),
            raw_score=f.get("raw_score", 0.0),
            contribution=f.get("contribution", 0.0),
        )
        for f in factors_raw
    ]

    # Converte price history
    prices = [
        PricePoint(occurred_at=dt, price=p)
        for dt, p in price_history
    ]

    # Monta input
    data = AnalystInput(
        product=ProductInfo(
            id=product_id,
            title=product_title,
            category_id=category_id,
            seller_id=seller_id,
            condition=product_condition,
            listing_type=listing_type,
            initial_price=initial_price,
        ),
        category=CategoryInfo(id=category_id, name=category_name) if category_id and category_name else None,
        opportunity_score=opportunity_score,
        opportunity_raw_score=opportunity_raw_score,
        factors=factors,
        signals=signals,
        price_history=prices,
        price_slope=price_slope,
        price_r_squared=price_r_squared,
        mean_price=mean_price,
        coefficient_of_variation=coefficient_of_variation,
        review_count=review_count,
        price_events_count=price_events_count,
        seller_count=seller_count,
        days_of_data=days_of_data,
    )

    analyst = AIAnalyst()
    return analyst.analyze(data)
