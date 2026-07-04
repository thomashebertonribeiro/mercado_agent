"""
intelligence/engine.py

Intelligence Engine — orquestrador que computa sinais matematicos.

Fluxo:
  1. Recebe um product_id (e/ou category_id)
  2. Carrega dados historicos dos repositorios de eventos
  3. Computa cada sinal via formulas.py
  4. Persiste os sinais gerados no banco
  5. Retorna lista de sinais computados
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from intelligence.formulas import (
    SignalResult,
    compute_constant_growth,
    compute_low_review_count,
    compute_price_drop,
    compute_price_growth,
    compute_product_abandoned,
    compute_product_stabilized,
    compute_volatility,
)
from intelligence.registry import SIGNAL_REGISTRY, SignalDefinition, SignalType, get_definition
from models.events import PriceEvent
from models.signal import Signal
from repositories.event import EventRepository
from repositories.signal import SignalRepository
from utils.logger import logger


def _make_hash(signal_type: str, product_id: Optional[str], category_id: Optional[str]) -> str:
    """Gera hash SHA-256 unico para um sinal."""
    raw = f"{signal_type}:{product_id or ''}:{category_id or ''}:{datetime.now(timezone.utc).isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()


class IntelligenceEngine:
    """Orquestrador de computacao de sinais.

    Uso:
        engine = IntelligenceEngine(session)
        signals = await engine.analyze_product("MLB1234567890")
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._event_repo = EventRepository(session)
        self._signal_repo = SignalRepository(session)

    # ------------------------------------------------------------------ #
    # API publica                                                         #
    # ------------------------------------------------------------------ #

    async def analyze_product(
        self,
        product_id: str,
        category_id: Optional[str] = None,
        days: int = 30,
    ) -> list[Signal]:
        """Analisa um produto e gera todos os sinais aplicaveis.

        Returns:
            Lista de sinais persistidos no banco.
        """
        logger.info(
            "Iniciando analise de produto",
            product_id=product_id,
            category_id=category_id,
            days=days,
        )

        # 1. Carrega dados
        prices = await self._load_price_history(product_id, limit=200)

        if not prices:
            logger.info(
                "Sem dados de precos para produto",
                product_id=product_id,
            )
            return []

        # 2. Computa sinais
        signals: list[Signal] = []

        for signal_type in SignalType:
            definition = get_definition(signal_type)
            if definition.requires_product and not prices:
                continue

            try:
                result = await self._compute_signal(
                    signal_type, product_id, category_id, prices, days,
                )
                if result is not None:
                    signals.append(result)
            except Exception as exc:
                logger.warning(
                    "Erro ao computar sinal",
                    signal_type=signal_type.value,
                    product_id=product_id,
                    error=str(exc),
                )

        # 3. Persiste
        for signal in signals:
            self._session.add(signal)

        await self._session.commit()

        logger.info(
            "Analise concluida",
            product_id=product_id,
            signals_generated=len(signals),
        )

        return signals

    async def analyze_category(
        self,
        category_id: str,
        days: int = 30,
    ) -> list[Signal]:
        """Analisa uma categoria e gera sinais agregados.

        Returns:
            Lista de sinais persistidos no banco.
        """
        logger.info(
            "Iniciando analise de categoria",
            category_id=category_id,
            days=days,
        )

        signals: list[Signal] = []
        category_types = {
            SignalType.COMPETITION_DROP,
            SignalType.NEW_SELLER,
            SignalType.SELLER_EXIT,
            SignalType.HIGH_SALES_CONCENTRATION,
            SignalType.HIGH_BRAND_CONCENTRATION,
        }

        for signal_type in category_types:
            definition = get_definition(signal_type)
            if not definition.requires_category:
                continue
            try:
                result = await self._compute_category_signal(
                    signal_type, category_id,
                )
                if result is not None:
                    signals.append(result)
            except Exception as exc:
                logger.warning(
                    "Erro ao computar sinal de categoria",
                    signal_type=signal_type.value,
                    category_id=category_id,
                    error=str(exc),
                )

        for signal in signals:
            self._session.add(signal)

        await self._session.commit()

        logger.info(
            "Analise de categoria concluida",
            category_id=category_id,
            signals_generated=len(signals),
        )

        return signals

    # ------------------------------------------------------------------ #
    # Computacao individual de sinais                                     #
    # ------------------------------------------------------------------ #

    async def _compute_signal(
        self,
        signal_type: SignalType,
        product_id: str,
        category_id: Optional[str],
        prices: list[tuple[datetime, float]],
        days: int,
    ) -> Optional[Signal]:
        definition = get_definition(signal_type)
        now = datetime.now(timezone.utc)

        formula_result: Optional[SignalResult] = None

        if signal_type == SignalType.PRICE_GROWTH:
            formula_result = compute_price_growth(prices, days)
        elif signal_type == SignalType.PRICE_DROP:
            formula_result = compute_price_drop(prices, days)
        elif signal_type == SignalType.HIGH_VOLATILITY:
            formula_result = compute_volatility(prices)
        elif signal_type == SignalType.LOW_VOLATILITY:
            result = compute_volatility(prices)
            if result is not None:
                cv = result.value or 0.0
                from intelligence.formulas import VOLATILITY_CV_THRESHOLD
                if cv < VOLATILITY_CV_THRESHOLD:
                    formula_result = result
                else:
                    return None
        elif signal_type == SignalType.PRODUCT_STABILIZED:
            formula_result = compute_product_stabilized(prices)
        elif signal_type == SignalType.CONSTANT_GROWTH:
            formula_result = compute_constant_growth(prices)
        elif signal_type == SignalType.PRODUCT_ABANDONED:
            last_event = prices[-1][0] if prices else now
            formula_result = compute_product_abandoned(prices, last_event, now)
        elif signal_type == SignalType.LOW_REVIEW_COUNT:
            from models.events import ReviewEvent
            from sqlalchemy import select, func

            review_count_q = (
                select(func.count())
                .select_from(ReviewEvent)
                .where(ReviewEvent.product_id == product_id)
            )
            review_count_result = await self._session.execute(review_count_q)
            review_count = review_count_result.scalar() or 0

            median_q = (
                select(func.avg(ReviewEvent.rating))
                .select_from(ReviewEvent)
                .where(ReviewEvent.product_id == product_id)
            )
            median_result = await self._session.execute(median_q)
            avg_rating = median_result.scalar() or 0.0

            formula_result = compute_low_review_count(review_count, float(avg_rating) * 5 if avg_rating else 10.0)
        else:
            return None

        if formula_result is None:
            return None

        explanation = definition.explanation_template.format(
            product_id=product_id,
            category_id=category_id or "?",
            **formula_result.explanation_fields,
        )

        return Signal(
            signal_type=signal_type.value,
            product_id=product_id,
            category_id=category_id,
            weight=definition.default_weight,
            confidence=formula_result.confidence,
            explanation=explanation,
            value=formula_result.value,
            extra_data=formula_result.extra_data,
            computed_at=now,
            source="intelligence_engine",
            hash=_make_hash(signal_type.value, product_id, category_id),
        )

    async def _compute_category_signal(
        self,
        signal_type: SignalType,
        category_id: str,
    ) -> Optional[Signal]:
        definition = get_definition(signal_type)
        now = datetime.now(timezone.utc)

        data = await self._load_category_data(category_id)

        if signal_type == SignalType.COMPETITION_DROP:
            return await self._compute_competition_drop(definition, category_id, data, now)
        elif signal_type == SignalType.NEW_SELLER:
            return await self._compute_new_seller(definition, category_id, data, now)
        elif signal_type == SignalType.SELLER_EXIT:
            return await self._compute_seller_exit(definition, category_id, data, now)
        elif signal_type == SignalType.HIGH_SALES_CONCENTRATION:
            return await self._compute_sales_concentration(definition, category_id, data, now)
        elif signal_type == SignalType.HIGH_BRAND_CONCENTRATION:
            return await self._compute_brand_concentration(definition, category_id, data, now)

        return None

    # ------------------------------------------------------------------ #
    # Sinais de categoria                                                 #
    # ------------------------------------------------------------------ #

    async def _load_category_data(
        self,
        category_id: str,
    ) -> dict:
        """Carrega dados agregados da categoria.

        Returns:
            dict com:
              - products: list[tuple[product_id, seller_id, title]]
              - seller_last_event: dict[seller_id, datetime] — ultimo PriceEvent por seller
              - seller_event_count: dict[seller_id, int] — total de PriceEvents por seller
              - product_last_event: dict[product_id, datetime]
              - product_seller: dict[product_id, seller_id]
        """
        from sqlalchemy import select, func
        from models.product import Product
        from models.events import PriceEvent

        products_q = (
            select(Product.id, Product.seller_id, Product.title)
            .where(Product.category_id == category_id)
        )
        products_result = await self._session.execute(products_q)
        products = products_result.all()

        seller_last_event: dict[int, datetime] = {}
        seller_event_count: dict[int, int] = {}
        product_last_event: dict[str, datetime] = {}
        product_seller: dict[str, int] = {}

        for pid, sid, title in products:
            product_seller[pid] = sid

        seller_ids = list(set(product_seller.values()))

        for sid in seller_ids:
            last_q = (
                select(PriceEvent.occurred_at)
                .where(PriceEvent.product_id.in_(
                    select(Product.id).where(
                        Product.category_id == category_id,
                        Product.seller_id == sid,
                    )
                ))
                .order_by(PriceEvent.occurred_at.desc())
                .limit(1)
            )
            last_result = await self._session.execute(last_q)
            last_ts = last_result.scalar_one_or_none()
            if last_ts:
                seller_last_event[sid] = last_ts

            count_q = (
                select(func.count())
                .select_from(PriceEvent)
                .where(
                    PriceEvent.product_id.in_(
                        select(Product.id).where(
                            Product.category_id == category_id,
                            Product.seller_id == sid,
                        )
                    )
                )
            )
            count_result = await self._session.execute(count_q)
            count_val = count_result.scalar() or 0
            if count_val > 0:
                seller_event_count[sid] = count_val

        for pid in product_seller:
            last_q = (
                select(PriceEvent.occurred_at)
                .where(PriceEvent.product_id == pid)
                .order_by(PriceEvent.occurred_at.desc())
                .limit(1)
            )
            last_result = await self._session.execute(last_q)
            last_ts = last_result.scalar_one_or_none()
            if last_ts:
                product_last_event[pid] = last_ts

        return {
            "products": products,
            "seller_last_event": seller_last_event,
            "seller_event_count": seller_event_count,
            "product_last_event": product_last_event,
            "product_seller": product_seller,
        }

    async def _compute_competition_drop(
        self,
        definition: SignalDefinition,
        category_id: str,
        data: dict,
        now: datetime,
    ) -> Optional[Signal]:
        sellers_with_data = list(data["seller_event_count"].keys())
        num_sellers = len(sellers_with_data)

        seller_last_event = data["seller_last_event"]
        half_window = (now - min(seller_last_event.values())) / 2 if seller_last_event else now
        midpoint = min(seller_last_event.values()) + half_window if seller_last_event else now

        early_count = 0
        late_count = 0
        for sid, last_ts in seller_last_event.items():
            if last_ts < midpoint:
                early_count += 1
            else:
                late_count += 1

        if early_count == 0:
            return None

        drop_pct = ((early_count - late_count) / early_count) * 100 if early_count > 0 else 0.0

        if drop_pct <= 5.0:
            return None

        confidence = min(drop_pct / 50.0, 1.0)
        if num_sellers > 0:
            confidence = min(confidence, 1.0)

        return Signal(
            signal_type=definition.signal_type.value,
            product_id=None,
            category_id=category_id,
            weight=definition.default_weight,
            confidence=round(confidence, 4),
            explanation=definition.explanation_template.format(
                category_id=category_id,
                drop_pct=drop_pct,
                days=(now - (min(seller_last_event.values()) if seller_last_event else now)).days,
                before=early_count,
                after=late_count,
            ),
            value=drop_pct,
            extra_data={
                "early_sellers": early_count,
                "late_sellers": late_count,
                "total_sellers": num_sellers,
            },
            computed_at=now,
            source="intelligence_engine",
            hash=_make_hash(definition.signal_type.value, None, category_id),
        )

    async def _compute_new_seller(
        self,
        definition: SignalDefinition,
        category_id: str,
        data: dict,
        now: datetime,
    ) -> Optional[Signal]:
        product_seller = data["product_seller"]
        product_last_event = data["product_last_event"]

        recent_products = [
            pid for pid, last_ts in product_last_event.items()
            if last_ts and (now - last_ts).days <= 14
        ]

        if not recent_products:
            return None

        recent_seller_ids = set()
        for pid in recent_products:
            sid = product_seller.get(pid)
            if sid:
                recent_seller_ids.add(sid)

        seller_last_event = data["seller_last_event"]
        new_sellers = [
            sid for sid in recent_seller_ids
            if sid in seller_last_event and (now - seller_last_event[sid]).days <= 7
        ]

        if not new_sellers:
            return None

        total_sellers = len(set(product_seller.values()))
        confidence = min(len(new_sellers) / max(total_sellers, 1), 1.0)

        from models.seller import Seller
        seller_names = {}
        for sid in new_sellers:
            seller = await self._session.get(Seller, sid)
            if seller:
                seller_names[sid] = seller.nickname

        seller_desc = "; ".join(
            f"{sid} ({seller_names.get(sid, 'desconhecido')})"
            for sid in sorted(new_sellers)[:5]
        )
        if len(new_sellers) > 5:
            seller_desc += f" e mais {len(new_sellers) - 5}"

        first_seen = seller_last_event[min(seller_last_event.keys(), key=lambda s: seller_last_event.get(s, now))]

        return Signal(
            signal_type=definition.signal_type.value,
            product_id=None,
            category_id=category_id,
            weight=definition.default_weight,
            confidence=round(confidence, 4),
            explanation=definition.explanation_template.format(
                category_id=category_id,
                seller_id=seller_desc,
                first_seen=first_seen.isoformat() if first_seen else "?",
            ),
            value=float(len(new_sellers)),
            extra_data={
                "new_sellers_count": len(new_sellers),
                "total_sellers": total_sellers,
                "new_seller_ids": sorted(new_sellers)[:20],
            },
            computed_at=now,
            source="intelligence_engine",
            hash=_make_hash(definition.signal_type.value, None, category_id),
        )

    async def _compute_seller_exit(
        self,
        definition: SignalDefinition,
        category_id: str,
        data: dict,
        now: datetime,
    ) -> Optional[Signal]:
        seller_last_event = data["seller_last_event"]
        product_seller = data["product_seller"]
        total_sellers = len(set(product_seller.values()))

        exited = []
        for sid, last_ts in seller_last_event.items():
            if last_ts and (now - last_ts).days >= 30:
                exited.append(sid)

        if not exited:
            return None

        confidence = min(len(exited) / max(total_sellers, 1) * 2, 1.0)

        from models.seller import Seller
        seller_names = {}
        for sid in exited[:5]:
            seller = await self._session.get(Seller, sid)
            if seller:
                seller_names[sid] = seller.nickname

        seller_desc = "; ".join(
            f"{sid} ({seller_names.get(sid, 'desconhecido')})"
            for sid in sorted(exited)[:5]
        )
        if len(exited) > 5:
            seller_desc += f" e mais {len(exited) - 5}"

        last_seen = seller_last_event[min(exited, key=lambda s: seller_last_event.get(s, now))]

        return Signal(
            signal_type=definition.signal_type.value,
            product_id=None,
            category_id=category_id,
            weight=definition.default_weight,
            confidence=round(confidence, 4),
            explanation=definition.explanation_template.format(
                category_id=category_id,
                seller_id=seller_desc,
                last_seen=last_seen.isoformat() if last_seen else "?",
                days_since=(now - last_seen).days if last_seen else 0,
            ),
            value=float(len(exited)),
            extra_data={
                "exited_sellers_count": len(exited),
                "total_sellers": total_sellers,
                "exited_seller_ids": sorted(exited)[:20],
            },
            computed_at=now,
            source="intelligence_engine",
            hash=_make_hash(definition.signal_type.value, None, category_id),
        )

    async def _compute_sales_concentration(
        self,
        definition: SignalDefinition,
        category_id: str,
        data: dict,
        now: datetime,
    ) -> Optional[Signal]:
        seller_event_count = data["seller_event_count"]
        if not seller_event_count:
            return None

        total_events = sum(seller_event_count.values())
        if total_events == 0:
            return None

        shares = [(count / total_events) * 100 for count in seller_event_count.values()]
        hhi = sum(s ** 2 for s in shares)

        sorted_sellers = sorted(seller_event_count.items(), key=lambda x: -x[1])
        top_n = min(3, len(sorted_sellers))
        top_share = sum(shares[i] for i in range(top_n)) if shares else 0

        confidence = min(hhi / 10000, 1.0) if hhi > 0 else 0.0

        if hhi < 1000:
            return None

        return Signal(
            signal_type=definition.signal_type.value,
            product_id=None,
            category_id=category_id,
            weight=definition.default_weight,
            confidence=round(confidence, 4),
            explanation=definition.explanation_template.format(
                category_id=category_id,
                hhi=hhi,
                top_n=top_n,
                top_share=top_share,
            ),
            value=hhi,
            extra_data={
                "hhi": round(hhi, 2),
                "top_seller_share_pct": [round(s, 2) for s in shares[:top_n]],
                "total_sellers": len(seller_event_count),
                "total_events": total_events,
            },
            computed_at=now,
            source="intelligence_engine",
            hash=_make_hash(definition.signal_type.value, None, category_id),
        )

    async def _compute_brand_concentration(
        self,
        definition: SignalDefinition,
        category_id: str,
        data: dict,
        now: datetime,
    ) -> Optional[Signal]:
        products = data["products"]
        if not products:
            return None

        brand_keywords = [
            "apple", "samsung", "sony", "lg", "philips", "nike", "adidas",
            "puma", "dell", "hp", "lenovo", "acer", "asus", "logitech",
            "jbl", "bose", "whirlpool", "electrolux", "brastemp", "consul",
            "coca-cola", "pepsi", "nestle", "heineken", "brahma", "skol",
        ]

        brand_counts: dict[str, int] = {}
        other_count = 0

        for _, _, title in products:
            if not title:
                other_count += 1
                continue
            title_lower = title.lower()
            found = False
            for kw in brand_keywords:
                if kw in title_lower:
                    brand_counts[kw] = brand_counts.get(kw, 0) + 1
                    found = True
                    break
            if not found:
                other_count += 1

        if other_count > 0:
            brand_counts["outros"] = other_count

        total = sum(brand_counts.values())
        if total == 0:
            return None

        top_n = min(3, len(brand_counts))
        sorted_brands = sorted(brand_counts.items(), key=lambda x: -x[1])
        top_share = sum(count / total * 100 for _, count in sorted_brands[:top_n])

        hhi_brand = sum((count / total * 100) ** 2 for count in brand_counts.values())
        confidence = min(hhi_brand / 5000, 1.0)

        if hhi_brand < 1000:
            return None

        return Signal(
            signal_type=definition.signal_type.value,
            product_id=None,
            category_id=category_id,
            weight=definition.default_weight,
            confidence=round(confidence, 4),
            explanation=definition.explanation_template.format(
                category_id=category_id,
                top_n=top_n,
                top_share=top_share,
            ),
            value=hhi_brand,
            extra_data={
                "brand_hhi": round(hhi_brand, 2),
                "top_brands": dict(sorted_brands[:top_n]),
                "total_products": total,
            },
            computed_at=now,
            source="intelligence_engine",
            hash=_make_hash(definition.signal_type.value, None, category_id),
        )

    # ------------------------------------------------------------------ #
    # Carga de dados                                                       #
    # ------------------------------------------------------------------ #

    async def _load_price_history(
        self,
        product_id: str,
        limit: int = 200,
    ) -> list[tuple[datetime, float]]:
        """Carrega historico de precos como pares (data, preco)."""
        events = await self._event_repo.get_price_history(product_id, limit)
        return [(e.occurred_at, float(e.price)) for e in events]
