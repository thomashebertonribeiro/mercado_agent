"""
repositories/market_trend.py

Repository para tendencias de mercado.
"""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.market_trend import MarketTrend


class MarketTrendRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        keyword: str,
        trend_type: str = "keyword",
        category_id: str | None = None,
        category_name: str | None = None,
        total_items: int | None = None,
        score: float | None = None,
        metadata_json: str | None = None,
    ) -> MarketTrend:
        now = datetime.now(timezone.utc)
        existing = await self._find_by_keyword_and_type(keyword, trend_type)

        if existing:
            existing.category_id = category_id or existing.category_id
            existing.category_name = category_name or existing.category_name
            existing.total_items = total_items or existing.total_items
            existing.score = score or existing.score
            existing.metadata_json = metadata_json or existing.metadata_json
            existing.collected_at = now
            await self._session.commit()
            return existing

        trend = MarketTrend(
            keyword=keyword,
            trend_type=trend_type,
            category_id=category_id,
            category_name=category_name,
            total_items=total_items,
            score=score,
            metadata_json=metadata_json,
            collected_at=now,
        )
        self._session.add(trend)
        await self._session.commit()
        await self._session.refresh(trend)
        return trend

    async def _find_by_keyword_and_type(self, keyword: str, trend_type: str) -> Optional[MarketTrend]:
        result = await self._session.execute(
            select(MarketTrend).where(
                MarketTrend.keyword == keyword,
                MarketTrend.trend_type == trend_type,
            )
        )
        return result.scalar_one_or_none()

    async def get_latest(self, trend_type: str | None = None, limit: int = 50) -> list[MarketTrend]:
        query = select(MarketTrend).order_by(MarketTrend.collected_at.desc())
        if trend_type:
            query = query.where(MarketTrend.trend_type == trend_type)
        query = query.limit(limit)
        result = await self._session.execute(query)
        return list(result.scalars().all())
