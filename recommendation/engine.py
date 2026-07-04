"""
recommendation/engine.py

Recommendation Engine — gera rankings e recomendações priorizadas.

Fontes de dados:
  - Sinais da Intelligence Engine
  - Opportunity Scores
  - Eventos de preço/estoque
  - Estado de categorias

Cada recomendação tem:
  - Tipo (ex: "top_opportunity", "growing_product")
  - Score de prioridade (0-100)
  - Título legível
  - Motivo baseado em dados
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.recommendation import Recommendation
from models.signal import Signal
from models.product import Product
from models.events import PriceEvent
from utils.logger import logger


# ─────────────────────────────────────────────────────────────────────────────
# Tipos de recomendação
# ─────────────────────────────────────────────────────────────────────────────

RECOMMENDATION_TYPES = {
    "top_opportunity": "Oportunidade de Topo",
    "growing_product": "Produto em Crescimento",
    "abandoned_product": "Produto Abandonado",
    "low_competition": "Baixa Concorrência",
    "hot_category": "Categoria Aquecida",
    "new_promising": "Novo Produto Promissor",
    "price_increase": "Preço Médio Aumentou",
    "competition_drop": "Concorrência Reduziu",
    "top_category": "Top Categoria",
}


class RecommendationEngine:
    """Motor de recomendações priorizadas.

    Uso:
        engine = RecommendationEngine(session)
        recs = await engine.generate_all()
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def generate_all(self, limit: int = 50) -> list[Recommendation]:
        """Gera todas as recomendações e persiste no banco."""
        all_recs: list[Recommendation] = []

        all_recs.extend(await self._top_opportunities())
        all_recs.extend(await self._growing_products())
        all_recs.extend(await self._abandoned_products())
        all_recs.extend(await self._low_competition_products())
        all_recs.extend(await self._hot_categories())
        all_recs.extend(await self._new_promising_products())
        all_recs.extend(await self._price_increase_products())
        all_recs.extend(await self._competition_drop_products())

        # Ordena por score e limita
        all_recs.sort(key=lambda r: -r.priority_score)
        all_recs = all_recs[:limit]

        # Persiste
        for rec in all_recs:
            self._session.add(rec)
        await self._session.commit()

        logger.info(
            "Recomendacoes geradas",
            total=len(all_recs),
        )

        return all_recs

    # ── Top Oportunidades ──────────────────────────────────────────────

    async def _top_opportunities(self) -> list[Recommendation]:
        """Produtos com maior Opportunity Score entre os sinais ativos."""
        from models.opportunity_score import OpportunityScoreResult

        subq = (
            select(
                OpportunityScoreResult.product_id,
                OpportunityScoreResult.total_score,
                OpportunityScoreResult.computed_at,
            )
            .distinct(OpportunityScoreResult.product_id)
            .order_by(
                OpportunityScoreResult.product_id,
                OpportunityScoreResult.computed_at.desc(),
            )
            .subquery()
        )

        q = (
            select(subq)
            .order_by(subq.c.total_score.desc())
            .limit(20)
        )
        result = await self._session.execute(q)
        rows = result.all()

        recs: list[Recommendation] = []
        for product_id, score, computed_at in rows:
            product = await self._session.get(Product, product_id)
            title = product.title if product else product_id
            recs.append(self._make_rec(
                recommendation_type="top_opportunity",
                priority_score=score,
                product_id=product_id,
                title=title,
                reason=f"Opportunity Score {score}/100 — maior pontuação entre produtos analisados.",
                category_id=product.category_id if product else None,
            ))
        return recs

    # ── Produtos em Crescimento ────────────────────────────────────────

    async def _growing_products(self) -> list[Recommendation]:
        """Produtos com sinal de price_growth ou constant_growth ativo."""
        q = (
            select(Signal)
            .where(
                Signal.signal_type.in_(["price_growth", "constant_growth"]),
                Signal.confidence >= 0.6,
            )
            .order_by(desc(Signal.confidence))
            .limit(20)
        )
        result = await self._session.execute(q)
        signals = result.scalars().all()

        recs = []
        for s in signals:
            product = await self._session.get(Product, s.product_id) if s.product_id else None
            recs.append(self._make_rec(
                recommendation_type="growing_product",
                priority_score=round(s.confidence * 100),
                product_id=s.product_id,
                title=product.title if product else (s.product_id or ""),
                reason=s.explanation,
                category_id=s.category_id or (product.category_id if product else None),
            ))
        return recs

    # ── Produtos Abandonados ───────────────────────────────────────────

    async def _abandoned_products(self) -> list[Recommendation]:
        q = (
            select(Signal)
            .where(Signal.signal_type == "product_abandoned", Signal.confidence >= 0.5)
            .order_by(desc(Signal.confidence))
            .limit(20)
        )
        result = await self._session.execute(q)
        signals = result.scalars().all()

        recs = []
        for s in signals:
            product = await self._session.get(Product, s.product_id) if s.product_id else None
            recs.append(self._make_rec(
                recommendation_type="abandoned_product",
                priority_score=round(s.confidence * 80),  # cap em 80
                product_id=s.product_id,
                title=product.title if product else (s.product_id or ""),
                reason=s.explanation,
                category_id=s.category_id or (product.category_id if product else None),
            ))
        return recs

    # ── Baixa Concorrência ─────────────────────────────────────────────

    async def _low_competition_products(self) -> list[Recommendation]:
        """Produtos com poucos sellers (via Opportunity Score details)."""
        from models.opportunity_score import OpportunityScoreResult

        subq = (
            select(
                OpportunityScoreResult.product_id,
                OpportunityScoreResult.total_score,
                OpportunityScoreResult.factors,
                OpportunityScoreResult.computed_at,
            )
            .distinct(OpportunityScoreResult.product_id)
            .order_by(
                OpportunityScoreResult.product_id,
                OpportunityScoreResult.computed_at.desc(),
            )
            .subquery()
        )

        q = (
            select(subq)
            .order_by(subq.c.total_score.desc())
            .limit(50)
        )
        result = await self._session.execute(q)
        rows = result.all()

        recs = []
        for product_id, score, factors, _ in rows:
            for f in (factors or []):
                if isinstance(f, dict) and f.get("name") == "competition" and f.get("raw_score", 0) >= 0.7:
                    product = await self._session.get(Product, product_id)
                    recs.append(self._make_rec(
                        recommendation_type="low_competition",
                        priority_score=round(f["raw_score"] * 100),
                        product_id=product_id,
                        title=product.title if product else product_id,
                        reason=f"Baixa concorrência: fator competição em {f['raw_score']:.0%}.",
                        category_id=product.category_id if product else None,
                    ))
                    break
        return recs

    # ── Categorias Aquecidas ───────────────────────────────────────────

    async def _hot_categories(self) -> list[Recommendation]:
        """Categorias com maior volume de eventos recentes."""
        from models.events import PriceEvent

        thirty_days_ago = datetime.now(timezone.utc).replace() - __import__("datetime").timedelta(days=30)

        q = (
            select(
                Product.category_id,
                func.count(PriceEvent.id).label("event_count"),
            )
            .join(PriceEvent, Product.id == PriceEvent.product_id)
            .where(
                Product.category_id.isnot(None),
                PriceEvent.occurred_at >= thirty_days_ago,
            )
            .group_by(Product.category_id)
            .order_by(desc("event_count"))
            .limit(20)
        )
        result = await self._session.execute(q)
        rows = result.all()

        recs = []
        for category_id, event_count in rows:
            from models.category import Category
            cat = await self._session.get(Category, category_id)
            recs.append(self._make_rec(
                recommendation_type="hot_category",
                priority_score=min(event_count, 100),
                category_id=category_id,
                title=cat.name if cat else category_id,
                reason=f"{event_count} eventos de preço nos últimos 30 dias — categoria aquecida.",
            ))
        return recs

    # ── Novos Produtos Promissores ─────────────────────────────────────

    async def _new_promising_products(self) -> list[Recommendation]:
        """Produtos criados recentemente com sinais positivos."""
        thirty_days_ago = datetime.now(timezone.utc).replace() - __import__("datetime").timedelta(days=30)

        q = (
            select(Product)
            .where(Product.created_at >= thirty_days_ago)
            .limit(20)
        )
        result = await self._session.execute(q)
        products = result.scalars().all()

        recs = []
        for product in products:
            has_signal_q = (
                select(Signal)
                .where(
                    Signal.product_id == product.id,
                    Signal.signal_type.in_(["price_growth", "constant_growth"]),
                    Signal.confidence >= 0.5,
                )
                .limit(1)
            )
            has_signal = (await self._session.execute(has_signal_q)).scalar_one_or_none()

            if has_signal:
                recs.append(self._make_rec(
                    recommendation_type="new_promising",
                    priority_score=round(has_signal.confidence * 100),
                    product_id=product.id,
                    title=product.title,
                    reason=f"Produto novo ({product.created_at.date()}) com sinal positivo: {has_signal.explanation[:200]}",
                    category_id=product.category_id,
                ))
        return recs

    # ── Preço Médio Aumentou ───────────────────────────────────────────

    async def _price_increase_products(self) -> list[Recommendation]:
        q = (
            select(Signal)
            .where(Signal.signal_type == "price_growth", Signal.confidence >= 0.6)
            .order_by(desc(Signal.value))
            .limit(20)
        )
        result = await self._session.execute(q)
        signals = result.scalars().all()

        recs = []
        for s in signals:
            product = await self._session.get(Product, s.product_id) if s.product_id else None
            recs.append(self._make_rec(
                recommendation_type="price_increase",
                priority_score=round(s.confidence * 100),
                product_id=s.product_id,
                title=product.title if product else (s.product_id or ""),
                reason=s.explanation,
                category_id=s.category_id or (product.category_id if product else None),
            ))
        return recs

    # ── Concorrência Reduziu ──────────────────────────────────────────

    async def _competition_drop_products(self) -> list[Recommendation]:
        q = (
            select(Signal)
            .where(Signal.signal_type == "competition_drop", Signal.confidence >= 0.5)
            .order_by(desc(Signal.confidence))
            .limit(20)
        )
        result = await self._session.execute(q)
        signals = result.scalars().all()

        recs = []
        for s in signals:
            recs.append(self._make_rec(
                recommendation_type="competition_drop",
                priority_score=round(s.confidence * 100),
                product_id=s.product_id,
                category_id=s.category_id,
                title=f"Categoria {s.category_id}",
                reason=s.explanation,
            ))
        return recs

    # ── Helper ─────────────────────────────────────────────────────────

    def _make_rec(
        self,
        recommendation_type: str,
        priority_score: int,
        title: str,
        reason: str,
        product_id: Optional[str] = None,
        category_id: Optional[str] = None,
    ) -> Recommendation:
        raw_hash = f"{recommendation_type}:{product_id or ''}:{category_id or ''}:{datetime.now(timezone.utc).isoformat()}"
        h = hashlib.sha256(raw_hash.encode()).hexdigest()

        return Recommendation(
            recommendation_type=recommendation_type,
            product_id=product_id,
            category_id=category_id,
            priority_score=min(priority_score, 100),
            title=title[:500],
            reason=reason,
            source="recommendation_engine",
            hash=h,
        )
