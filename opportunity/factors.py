"""
opportunity/factors.py

Funções de computação individual de cada fator do Opportunity Score.

Todas são funções PURAS: recebem dados numéricos, retornam score 0.0-1.0.
Nenhuma depende de IO, banco de dados ou estado externo.

Cada função documenta:
  - Fórmula matemática
  - Range de entrada esperado
  - Comportamento em casos extremos
"""

from __future__ import annotations

import math
from typing import Optional

from opportunity.config import DEFAULT_THRESHOLDS


# --------------------------------------------------------------------------- #
# Utilitários                                                                 #
# --------------------------------------------------------------------------- #


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Limita valor ao intervalo [lo, hi]."""
    return max(lo, min(hi, value))


def _sigmoid(x: float, midpoint: float = 0.0, steepness: float = 1.0) -> float:
    """Função sigmoide para curvas suaves de score."""
    return 1.0 / (1.0 + math.exp(-steepness * (x - midpoint)))


# --------------------------------------------------------------------------- #
# 1. Demanda (Demand)                                                         #
# --------------------------------------------------------------------------- #


def compute_demand_score(
    review_count: int = 0,
    sold_quantity: Optional[int] = None,
    price_events_count: int = 0,
    review_target: float = DEFAULT_THRESHOLDS["demand_review_target"],
    sold_target: float = DEFAULT_THRESHOLDS["demand_sold_target"],
) -> float:
    """Score de demanda baseado em avaliações, vendas e atividade de preço.

    Fórmula:
      review_score  = min(review_count / review_target, 1.0)
      sold_score    = min(sold_quantity / sold_target, 1.0)  [se disponível]
      activity_bonus = sigmoid(price_events_count, midpoint=10, steepness=0.3)
      demand_score  = 0.5 * review_score + 0.3 * sold_score + 0.2 * activity_bonus
                      [se sold_quantity for None: 0.7 * review_score + 0.3 * activity_bonus]

    Retorna float em [0.0, 1.0].
    Maior score = maior demanda.
    """
    review_score = min(review_count / review_target, 1.0) if review_target > 0 else 0.0
    activity_bonus = _sigmoid(float(price_events_count), midpoint=10.0, steepness=0.3)

    if sold_quantity is not None and sold_target > 0:
        sold_score = min(sold_quantity / sold_target, 1.0)
        return _clamp(0.5 * review_score + 0.3 * sold_score + 0.2 * activity_bonus)
    else:
        return _clamp(0.7 * review_score + 0.3 * activity_bonus)


# --------------------------------------------------------------------------- #
# 2. Margem Estimada (Estimated Margin)                                       #
# --------------------------------------------------------------------------- #


def compute_margin_score(
    price: float,
    estimated_cost: Optional[float] = None,
    category_avg_price: Optional[float] = None,
    ideal_margin_pct: float = DEFAULT_THRESHOLDS["margin_ideal_pct"],
) -> float:
    """Score de margem estimada.

    Se estimated_cost for fornecido:
      margin_pct = (price - estimated_cost) / price
      Se margin_pct <= 0 → score 0
      Se margin_pct >= ideal_margin_pct → score 1
      Caso contrário → score = margin_pct / ideal_margin_pct

    Se apenas category_avg_price for fornecido (proxy de margem):
      ratio = price / category_avg_price
      Margem maior que a média da categoria sugere margem maior.
      score = sigmoid(ratio - 1.0, midpoint=0, steepness=3)
      score = 1.0 quando price >> avg, 0.5 quando price == avg, 0 quando price << avg

    Se nenhum for fornecido, retorna 0.5 (neutro).

    Retorna float em [0.0, 1.0].
    Maior score = maior margem estimada.
    """
    if price <= 0:
        return 0.0

    if estimated_cost is not None and estimated_cost > 0:
        margin_pct = (price - estimated_cost) / price
        if margin_pct <= 0:
            return 0.0
        return _clamp(margin_pct / ideal_margin_pct)

    if category_avg_price is not None and category_avg_price > 0:
        ratio = price / category_avg_price
        return _clamp(_sigmoid(ratio - 1.0, midpoint=0.0, steepness=3.0))

    return 0.5


# --------------------------------------------------------------------------- #
# 3. Número de Vendedores (Competition)                                       #
# --------------------------------------------------------------------------- #


def compute_competition_score(
    seller_count: int,
    saturation: float = DEFAULT_THRESHOLDS["competition_saturation"],
    optimal_count: Optional[int] = None,
) -> float:
    """Score de competição baseado no número de vendedores.

    Fórmula:
      Se optimal_count for fornecido:
        Usa curva de sino: score = exp(-((seller_count - optimal)^2) / (2 * sigma^2))
        Onde sigma = saturation / 2
        Pico no número ótimo, decai para ambos os lados.
        Poucos vendedores = baixa demanda validada. Muitos = mercado saturado.

      Se optimal_count for None:
        score = 1.0 - min(seller_count / saturation, 1.0)
        Quanto mais vendedores, menor o score (mercado saturado).

    Retorna float em [0.0, 1.0].
    """
    if seller_count <= 0:
        return 0.0

    if optimal_count is not None and saturation > 0:
        sigma = saturation / 2.0
        score = math.exp(-((seller_count - optimal_count) ** 2) / (2.0 * sigma ** 2))
        return _clamp(score)

    return _clamp(1.0 - (seller_count / saturation)) if saturation > 0 else 0.0


# --------------------------------------------------------------------------- #
# 4. Histórico de Crescimento (Growth)                                        #
# --------------------------------------------------------------------------- #


def compute_growth_score(
    price_slope: float = 0.0,
    r_squared: float = 0.0,
    mean_price: float = 1.0,
    slope_max: float = DEFAULT_THRESHOLDS["growth_slope_max"],
) -> float:
    """Score de crescimento baseado na tendência de preço.

    Fórmula:
      normalized_slope = abs(price_slope) / mean_price  [variação % por período]
      direction_score  = 1.0 se price_slope > 0 else 0.3 se price_slope == 0 else 0.0
      slope_score      = min(normalized_slope / slope_max, 1.0)
      confidence       = max(r_squared, 0.3)  # R² baixo reduz confiança
      growth_score     = direction_score * slope_score * confidence

    Preço subindo = oportunidade (direção positiva).
    Preço estável = oportunidade moderada.
    Preço caindo = risco (score baixo).
    R² baixo = tendência não confiável → score reduzido.

    Retorna float em [0.0, 1.0].
    """
    if mean_price <= 0:
        return 0.0

    normalized_slope = abs(price_slope) / mean_price

    if price_slope > 0:
        direction_score = 1.0
    elif price_slope == 0:
        direction_score = 0.3
    else:
        direction_score = 0.0

    slope_score = min(normalized_slope / slope_max, 1.0) if slope_max > 0 else 0.0
    confidence = max(r_squared, 0.3)

    return _clamp(direction_score * slope_score * confidence)


# --------------------------------------------------------------------------- #
# 5. Quantidade de Avaliações (Reviews)                                       #
# --------------------------------------------------------------------------- #


def compute_reviews_score(
    review_count: int,
    review_target: float = DEFAULT_THRESHOLDS["reviews_target"],
) -> float:
    """Score baseado na quantidade de avaliações.

    Fórmula:
      review_score = min(review_count / review_target, 1.0)

    Quanto mais avaliações, mais validado o produto.
    Acima do target, score máximo (1.0).

    Retorna float em [0.0, 1.0].
    """
    if review_target <= 0:
        return 0.0
    return _clamp(review_count / review_target)


# --------------------------------------------------------------------------- #
# 6. Velocidade de Entrada de Concorrentes                                    #
# --------------------------------------------------------------------------- #


def compute_competitor_entry_score(
    new_sellers_per_month: float = 0.0,
    max_entry_rate: float = DEFAULT_THRESHOLDS["competitor_entry_max"],
) -> float:
    """Score de velocidade de entrada de concorrentes.

    Fórmula:
      entry_rate = new_sellers_per_month
      score = 1.0 - min(entry_rate / max_entry_rate, 1.0)

    Poucos concorrentes entrando = mercado mais protegido = maior score.
    Muitos concorrentes entrando = mercado competitivo = menor score.

    Retorna float em [0.0, 1.0].
    """
    if max_entry_rate <= 0:
        return 0.0
    return _clamp(1.0 - (new_sellers_per_month / max_entry_rate))


# --------------------------------------------------------------------------- #
# 7. Preço (Price Score)                                                      #
# --------------------------------------------------------------------------- #


def compute_price_score(
    price: float,
    optimal_price: float = 100.0,
    tolerance_pct: float = 0.50,
) -> float:
    """Score de adequação de preço.

    Fórmula:
      deviation = abs(price - optimal_price) / optimal_price
      score = 1.0 - min(deviation / tolerance_pct, 1.0)

    Preço próximo do ótimo = maior score.
    Preço muito abaixo ou muito acima = menor score.

    Retorna float em [0.0, 1.0].
    """
    if optimal_price <= 0:
        return 0.5  # neutro se não temos referência
    deviation = abs(price - optimal_price) / optimal_price
    return _clamp(1.0 - (deviation / tolerance_pct))


# --------------------------------------------------------------------------- #
# 8. Volatilidade (Volatility)                                                #
# --------------------------------------------------------------------------- #


def compute_volatility_score(
    coefficient_of_variation: float = 0.0,
    cv_max: float = DEFAULT_THRESHOLDS["volatility_cv_max"],
) -> float:
    """Score de volatilidade de preço.

    Fórmula:
      score = 1.0 - min(cv / cv_max, 1.0)

    Baixa volatilidade = previsível = maior score.
    Alta volatilidade = arriscado = menor score.

    Retorna float em [0.0, 1.0].
    """
    if cv_max <= 0:
        return 0.0
    return _clamp(1.0 - (coefficient_of_variation / cv_max))


# --------------------------------------------------------------------------- #
# 9. Sazonalidade (Seasonality)                                               #
# --------------------------------------------------------------------------- #


def compute_seasonality_score(
    seasonality_index: float = 0.0,
    index_max: float = DEFAULT_THRESHOLDS["seasonality_index_max"],
) -> float:
    """Score de sazonalidade.

    Fórmula:
      score = 1.0 - min(seasonality_index / index_max, 1.0)

    Baixa sazonalidade = demanda consistente = maior score.
    Alta sazonalidade = vendas concentradas = menor score.

    seasonality_index: 0.0 = sem sazonalidade, 1.0 = totalmente sazonal.

    Retorna float em [0.0, 1.0].
    """
    if index_max <= 0:
        return 0.0
    return _clamp(1.0 - (seasonality_index / index_max))


# --------------------------------------------------------------------------- #
# 10. Frete (Shipping)                                                        #
# --------------------------------------------------------------------------- #


def compute_shipping_score(
    shipping_cost: float = 0.0,
    price: float = 1.0,
    max_ratio: float = DEFAULT_THRESHOLDS["shipping_max_ratio"],
) -> float:
    """Score de frete.

    Fórmula:
      ratio = shipping_cost / price
      score = 1.0 - min(ratio / max_ratio, 1.0)

    Frete baixo relativo ao preço = maior score.
    Frete alto relativo ao preço = menor score (consome margem).

    Se price <= 0 ou shipping_cost <= 0, retorna 0.5 (neutro).

    Retorna float em [0.0, 1.0].
    """
    if shipping_cost <= 0 or price <= 0:
        return 0.5
    if max_ratio <= 0:
        return 0.0
    ratio = shipping_cost / price
    return _clamp(1.0 - (ratio / max_ratio))


# --------------------------------------------------------------------------- #
# 11. Peso (Weight)                                                           #
# --------------------------------------------------------------------------- #


def compute_weight_score(
    weight_kg: float = 0.0,
    max_weight: float = DEFAULT_THRESHOLDS["weight_max_kg"],
) -> float:
    """Score de peso do produto.

    Fórmula:
      score = 1.0 - min(weight_kg / max_weight, 1.0)

    Produtos leves = mais baratos de transportar = maior score.
    Produtos pesados = mais caros de transportar = menor score.

    Se weight_kg <= 0, retorna 1.0 (peso desconhecido = não penaliza).

    Retorna float em [0.0, 1.0].
    """
    if weight_kg <= 0:
        return 1.0
    if max_weight <= 0:
        return 0.0
    return _clamp(1.0 - (weight_kg / max_weight))


# --------------------------------------------------------------------------- #
# 12. Volume (Volume)                                                         #
# --------------------------------------------------------------------------- #


def compute_volume_score(
    volume_cm3: float = 0.0,
    max_volume: float = DEFAULT_THRESHOLDS["volume_max_cm3"],
) -> float:
    """Score de volume do produto.

    Fórmula:
      score = 1.0 - min(volume_cm3 / max_volume, 1.0)

    Produtos compactos = mais baratos de armazenar/transportar = maior score.
    Produtos volumosos = mais caros = menor score.

    Se volume_cm3 <= 0, retorna 1.0 (volume desconhecido = não penaliza).

    Retorna float em [0.0, 1.0].
    """
    if volume_cm3 <= 0:
        return 1.0
    if max_volume <= 0:
        return 0.0
    return _clamp(1.0 - (volume_cm3 / max_volume))
