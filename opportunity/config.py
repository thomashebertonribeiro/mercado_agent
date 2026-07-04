"""
opportunity/config.py

Configuração de pesos para o Opportunity Score.

Cada peso é um float entre 0.0 e 1.0 que controla a importância
relativa do fator no score final.

Todos os pesos são configuráveis via construtor ou via dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Optional


@dataclass
class OpportunityConfig:
    """Pesos configuráveis para cada fator do Opportunity Score.

    A soma dos pesos não precisa ser 1.0 — o algoritmo normaliza
    automaticamente dividindo pela soma total.
    """

    # Fatores de demanda e mercado
    demand_weight: float = 0.15
    margin_weight: float = 0.15
    competition_weight: float = 0.10
    growth_weight: float = 0.10

    # Fatores de validação social
    reviews_weight: float = 0.08
    competitor_entry_weight: float = 0.10

    # Fatores de preço e risco
    price_weight: float = 0.08
    volatility_weight: float = 0.08
    seasonality_weight: float = 0.05

    # Fatores logísticos
    shipping_weight: float = 0.05
    weight_weight: float = 0.03
    volume_weight: float = 0.03

    def total_weight(self) -> float:
        """Soma de todos os pesos para normalização."""
        return sum(
            getattr(self, f.name)
            for f in fields(self)
        )

    def to_dict(self) -> dict[str, float]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: dict[str, float]) -> OpportunityConfig:
        valid_keys = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


# Limiares padrão para computação dos fatores
DEFAULT_THRESHOLDS = {
    "demand_review_target": 500,           # avaliações para demanda máxima
    "demand_sold_target": 5000,            # unidades vendidas para demanda máxima
    "margin_ideal_pct": 0.30,             # margem ideal (30%)
    "competition_saturation": 50,          # vendedores para saturação total
    "growth_slope_max": 0.05,             # inclinação máxima esperada (5%/dia)
    "reviews_target": 200,                # avaliações para score máximo
    "competitor_entry_max": 10,           # novos vendedores/mês para saturação
    "volatility_cv_max": 0.15,            # CV máximo para score 0
    "seasonality_index_max": 0.50,        # índice de sazonalidade máximo
    "shipping_max_ratio": 0.20,           # frete máximo como % do preço
    "weight_max_kg": 30.0,                # peso máximo para score 0
    "volume_max_cm3": 100000.0,           # volume máximo para score 0
}
