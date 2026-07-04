"""
opportunity/score.py

OpportunityScorer — orquestrador que combina múltiplos fatores em um score 0-100.

Algoritmo:
  1. Cada fator é computado via função pura em factors.py, retornando 0.0-1.0
  2. Cada fator é multiplicado pelo seu peso configurável
  3. O score final é a soma ponderada, normalizada pela soma dos pesos, × 100
  4. O resultado inclui o score total + detalhamento por fator

Fórmula completa:

  score_total = Σ(factor_i × weight_i) / Σ(weight_i) × 100

  Onde:
    factor_i ∈ [0.0, 1.0]  (score normalizado do fator i)
    weight_i ∈ [0.0, 1.0]  (peso configurável do fator i)
    score_total ∈ [0, 100]  (score final arredondado para inteiro)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from opportunity.config import OpportunityConfig
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


@dataclass
class FactorDetail:
    """Detalhamento de um fator individual."""

    name: str
    label: str
    weight: float
    raw_score: float
    contribution: float  # raw_score * weight (antes da normalização)


@dataclass
class OpportunityScore:
    """Resultado completo do Opportunity Score."""

    total_score: int  # 0-100, arredondado
    raw_score: float  # 0.0-1.0, sem arredondamento
    factors: list[FactorDetail] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_score": self.total_score,
            "raw_score": round(self.raw_score, 4),
            "factors": [
                {
                    "name": f.name,
                    "label": f.label,
                    "weight": f.weight,
                    "raw_score": round(f.raw_score, 4),
                    "contribution": round(f.contribution, 4),
                }
                for f in self.factors
            ],
        }


FACTOR_LABELS: dict[str, str] = {
    "demand": "Demanda",
    "margin": "Margem Estimada",
    "competition": "Número de Vendedores",
    "growth": "Histórico de Crescimento",
    "reviews": "Quantidade de Avaliações",
    "competitor_entry": "Velocidade de Entrada de Concorrentes",
    "price": "Preço",
    "volatility": "Volatilidade",
    "seasonality": "Sazonalidade",
    "shipping": "Frete",
    "weight": "Peso",
    "volume": "Volume",
}


class OpportunityScorer:
    """Calcula o Opportunity Score para um produto/mercado.

    Completamente desacoplado do banco de dados — recebe dados
    como argumentos numéricos e retorna um OpportunityScore.

    Uso:
        scorer = OpportunityScorer()
        result = scorer.compute(
            price=89.90,
            review_count=350,
            seller_count=12,
            coefficient_of_variation=0.03,
            ...
        )
        print(result.total_score)  # 0-100
    """

    def __init__(self, config: Optional[OpportunityConfig] = None) -> None:
        self.config = config or OpportunityConfig()

    def compute(
        self,
        # Demanda
        review_count: int = 0,
        sold_quantity: Optional[int] = None,
        price_events_count: int = 0,
        # Margem
        price: float = 0.0,
        estimated_cost: Optional[float] = None,
        category_avg_price: Optional[float] = None,
        # Competição
        seller_count: int = 0,
        optimal_seller_count: Optional[int] = None,
        # Crescimento
        price_slope: float = 0.0,
        r_squared: float = 0.0,
        mean_price: float = 1.0,
        # Concorrentes
        new_sellers_per_month: float = 0.0,
        # Preço
        optimal_price: float = 100.0,
        # Volatilidade
        coefficient_of_variation: float = 0.0,
        # Sazonalidade
        seasonality_index: float = 0.0,
        # Frete
        shipping_cost: float = 0.0,
        # Peso
        weight_kg: float = 0.0,
        # Volume
        volume_cm3: float = 0.0,
    ) -> OpportunityScore:
        """Computa o Opportunity Score com base nos dados fornecidos.

        Args:
            review_count: Número de avaliações do produto.
            sold_quantity: Quantidade vendida (opcional).
            price_events_count: Número de eventos de preço no período.
            price: Preço atual do produto.
            estimated_cost: Custo estimado do produto (opcional).
            category_avg_price: Preço médio na categoria (opcional).
            seller_count: Número de vendedores do produto.
            optimal_seller_count: Número ótimo de vendedores (opcional).
            price_slope: Inclinação da regressão linear de preços.
            r_squared: R² da regressão linear de preços.
            mean_price: Preço médio no período.
            new_sellers_per_month: Novos vendedores por mês.
            optimal_price: Preço considerado ótimo para o mercado.
            coefficient_of_variation: CV dos preços.
            seasonality_index: Índice de sazonalidade (0-1).
            shipping_cost: Custo de frete.
            weight_kg: Peso em kg.
            volume_cm3: Volume em cm³.

        Returns:
            OpportunityScore com score total e detalhamento.
        """
        # Computa cada fator
        raw_factors: dict[str, float] = {
            "demand": compute_demand_score(
                review_count=review_count,
                sold_quantity=sold_quantity,
                price_events_count=price_events_count,
            ),
            "margin": compute_margin_score(
                price=price,
                estimated_cost=estimated_cost,
                category_avg_price=category_avg_price,
            ),
            "competition": compute_competition_score(
                seller_count=seller_count,
                optimal_count=optimal_seller_count,
            ),
            "growth": compute_growth_score(
                price_slope=price_slope,
                r_squared=r_squared,
                mean_price=mean_price if mean_price > 0 else price,
            ),
            "reviews": compute_reviews_score(
                review_count=review_count,
            ),
            "competitor_entry": compute_competitor_entry_score(
                new_sellers_per_month=new_sellers_per_month,
            ),
            "price": compute_price_score(
                price=price,
                optimal_price=optimal_price,
            ),
            "volatility": compute_volatility_score(
                coefficient_of_variation=coefficient_of_variation,
            ),
            "seasonality": compute_seasonality_score(
                seasonality_index=seasonality_index,
            ),
            "shipping": compute_shipping_score(
                shipping_cost=shipping_cost,
                price=price,
            ),
            "weight": compute_weight_score(
                weight_kg=weight_kg,
            ),
            "volume": compute_volume_score(
                volume_cm3=volume_cm3,
            ),
        }

        # Calcula score ponderado
        weights = {
            "demand": self.config.demand_weight,
            "margin": self.config.margin_weight,
            "competition": self.config.competition_weight,
            "growth": self.config.growth_weight,
            "reviews": self.config.reviews_weight,
            "competitor_entry": self.config.competitor_entry_weight,
            "price": self.config.price_weight,
            "volatility": self.config.volatility_weight,
            "seasonality": self.config.seasonality_weight,
            "shipping": self.config.shipping_weight,
            "weight": self.config.weight_weight,
            "volume": self.config.volume_weight,
        }

        total_weight = self.config.total_weight()
        if total_weight <= 0:
            return OpportunityScore(total_score=0, raw_score=0.0)

        weighted_sum = 0.0
        details: list[FactorDetail] = []

        for name in FACTOR_LABELS:
            factor_score = raw_factors[name]
            weight = weights[name]
            contribution = factor_score * weight
            weighted_sum += contribution

            details.append(FactorDetail(
                name=name,
                label=FACTOR_LABELS[name],
                weight=weight,
                raw_score=factor_score,
                contribution=contribution,
            ))

        raw_score = weighted_sum / total_weight
        total_score = round(raw_score * 100)

        return OpportunityScore(
            total_score=total_score,
            raw_score=raw_score,
            factors=details,
        )
