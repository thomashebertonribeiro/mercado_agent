"""
ai_analyst/schemas.py

Schemas de entrada e saída do AI Analyst.

Entrada: dados produzidos pelo Intelligence Engine + Opportunity Score.
Saída: relatório executivo com riscos, oportunidades e ações.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Input schemas — o que o AI Analyst recebe para analisar
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PricePoint:
    occurred_at: datetime
    price: float


@dataclass
class FactorData:
    name: str
    label: str
    weight: float
    raw_score: float
    contribution: float


@dataclass
class SignalData:
    signal_type: str
    label: str
    confidence: float
    weight: float
    value: Optional[float]
    explanation: str
    extra_data: Optional[dict]
    computed_at: datetime


@dataclass
class ProductInfo:
    id: str
    title: str
    category_id: Optional[str]
    seller_id: int
    condition: Optional[str]
    listing_type: Optional[str]
    initial_price: Optional[float]


@dataclass
class CategoryInfo:
    id: str
    name: str


@dataclass
class AnalystInput:
    """Dados completos que o AI Analyst recebe para análise.

    Tudo que está aqui foi produzido pelo sistema (Intelligence Engine,
    Opportunity Score, coletores) — nada é buscado externamente.
    """
    product: ProductInfo
    category: Optional[CategoryInfo]
    opportunity_score: Optional[float]
    opportunity_raw_score: Optional[float]
    factors: list[FactorData]
    signals: list[SignalData]
    price_history: list[PricePoint]
    price_slope: float
    price_r_squared: float
    mean_price: float
    coefficient_of_variation: float
    review_count: int
    price_events_count: int
    seller_count: int
    days_of_data: int


# ─────────────────────────────────────────────────────────────────────────────
# Output schemas — o que o AI Analyst produz
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RiskItem:
    """Risco identificado, justificado com dados."""
    risk: str
    severity: str  # "baixo" | "médio" | "alto"
    justification: str
    source_signals: list[str]


@dataclass
class OpportunityItem:
    """Oportunidade identificada, justificada com dados."""
    opportunity: str
    potential: str  # "baixo" | "médio" | "alto"
    justification: str
    source_signals: list[str]


@dataclass
class ActionItem:
    """Ação recomendada."""
    action: str
    priority: str  # "baixa" | "média" | "alta"
    reasoning: str


@dataclass
class AnalystReport:
    """Relatório completo gerado pelo AI Analyst.

    Nenhum campo aqui é inventado — todo texto é derivado
    exclusivamente dos dados em AnalystInput.
    """
    product_id: str
    product_title: str
    category_name: Optional[str]
    generated_at: str

    # Resumo executivo (1-3 parágrafos)
    executive_summary: str

    # Riscos
    risks: list[RiskItem] = field(default_factory=list)

    # Oportunidades
    opportunities: list[OpportunityItem] = field(default_factory=list)

    # Motivos da recomendação
    recommendation_reasons: list[str] = field(default_factory=list)

    # Possíveis ações
    possible_actions: list[ActionItem] = field(default_factory=list)

    # Nível de confiança
    confidence_level: str = "médio"
    confidence_score: float = 0.5
    confidence_justification: str = ""

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "product_title": self.product_title,
            "category_name": self.category_name,
            "generated_at": self.generated_at,
            "executive_summary": self.executive_summary,
            "risks": [
                {
                    "risk": r.risk,
                    "severity": r.severity,
                    "justification": r.justification,
                    "source_signals": r.source_signals,
                }
                for r in self.risks
            ],
            "opportunities": [
                {
                    "opportunity": o.opportunity,
                    "potential": o.potential,
                    "justification": o.justification,
                    "source_signals": o.source_signals,
                }
                for o in self.opportunities
            ],
            "recommendation_reasons": self.recommendation_reasons,
            "possible_actions": [
                {
                    "action": a.action,
                    "priority": a.priority,
                    "reasoning": a.reasoning,
                }
                for a in self.possible_actions
            ],
            "confidence": {
                "level": self.confidence_level,
                "score": self.confidence_score,
                "justification": self.confidence_justification,
            },
        }
