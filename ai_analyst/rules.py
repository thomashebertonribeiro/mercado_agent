"""
ai_analyst/rules.py

Regras de análise determinísticas — o "cérebro" do AI Analyst.

Cada função recebe partes do AnalystInput e retorna conclusões
justificadas exclusivamente com os dados recebidos.

Nenhuma função consulta API externa, faz web search ou inventa informação.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ai_analyst.schemas import (
    ActionItem,
    AnalystInput,
    FactorData,
    OpportunityItem,
    RiskItem,
    SignalData,
)


# ─────────────────────────────────────────────────────────────────────────────
# Thresholds
# ─────────────────────────────────────────────────────────────────────────────

CV_HIGH_THRESHOLD = 0.05       # 5% = volatilidade alta
CV_LOW_THRESHOLD = 0.03        # 3% = volatilidade baixa
R_SQUARED_HIGH = 0.7           # R² >= 0.7 = tendência forte
R_SQUARED_MEDIUM = 0.4         # R² >= 0.4 = tendência moderada
ABANDONED_DAYS = 30            # dias sem atualização = abandonado
GROWTH_SLOPE_MIN = 0.0001      # slope mínimo por dia para considerar crescimento
DROP_SLOPE_MAX = -0.0001       # slope máximo por dia para considerar queda
OPPORTUNITY_SCORE_HIGH = 70    # score >= 70 = oportunidade alta
OPPORTUNITY_SCORE_LOW = 30     # score <= 30 = risco alto
REVIEW_LOW_THRESHOLD = 50      # < 50 avaliações = poucas avaliações
COMPETITION_LOW = 5            # <= 5 sellers = competição baixa
COMPETITION_HIGH = 20          # >= 20 sellers = competição alta


# ─────────────────────────────────────────────────────────────────────────────
# Identificação de riscos
# ─────────────────────────────────────────────────────────────────────────────


def identify_risks(data: AnalystInput) -> list[RiskItem]:
    """Analisa os dados e retorna riscos identificados."""
    risks: list[RiskItem] = []
    active_signals = {s.signal_type: s for s in data.signals}

    # Risco: produto abandonado
    if "product_abandoned" in active_signals:
        s = active_signals["product_abandoned"]
        risks.append(RiskItem(
            risk="Produto pode estar abandonado",
            severity="alto",
            justification=(
                f"Sinal '{s.label}' detectado com confiança de {s.confidence:.0%}. "
                f"{s.explanation}"
            ),
            source_signals=["product_abandoned"],
        ))

    # Risco: alta volatilidade
    if "high_volatility" in active_signals:
        s = active_signals["high_volatility"]
        risks.append(RiskItem(
            risk="Preço com alta volatilidade",
            severity="alto",
            justification=(
                f"Sinal '{s.label}' com confiança de {s.confidence:.0%}. "
                f"{s.explanation}"
            ),
            source_signals=["high_volatility"],
        ))

    # Risco: volatilidade geral alta (mesmo sem sinal específico)
    if "high_volatility" not in active_signals and data.coefficient_of_variation >= CV_HIGH_THRESHOLD:
        risks.append(RiskItem(
            risk="Preço com volatilidade elevada",
            severity="médio",
            justification=(
                f"Coeficiente de variação dos preços é de {data.coefficient_of_variation:.2%}, "
                f"acima do limiar de {CV_HIGH_THRESHOLD:.0%}. "
                f"Isso indica instabilidade no preço do produto."
            ),
            source_signals=[],
        ))

    # Risco: tendência de queda
    if data.price_slope < DROP_SLOPE_MAX and data.price_r_squared >= R_SQUARED_MEDIUM:
        risks.append(RiskItem(
            risk="Tendência de queda de preço",
            severity="médio",
            justification=(
                f"Inclinação dos preços é de {data.price_slope:.6f} por dia "
                f"(R²={data.price_r_squared:.3f}), indicando tendência de queda "
                f"com moderada a alta confiança estatística."
            ),
            source_signals=["price_drop"] if "price_drop" in active_signals else [],
        ))

    # Risco: oportunidade score baixo
    if data.opportunity_raw_score is not None and data.opportunity_raw_score <= OPPORTUNITY_SCORE_LOW / 100:
        risks.append(RiskItem(
            risk="Pontuação de oportunidade baixa",
            severity="alto",
            justification=(
                f"Opportunity Score de {data.opportunity_score}/100 indica "
                f"que este produto tem baixo potencial de oportunidade no mercado atual. "
                f"Os fatores mais fracos são: "
                + _worst_factors(data.factors, 3)
            ),
            source_signals=[f.name for f in data.factors if f.raw_score < 0.3],
        ))

    # Risco: poucas avaliações
    if data.review_count < REVIEW_LOW_THRESHOLD and data.days_of_data > 7:
        risks.append(RiskItem(
            risk="Poucas avaliações no período",
            severity="baixo",
            justification=(
                f"Apenas {data.review_count} avaliações em {data.days_of_data} dias "
                f"(média de {data.review_count / max(data.days_of_data, 1):.1f} por dia). "
                f"Isso pode indicar baixo engajamento ou volume de vendas."
            ),
            source_signals=["low_review_count"] if "low_review_count" in active_signals else [],
        ))

    # Risco: sellers saindo da categoria
    if "seller_exit" in active_signals:
        s = active_signals["seller_exit"]
        if s.value and s.value >= 2:
            risks.append(RiskItem(
                risk="Saída de vendedores da categoria",
                severity="médio",
                justification=(
                    f"Sinal '{s.label}' detectado: {int(s.value)} vendedor(es) "
                    f"aparentam ter saído da categoria recentemente. "
                    f"{s.explanation}"
                ),
                source_signals=["seller_exit"],
            ))

    # Risco: crescimento constante seguido de estabilização (topo de ciclo)
    if "constant_growth" in active_signals and "product_stabilized" in active_signals:
        s_growth = active_signals["constant_growth"]
        s_stable = active_signals["product_stabilized"]
        risks.append(RiskItem(
            risk="Possível topo de ciclo (crescimento seguido de estabilização)",
            severity="médio",
            justification=(
                f"Produto apresentou crescimento consistente (R²={s_growth.value:.3f}) "
                f"e agora está estabilizado (CV={s_stable.value:.2%}). "
                f"Este padrão pode indicar que o preço atingiu um teto."
            ),
            source_signals=["constant_growth", "product_stabilized"],
        ))

    # Risco: concentração de vendas alta
    if "high_sales_concentration" in active_signals:
        s = active_signals["high_sales_concentration"]
        risks.append(RiskItem(
            risk="Alta concentração de mercado na categoria",
            severity="médio",
            justification=(
                f"Sinal '{s.label}' com HHI={s.value:.0f}. "
                f"{s.explanation}"
            ),
            source_signals=["high_sales_concentration"],
        ))

    if not risks:
        risks.append(RiskItem(
            risk="Nenhum risco significativo identificado",
            severity="baixo",
            justification=(
                f"Com base nos {len(data.signals)} sinais analisados e "
                f"oportunity score de {data.opportunity_score}/100, "
                f"não foram identificados riscos relevantes neste momento."
            ),
            source_signals=[],
        ))

    return risks


# ─────────────────────────────────────────────────────────────────────────────
# Identificação de oportunidades
# ─────────────────────────────────────────────────────────────────────────────


def identify_opportunities(data: AnalystInput) -> list[OpportunityItem]:
    """Analisa os dados e retorna oportunidades identificadas."""
    opportunities: list[OpportunityItem] = []
    active_signals = {s.signal_type: s for s in data.signals}

    # Oportunidade: preço em queda (comprar/entrar)
    if "price_drop" in active_signals:
        s = active_signals["price_drop"]
        opportunities.append(OpportunityItem(
            opportunity="Preço em queda — possível momento de entrada",
            potential="alto",
            justification=(
                f"Sinal '{s.label}' com queda de {abs(s.value or 0):.1f}%. "
                f"{s.explanation}"
            ),
            source_signals=["price_drop"],
        ))

    # Oportunidade: baixa concorrência
    if data.seller_count <= COMPETITION_LOW:
        opportunities.append(OpportunityItem(
            opportunity="Baixa concorrência no produto",
            potential="alto",
            justification=(
                f"Apenas {data.seller_count} vendedor(es) oferecem este produto. "
                f"Competição baixa pode significar maior margem e poder de precificação."
            ),
            source_signals=[],
        ))

    # Oportunidade: volatilidade baixa
    if "low_volatility" in active_signals:
        s = active_signals["low_volatility"]
        opportunities.append(OpportunityItem(
            opportunity="Preço estável — previsibilidade",
            potential="médio",
            justification=(
                f"Sinal '{s.label}' com CV de {data.coefficient_of_variation:.2%}. "
                f"{s.explanation}"
            ),
            source_signals=["low_volatility"],
        ))

    # Oportunidade: demanda (reviews)
    if data.review_count >= REVIEW_LOW_THRESHOLD * 2:
        opportunities.append(OpportunityItem(
            opportunity="Produto com bom volume de avaliações",
            potential="médio",
            justification=(
                f"{data.review_count} avaliações em {data.days_of_data} dias "
                f"indicam que o produto tem volume de vendas relevante "
                f"e engajamento dos compradores."
            ),
            source_signals=[],
        ))

    # Oportunidade: tendência de crescimento
    if "price_growth" in active_signals:
        s = active_signals["price_growth"]
        opportunities.append(OpportunityItem(
            opportunity="Preço em tendência de alta",
            potential="médio",
            justification=(
                f"Sinal '{s.label}' com alta de {s.value:.1f}%. "
                f"{s.explanation}"
            ),
            source_signals=["price_growth"],
        ))

    # Oportunidade: produto estabilizado após queda (fundo de poço)
    if "product_stabilized" in active_signals and "price_drop" in active_signals:
        s_stable = active_signals["product_stabilized"]
        opportunities.append(OpportunityItem(
            opportunity="Produto estabilizou após queda — possível ponto de inflexão",
            potential="alto",
            justification=(
                f"Produto estava em queda e agora estabilizou (CV={data.coefficient_of_variation:.2%}). "
                f"Isso pode indicar que o preço encontrou um piso, "
                f"sendo potencialmente um bom momento de entrada."
            ),
            source_signals=["product_stabilized", "price_drop"],
        ))

    # Oportunidade: oportunity score alto
    if data.opportunity_raw_score is not None and data.opportunity_raw_score >= OPPORTUNITY_SCORE_HIGH / 100:
        opportunities.append(OpportunityItem(
            opportunity="Produto com alto score de oportunidade",
            potential="alto",
            justification=(
                f"Opportunity Score de {data.opportunity_score}/100. "
                "Os fatores mais fortes são: "
                + _best_factors(data.factors, 3)
            ),
            source_signals=[f.name for f in data.factors if f.raw_score >= 0.7],
        ))

    # Oportunidade: queda de concorrência na categoria
    if "competition_drop" in active_signals:
        s = active_signals["competition_drop"]
        opportunities.append(OpportunityItem(
            opportunity="Redução da concorrência na categoria",
            potential="alto",
            justification=(
                f"Sinal '{s.label}' detectado. {s.explanation}"
            ),
            source_signals=["competition_drop"],
        ))

    if not opportunities:
        opportunities.append(OpportunityItem(
            opportunity="Nenhuma oportunidade significativa identificada",
            potential="baixo",
            justification=(
                f"Com base nos {len(data.signals)} sinais disponíveis e "
                f"score de {data.opportunity_score}/100, "
                f"não foram identificadas oportunidades relevantes no momento."
            ),
            source_signals=[],
        ))

    return opportunities


# ─────────────────────────────────────────────────────────────────────────────
# Motivos da recomendação
# ─────────────────────────────────────────────────────────────────────────────


def generate_recommendation_reasons(data: AnalystInput) -> list[str]:
    """Gera os motivos que justificam a recomendação geral."""
    reasons: list[str] = []
    active_signals = {s.signal_type: s for s in data.signals}

    if data.opportunity_raw_score is not None:
        if data.opportunity_raw_score >= OPPORTUNITY_SCORE_HIGH / 100:
            reasons.append(
                f"Opportunity Score de {data.opportunity_score}/100 indica "
                f"alto potencial. Scores acima de {OPPORTUNITY_SCORE_HIGH} "
                f"são considerados favoráveis."
            )
        elif data.opportunity_raw_score <= OPPORTUNITY_SCORE_LOW / 100:
            reasons.append(
                f"Opportunity Score de {data.opportunity_score}/100 está abaixo "
                f"do limiar de {OPPORTUNITY_SCORE_LOW}, indicando baixo potencial."
            )
        else:
            reasons.append(
                f"Opportunity Score de {data.opportunity_score}/100 está na "
                f"faixa intermediária ({OPPORTUNITY_SCORE_LOW}-{OPPORTUNITY_SCORE_HIGH}). "
                f"Recomenda-se análise adicional dos fatores individuais."
            )

    # Análise de tendência
    if data.price_r_squared >= R_SQUARED_HIGH:
        if data.price_slope > GROWTH_SLOPE_MIN:
            reasons.append(
                f"Tendência de alta forte (R²={data.price_r_squared:.3f}, "
                f"slope={data.price_slope:.6f}/dia). "
                f"Preço subindo de forma consistente."
            )
        elif data.price_slope < DROP_SLOPE_MAX:
            reasons.append(
                f"Tendência de queda forte (R²={data.price_r_squared:.3f}, "
                f"slope={data.price_slope:.6f}/dia). "
                f"Preço caindo de forma consistente."
            )
    elif data.price_r_squared >= R_SQUARED_MEDIUM:
        reasons.append(
            f"Tendência moderada (R²={data.price_r_squared:.3f}). "
            f"Há movimento direcional, mas com dispersão significativa."
        )
    else:
        reasons.append(
            f"Sem tendência clara (R²={data.price_r_squared:.3f}). "
            f"Preço oscila sem direção definida."
        )

    # Análise de volatilidade
    cv = data.coefficient_of_variation
    if cv >= CV_HIGH_THRESHOLD:
        reasons.append(
            f"Volatilidade alta (CV={cv:.2%}). "
            f"Risco de oscilações bruscas de preço."
        )
    elif cv <= CV_LOW_THRESHOLD:
        reasons.append(
            f"Volatilidade baixa (CV={cv:.2%}). "
            f"Preço estável e previsível."
        )
    else:
        reasons.append(
            f"Volatilidade moderada (CV={cv:.2%}). "
            f"Oscilações dentro da normalidade."
        )

    # Análise de sinais ativos
    high_conf_signals = [s for s in data.signals if s.confidence >= 0.7]
    if high_conf_signals:
        signal_labels = [f"'{s.label}' ({s.confidence:.0%})" for s in high_conf_signals[:5]]
        reasons.append(
            f"Sinais com alta confiança detectados: {', '.join(signal_labels)}."
        )

    signal_count = len(data.signals)
    if signal_count == 0:
        reasons.append(
            "Nenhum sinal foi gerado para este produto. "
            "Podem faltar dados históricos suficientes para análise."
        )

    return reasons


# ─────────────────────────────────────────────────────────────────────────────
# Ações recomendadas
# ─────────────────────────────────────────────────────────────────────────────


def generate_actions(data: AnalystInput) -> list[ActionItem]:
    """Gera ações recomendadas com base na análise."""
    actions: list[ActionItem] = []
    active_signals = {s.signal_type: s for s in data.signals}

    # Ação: monitorar volatilidade
    if data.coefficient_of_variation >= CV_HIGH_THRESHOLD:
        actions.append(ActionItem(
            action="Aumentar frequência de coleta devido à alta volatilidade",
            priority="alta",
            reasoning=(
                f"CV de {data.coefficient_of_variation:.2%} indica instabilidade. "
                f"Recomenda-se monitoramento mais frequente para capturar "
                f"movimentos de preço em tempo real."
            ),
        ))

    # Ação: verificar abandono
    if "product_abandoned" in active_signals:
        s = active_signals["product_abandoned"]
        actions.append(ActionItem(
            action="Verificar se o produto ainda está ativo no Mercado Livre",
            priority="alta",
            reasoning=(
                f"Sinal de abandono com {s.confidence:.0%} de confiança. "
                f"Produto pode ter sido descontinuado ou o vendedor pode "
                f"ter encerrado o anúncio."
            ),
        ))

    # Ação: oportunidade de entrada
    if "price_drop" in active_signals and "product_stabilized" in active_signals:
        actions.append(ActionItem(
            action="Considerar entrada — preço caiu e estabilizou",
            priority="alta",
            reasoning=(
                "Queda seguida de estabilização sugere que o preço "
                "encontrou um piso. Pode ser um bom momento para comprar "
                "ou reposicionar o produto."
            ),
        ))

    # Ação: preço subindo
    if "price_growth" in active_signals:
        s = active_signals["price_growth"]
        actions.append(ActionItem(
            action="Avaliar reajuste de preço — tendência de alta detectada",
            priority="média",
            reasoning=(
                f"Preço subiu {s.value:.1f}% com confiança de {s.confidence:.0%}. "
                f"Se o produto estiver na curva de alta, pode haver "
                f"espaço para reajuste."
            ),
        ))

    # Ação: baixa competição
    if data.seller_count <= COMPETITION_LOW:
        actions.append(ActionItem(
            action="Aproveitar baixa concorrência para diferenciar o anúncio",
            priority="média",
            reasoning=(
                f"Com apenas {data.seller_count} vendedor(es), "
                f"investir em descrição, fotos e conditional buying pode "
                f"aumentar a taxa de conversão sem guerra de preços."
            ),
        ))

    # Ação: melhorar avaliações
    if data.review_count < REVIEW_LOW_THRESHOLD and data.days_of_data > 7:
        actions.append(ActionItem(
            action="Implementar estratégia para aumentar avaliações",
            priority="baixa",
            reasoning=(
                f"Apenas {data.review_count} avaliações em {data.days_of_data} dias. "
                f"Baixo número de reviews pode impactar negativamente "
                f"a conversão. Considere campanhas de pós-venda."
            ),
        ))

    if not actions:
        actions.append(ActionItem(
            action="Manter monitoramento padrão",
            priority="baixa",
            reasoning=(
                "Nenhum sinal de alerta ou oportunidade urgente identificado. "
                "Continuar com a frequência normal de coleta."
            ),
        ))

    return actions


# ─────────────────────────────────────────────────────────────────────────────
# Nível de confiança
# ─────────────────────────────────────────────────────────────────────────────


def compute_confidence(data: AnalystInput) -> tuple[str, float, str]:
    """Calcula o nível de confiança da análise.

    Returns:
        (level, score, justification)
    """
    score = 0.0
    reasons: list[str] = []

    # Confiança aumenta com a quantidade de dados
    data_points = data.price_events_count
    if data_points >= 100:
        score += 0.25
        reasons.append(f"{data_points} pontos de preço — base histórica robusta")
    elif data_points >= 30:
        score += 0.15
        reasons.append(f"{data_points} pontos de preço — base histórica moderada")
    elif data_points >= 10:
        score += 0.08
        reasons.append(f"{data_points} pontos de preço — base histórica limitada")
    else:
        reasons.append(f"{data_points} pontos de preço — base histórica insuficiente")

    # Confiança aumenta com o período de dados
    if data.days_of_data >= 90:
        score += 0.20
        reasons.append(f"{data.days_of_data} dias de histórico — período longo")
    elif data.days_of_data >= 30:
        score += 0.10
        reasons.append(f"{data.days_of_data} dias de histórico — período adequado")
    else:
        reasons.append(f"{data.days_of_data} dias de histórico — período curto")

    # Confiança aumenta com sinais de alta confiança
    high_conf_signals = [s for s in data.signals if s.confidence >= 0.7]
    if high_conf_signals:
        score += min(len(high_conf_signals) * 0.05, 0.15)
        reasons.append(
            f"{len(high_conf_signals)} sinal(is) com confiança ≥70%"
        )

    # Confiança aumenta com R² alto
    if data.price_r_squared >= R_SQUARED_HIGH:
        score += 0.15
        reasons.append(f"Tendência forte (R²={data.price_r_squared:.3f})")
    elif data.price_r_squared >= R_SQUARED_MEDIUM:
        score += 0.08
        reasons.append(f"Tendência moderada (R²={data.price_r_squared:.3f})")

    # Confiança aumenta se há opportunity score
    if data.opportunity_raw_score is not None:
        score += 0.10
        reasons.append("Opportunity Score disponível")

    # Confiança aumenta com número de sinais
    if len(data.signals) >= 5:
        score += 0.10
        reasons.append(f"{len(data.signals)} sinais gerados — análise abrangente")
    elif len(data.signals) >= 2:
        score += 0.05
        reasons.append(f"{len(data.signals)} sinais gerados")

    score = min(score, 1.0)

    # Determina nível
    if score >= 0.7:
        level = "alto"
    elif score >= 0.4:
        level = "médio"
    else:
        level = "baixo"

    justification = "; ".join(reasons) if reasons else "Dados insuficientes para calcular confiança"

    return level, round(score, 4), justification


# ─────────────────────────────────────────────────────────────────────────────
# Resumo executivo
# ─────────────────────────────────────────────────────────────────────────────


def generate_executive_summary(
    data: AnalystInput,
    risks: list[RiskItem],
    opportunities: list[OpportunityItem],
) -> str:
    """Gera o resumo executivo em 1-3 parágrafos."""
    parts: list[str] = []

    product_desc = f"Produto '{data.product.title[:80]}'"
    cat_desc = f" na categoria '{data.category.name}'" if data.category else ""
    score_desc = f"Opportunity Score: {data.opportunity_score}/100" if data.opportunity_score is not None else "Opportunity Score: não disponível"

    # Parágrafo 1: visão geral
    parts.append(
        f"{product_desc}{cat_desc}. "
        f"{score_desc}. "
        f"Período analisado: {data.days_of_data} dias "
        f"com {data.price_events_count} eventos de preço. "
    )

    # Parágrafo 2: riscos e oportunidades
    high_risks = [r for r in risks if r.severity == "alto"]
    high_opps = [o for o in opportunities if o.potential == "alto"]

    if high_risks:
        risk_summary = "; ".join(r.risk for r in high_risks[:3])
        parts.append(
            f"Riscos identificados: {risk_summary}. "
        )
    else:
        parts.append("Nenhum risco de alta severidade identificado. ")

    if high_opps:
        opp_summary = "; ".join(o.opportunity for o in high_opps[:3])
        parts.append(
            f"Oportunidades identificadas: {opp_summary}. "
        )
    else:
        parts.append("Nenhuma oportunidade de alto potencial identificada. ")

    # Parágrafo 3: tendência e volatilidade
    trend_desc = _describe_trend(data.price_slope, data.price_r_squared)
    vol_desc = _describe_volatility(data.coefficient_of_variation)
    parts.append(
        f"Tendência de preço: {trend_desc}. "
        f"Volatilidade: {vol_desc}. "
    )

    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Utilitários
# ─────────────────────────────────────────────────────────────────────────────


def _worst_factors(factors: list[FactorData], n: int) -> str:
    """Retorna descrição dos N piores fatores."""
    sorted_factors = sorted(factors, key=lambda f: f.raw_score)
    worst = sorted_factors[:n]
    return ", ".join(f"'{f.label}' ({f.raw_score:.2f})" for f in worst)


def _best_factors(factors: list[FactorData], n: int) -> str:
    """Retorna descrição dos N melhores fatores."""
    sorted_factors = sorted(factors, key=lambda f: -f.raw_score)
    best = sorted_factors[:n]
    return ", ".join(f"'{f.label}' ({f.raw_score:.2f})" for f in best)


def _describe_trend(slope: float, r_squared: float) -> str:
    """Descreve a tendência de preço em linguagem natural."""
    if r_squared >= R_SQUARED_HIGH:
        if slope > GROWTH_SLOPE_MIN:
            return f"alta forte e consistente ({slope:.6f}/dia, R²={r_squared:.3f})"
        elif slope < DROP_SLOPE_MAX:
            return f"queda forte e consistente ({abs(slope):.6f}/dia, R²={r_squared:.3f})"
        else:
            return f"estável (R²={r_squared:.3f})"
    elif r_squared >= R_SQUARED_MEDIUM:
        if slope > 0:
            return f"levemente altista ({slope:.6f}/dia, R²={r_squared:.3f})"
        elif slope < 0:
            return f"levemente baixista ({abs(slope):.6f}/dia, R²={r_squared:.3f})"
    return "indefinida (R² baixo ou dados insuficientes)"


def _describe_volatility(cv: float) -> str:
    """Descreve a volatilidade em linguagem natural."""
    if cv >= CV_HIGH_THRESHOLD:
        return f"alta (CV={cv:.2%})"
    elif cv <= CV_LOW_THRESHOLD:
        return f"baixa (CV={cv:.2%})"
    return f"moderada (CV={cv:.2%})"
