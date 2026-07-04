"""
intelligence/registry.py

Registro central de tipos de sinal.

Cada sinal possui:
  - nome legivel em portugues
  - peso fixo (importancia relativa)
  - template de explicacao (preenchido com valores computados)
  - funcao de formula associada

Nenhum codigo de computacao aqui — apenas definicoes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class SignalType(str, Enum):
    """Todos os tipos de sinal suportados pela Intelligence Engine."""

    COMPETITION_DROP = "competition_drop"
    PRICE_GROWTH = "price_growth"
    PRICE_DROP = "price_drop"
    NEW_SELLER = "new_seller"
    SELLER_EXIT = "seller_exit"
    PRODUCT_STABILIZED = "product_stabilized"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    HIGH_SALES_CONCENTRATION = "high_sales_concentration"
    HIGH_BRAND_CONCENTRATION = "high_brand_concentration"
    LOW_REVIEW_COUNT = "low_review_count"
    CONSTANT_GROWTH = "constant_growth"
    PRODUCT_ABANDONED = "product_abandoned"


@dataclass
class SignalDefinition:
    """Definicao de um tipo de sinal.

    Attributes:
        signal_type: identificador unico (valor do enum).
        label: nome exibivel em portugues.
        default_weight: peso padrao do sinal (0.0 a 1.0).
        explanation_template: template string com placeholders {..}.
        requires_product: se o sinal precisa de um product_id.
        requires_category: se o sinal precisa de um category_id.
        min_data_points: minimo de pontos de dados para compute.
    """

    signal_type: SignalType
    label: str
    default_weight: float
    explanation_template: str
    requires_product: bool = True
    requires_category: bool = False
    min_data_points: int = 2


# --------------------------------------------------------------------------- #
# Registry: todas as definicoes de sinais                                     #
# --------------------------------------------------------------------------- #

SIGNAL_REGISTRY: dict[SignalType, SignalDefinition] = {
    SignalType.COMPETITION_DROP: SignalDefinition(
        signal_type=SignalType.COMPETITION_DROP,
        label="Queda de concorrência",
        default_weight=0.7,
        explanation_template=(
            "Concorrência na categoria {category_id} caiu {drop_pct:.1f}% "
            "nos últimos {days}d: {before} vendedores → {after} vendedores."
        ),
        requires_product=False,
        requires_category=True,
        min_data_points=2,
    ),
    SignalType.PRICE_GROWTH: SignalDefinition(
        signal_type=SignalType.PRICE_GROWTH,
        label="Crescimento de preço",
        default_weight=0.6,
        explanation_template=(
            "Preço de {product_id} subiu {change_pct:.1f}% nos últimos "
            "{days}d: de {initial_price} para {current_price}."
        ),
        requires_product=True,
        min_data_points=2,
    ),
    SignalType.PRICE_DROP: SignalDefinition(
        signal_type=SignalType.PRICE_DROP,
        label="Queda de preço",
        default_weight=0.6,
        explanation_template=(
            "Preço de {product_id} caiu {change_pct:.1f}% nos últimos "
            "{days}d: de {initial_price} para {current_price}."
        ),
        requires_product=True,
        min_data_points=2,
    ),
    SignalType.NEW_SELLER: SignalDefinition(
        signal_type=SignalType.NEW_SELLER,
        label="Novo vendedor",
        default_weight=0.5,
        explanation_template=(
            "Vendedor {seller_id} entrou na categoria {category_id} "
            "em {first_seen}, possivelmente aumentando a oferta."
        ),
        requires_product=False,
        requires_category=True,
        min_data_points=1,
    ),
    SignalType.SELLER_EXIT: SignalDefinition(
        signal_type=SignalType.SELLER_EXIT,
        label="Saída de vendedor",
        default_weight=0.6,
        explanation_template=(
            "Vendedor {seller_id} parece ter saido da categoria "
            "{category_id} — ultima atividade em {last_seen} "
            "({days_since}d atras)."
        ),
        requires_product=False,
        requires_category=True,
        min_data_points=1,
    ),
    SignalType.PRODUCT_STABILIZED: SignalDefinition(
        signal_type=SignalType.PRODUCT_STABILIZED,
        label="Produto estabilizado",
        default_weight=0.3,
        explanation_template=(
            "Preço de {product_id} estabilizou nos últimos {days}d "
            "(CV={cv:.2%}, abaixo do limiar de {threshold:.1%})."
        ),
        requires_product=True,
        min_data_points=3,
    ),
    SignalType.HIGH_VOLATILITY: SignalDefinition(
        signal_type=SignalType.HIGH_VOLATILITY,
        label="Alta volatilidade",
        default_weight=0.7,
        explanation_template=(
            "Preço de {product_id} apresentou alta volatilidade nos "
            "últimos {days}d (CV={cv:.2%}, acima do limiar de {threshold:.1%}). "
            "Variação máxima de {max_variation:.1f}%."
        ),
        requires_product=True,
        min_data_points=3,
    ),
    SignalType.LOW_VOLATILITY: SignalDefinition(
        signal_type=SignalType.LOW_VOLATILITY,
        label="Baixa volatilidade",
        default_weight=0.3,
        explanation_template=(
            "Preço de {product_id} apresentou baixa volatilidade nos "
            "últimos {days}d (CV={cv:.2%})."
        ),
        requires_product=True,
        min_data_points=3,
    ),
    SignalType.HIGH_SALES_CONCENTRATION: SignalDefinition(
        signal_type=SignalType.HIGH_SALES_CONCENTRATION,
        label="Alta concentração de vendas",
        default_weight=0.6,
        explanation_template=(
            "Concentração de vendas na categoria {category_id} está alta "
            "(HHI={hhi:.0f}). Top {top_n} vendedores respondem por "
            "{top_share:.1f}% das transações."
        ),
        requires_product=False,
        requires_category=True,
        min_data_points=2,
    ),
    SignalType.HIGH_BRAND_CONCENTRATION: SignalDefinition(
        signal_type=SignalType.HIGH_BRAND_CONCENTRATION,
        label="Alta concentração de marcas",
        default_weight=0.5,
        explanation_template=(
            "Concentração de marcas na categoria {category_id} está alta. "
            "{top_n} marcas dominam {top_share:.1f}% dos anúncios."
        ),
        requires_product=False,
        requires_category=True,
        min_data_points=2,
    ),
    SignalType.LOW_REVIEW_COUNT: SignalDefinition(
        signal_type=SignalType.LOW_REVIEW_COUNT,
        label="Baixo número de avaliações",
        default_weight=0.4,
        explanation_template=(
            "{product_id} tem apenas {review_count} avaliações nos "
            "últimos {days}d, abaixo da mediana da categoria "
            "(mediana={median_reviews})."
        ),
        requires_product=True,
        min_data_points=1,
    ),
    SignalType.CONSTANT_GROWTH: SignalDefinition(
        signal_type=SignalType.CONSTANT_GROWTH,
        label="Crescimento constante",
        default_weight=0.6,
        explanation_template=(
            "Preço de {product_id} apresenta tendência de crescimento "
            "consistente: R²={r_squared:.3f}, inclinação de "
            "{slope:.4f} por dia nos últimos {days}d."
        ),
        requires_product=True,
        min_data_points=4,
    ),
    SignalType.PRODUCT_ABANDONED: SignalDefinition(
        signal_type=SignalType.PRODUCT_ABANDONED,
        label="Produto abandonado",
        default_weight=0.8,
        explanation_template=(
            "{product_id} parece abandonado: sem atualizações há "
            "{days_since_update}d, preço caiu {drop_pct:.1f}% desde "
            "a última atualização."
        ),
        requires_product=True,
        min_data_points=2,
    ),
}


def get_definition(signal_type: SignalType) -> SignalDefinition:
    """Retorna a definicao de um tipo de sinal."""
    return SIGNAL_REGISTRY[signal_type]


def list_signals() -> list[SignalDefinition]:
    """Lista todas as definicoes de sinais disponiveis."""
    return list(SIGNAL_REGISTRY.values())
