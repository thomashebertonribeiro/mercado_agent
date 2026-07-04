"""
ai_analyst/report.py

Montagem do relatório final a partir dos resultados das regras.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ai_analyst.schemas import (
    ActionItem,
    AnalystInput,
    AnalystReport,
    OpportunityItem,
    RiskItem,
)
from ai_analyst.rules import (
    compute_confidence,
    generate_actions,
    generate_executive_summary,
    generate_recommendation_reasons,
    identify_opportunities,
    identify_risks,
)


def build_report(data: AnalystInput) -> AnalystReport:
    """Executa todas as regras de análise e monta o relatório final.

    Args:
        data: Dados de entrada produzidos pelo sistema.

    Returns:
        AnalystReport com todas as seções preenchidas.
    """
    risks = identify_risks(data)
    opportunities = identify_opportunities(data)
    recommendation_reasons = generate_recommendation_reasons(data)
    actions = generate_actions(data)
    confidence_level, confidence_score, confidence_justification = compute_confidence(data)
    executive_summary = generate_executive_summary(data, risks, opportunities)

    return AnalystReport(
        product_id=data.product.id,
        product_title=data.product.title,
        category_name=data.category.name if data.category else None,
        generated_at=datetime.now(timezone.utc).isoformat(),
        executive_summary=executive_summary,
        risks=risks,
        opportunities=opportunities,
        recommendation_reasons=recommendation_reasons,
        possible_actions=actions,
        confidence_level=confidence_level,
        confidence_score=confidence_score,
        confidence_justification=confidence_justification,
    )
