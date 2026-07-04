"""
intelligence/formulas.py

Funcoes matematicas puras para computacao de sinais.

Nenhuma funcao aqui depende de banco, IO ou estado externo.
Todas recebem dados ja carregados e retornam resultados estruturados.

Cada funcao retorna um SignalResult ou None (se dados insuficientes).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


VOLATILITY_CV_THRESHOLD = 0.05       # 5% CV = limite entre baixa/alta volatilidade
ABANDONED_DAYS_THRESHOLD = 30        # dias sem atualizacao para considerar abandonado
STABILIZED_CV_THRESHOLD = 0.03       # 3% CV = considerado estabilizado


@dataclass
class SignalResult:
    """Resultado estruturado de uma computacao de sinal."""

    value: Optional[float] = None
    confidence: float = 0.0
    explanation_fields: dict = field(default_factory=dict)
    extra_data: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Utilitarios matematicos                                                     #
# --------------------------------------------------------------------------- #


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _stdev(values: list[float]) -> float:
    m = _mean(values)
    variance = sum((x - m) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


def _coefficient_of_variation(values: list[float]) -> float:
    """Coeficiente de variacao = desvio padrao / media."""
    m = _mean(values)
    return _stdev(values) / m if m != 0 else 0.0


def _linear_regression(
    xs: list[float], ys: list[float]
) -> tuple[float, float, float]:
    """Retorna (slope, intercept, r_squared) para regressao linear simples."""
    n = len(xs)
    mean_x = _mean(xs)
    mean_y = _mean(ys)

    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)

    slope = num / den if den != 0 else 0.0
    intercept = mean_y - slope * mean_x

    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot != 0 else 0.0

    return slope, intercept, r_squared


def _price_change_pct(
    prices: list[tuple[datetime, float]]
) -> float:
    """Variacao percentual entre primeiro e ultimo preco."""
    if len(prices) < 2:
        return 0.0
    first = prices[0][1]
    last = prices[-1][1]
    if first == 0:
        return 0.0
    return ((last - first) / first) * 100.0


# --------------------------------------------------------------------------- #
# API publica para regressao linear                                           #
# --------------------------------------------------------------------------- #


def compute_linear_regression(
    dates: list[datetime],
    values: list[float],
) -> tuple[float, float]:
    """Regressao linear simples com datas.

    Converte datetimes para timestamps (segundos desde epoch) e
    retorna (slope, r_squared).

    Args:
        dates: lista de datetimes (x).
        values: lista de valores numericos (y).

    Returns:
        (slope, r_squared) — inclinacao por segundo e coeficiente de determinacao.
    """
    if len(dates) < 2 or len(values) < 2:
        return 0.0, 0.0

    xs = [d.timestamp() for d in dates]
    _, _, r_squared = _linear_regression(xs, values)

    # slope por dia (mais legivel que por segundo)
    slope, _, _ = _linear_regression([d.timestamp() for d in dates], values)

    return slope, r_squared


# --------------------------------------------------------------------------- #
# Sinais de Produto                                                           #
# --------------------------------------------------------------------------- #


def compute_price_growth(
    prices: list[tuple[datetime, float]],
    days: int = 30,
) -> Optional[SignalResult]:
    """Sinal: preco crescendo (tendencia positiva significativa).

    Args:
        prices: lista de (occurred_at, price) ordenada por data.
        days: janela de analise em dias.

    Returns:
        SignalResult com change_pct, ou None se dados insuficientes.
    """
    if len(prices) < 2:
        return None

    change_pct = _price_change_pct(prices)
    if change_pct <= 1.0:
        return None

    xs = list(range(len(prices)))
    ys = [p[1] for p in prices]
    slope, _, r_squared = _linear_regression(xs, ys)

    if slope <= 0:
        return None

    first_p = prices[0][1]
    last_p = prices[-1][1]

    confidence = min(abs(change_pct) / 50.0, 1.0) * 0.5 + r_squared * 0.5

    return SignalResult(
        value=change_pct,
        confidence=round(confidence, 4),
        explanation_fields={
            "change_pct": change_pct,
            "days": days,
            "initial_price": first_p,
            "current_price": last_p,
        },
        extra_data={
            "slope": slope,
            "r_squared": r_squared,
            "data_points": len(prices),
        },
    )


def compute_price_drop(
    prices: list[tuple[datetime, float]],
    days: int = 30,
) -> Optional[SignalResult]:
    """Sinal: preco caindo (tendencia negativa significativa)."""
    if len(prices) < 2:
        return None

    change_pct = _price_change_pct(prices)
    if change_pct >= -1.0:
        return None

    xs = list(range(len(prices)))
    ys = [p[1] for p in prices]
    slope, _, r_squared = _linear_regression(xs, ys)

    if slope >= 0:
        return None

    first_p = prices[0][1]
    last_p = prices[-1][1]

    confidence = min(abs(change_pct) / 50.0, 1.0) * 0.5 + r_squared * 0.5

    return SignalResult(
        value=change_pct,
        confidence=round(confidence, 4),
        explanation_fields={
            "change_pct": abs(change_pct),
            "days": days,
            "initial_price": first_p,
            "current_price": last_p,
        },
        extra_data={
            "slope": slope,
            "r_squared": r_squared,
            "data_points": len(prices),
        },
    )


def compute_volatility(
    prices: list[tuple[datetime, float]],
) -> Optional[SignalResult]:
    """Sinal: alta ou baixa volatilidade baseada no CV.

    Returns:
        (high_result, low_result) — um dos dois sera None.
    """
    if len(prices) < 3:
        return None

    values = [p[1] for p in prices]
    cv = _coefficient_of_variation(values)
    max_variation = max(
        abs(values[i] - values[i - 1]) / values[i - 1] * 100
        for i in range(1, len(values))
        if values[i - 1] != 0
    )

    extra = {
        "cv": cv,
        "stdev": _stdev(values),
        "mean": _mean(values),
        "max_variation_pct": max_variation,
        "data_points": len(prices),
    }

    if cv >= VOLATILITY_CV_THRESHOLD:
        confidence = min(cv / 0.15, 1.0)
        return SignalResult(
            value=cv,
            confidence=round(confidence, 4),
            explanation_fields={
                "cv": cv,
                "threshold": VOLATILITY_CV_THRESHOLD,
                "days": (prices[-1][0] - prices[0][0]).days,
                "max_variation": max_variation,
            },
            extra_data=extra,
        )
    else:
        confidence = 1.0 - (cv / VOLATILITY_CV_THRESHOLD)
        return SignalResult(
            value=cv,
            confidence=round(confidence, 4),
            explanation_fields={
                "cv": cv,
                "days": (prices[-1][0] - prices[0][0]).days,
            },
            extra_data=extra,
        )


def compute_product_stabilized(
    prices: list[tuple[datetime, float]],
) -> Optional[SignalResult]:
    """Sinal: produto estabilizado (CV muito baixo)."""
    if len(prices) < 3:
        return None

    values = [p[1] for p in prices]
    cv = _coefficient_of_variation(values)

    if cv > STABILIZED_CV_THRESHOLD:
        return None

    days = (prices[-1][0] - prices[0][0]).days
    confidence = 1.0 - (cv / STABILIZED_CV_THRESHOLD)

    return SignalResult(
        value=cv,
        confidence=round(confidence, 4),
        explanation_fields={
            "cv": cv,
            "threshold": STABILIZED_CV_THRESHOLD,
            "days": days or 1,
        },
        extra_data={
            "stdev": _stdev(values),
            "mean": _mean(values),
            "data_points": len(prices),
        },
    )


def compute_constant_growth(
    prices: list[tuple[datetime, float]],
) -> Optional[SignalResult]:
    """Sinal: crescimento constante (R² alto com tendencia positiva)."""
    if len(prices) < 4:
        return None

    xs = list(range(len(prices)))
    ys = [p[1] for p in prices]
    slope, intercept, r_squared = _linear_regression(xs, ys)

    if slope <= 0 or r_squared < 0.7:
        return None

    days = (prices[-1][0] - prices[0][0]).days or 1
    confidence = r_squared * min(slope / _mean(ys) * 100, 1.0) if _mean(ys) != 0 else 0.0
    confidence = min(confidence, 1.0)

    return SignalResult(
        value=slope,
        confidence=round(confidence, 4),
        explanation_fields={
            "r_squared": r_squared,
            "slope": slope,
            "days": days,
        },
        extra_data={
            "intercept": intercept,
            "data_points": len(prices),
        },
    )


def compute_product_abandoned(
    prices: list[tuple[datetime, float]],
    last_event_at: datetime,
    now: Optional[datetime] = None,
) -> Optional[SignalResult]:
    """Sinal: produto abandonado (sem atualizacoes + queda de preco)."""
    if now is None:
        now = datetime.now(timezone.utc)

    days_since_update = (now - last_event_at).days

    if days_since_update < ABANDONED_DAYS_THRESHOLD:
        return None

    if len(prices) >= 2:
        change_pct = _price_change_pct(prices)
        drop_pct = abs(change_pct) if change_pct < 0 else 0.0
    else:
        drop_pct = 0.0

    confidence = min(days_since_update / 90.0, 1.0) * 0.6 + min(drop_pct / 30.0, 1.0) * 0.4
    confidence = min(confidence, 1.0)

    return SignalResult(
        value=float(days_since_update),
        confidence=round(confidence, 4),
        explanation_fields={
            "days_since_update": days_since_update,
            "drop_pct": drop_pct,
        },
        extra_data={
            "last_event_at": last_event_at.isoformat(),
            "abandoned_threshold_days": ABANDONED_DAYS_THRESHOLD,
        },
    )


def compute_low_review_count(
    review_count: int,
    category_median_reviews: float,
) -> Optional[SignalResult]:
    """Sinal: baixo numero de avaliacoes comparado a mediana da categoria."""
    if review_count >= category_median_reviews:
        return None

    ratio = review_count / category_median_reviews if category_median_reviews > 0 else 0
    confidence = 1.0 - ratio

    return SignalResult(
        value=float(review_count),
        confidence=round(confidence, 4),
        explanation_fields={
            "review_count": review_count,
            "median_reviews": category_median_reviews,
        },
        extra_data={
            "ratio_vs_median": ratio,
        },
    )
