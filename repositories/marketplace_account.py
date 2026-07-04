"""
repositories/marketplace_account.py

Repository para contas de marketplace conectadas.
"""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.marketplace_account import MarketplaceAccount


class MarketplaceAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_marketplace_and_user(
        self, marketplace: str, user_id: int
    ) -> Optional[MarketplaceAccount]:
        result = await self._session.execute(
            select(MarketplaceAccount).where(
                MarketplaceAccount.marketplace == marketplace,
                MarketplaceAccount.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_all(self, marketplace: str | None = None) -> list[MarketplaceAccount]:
        query = select(MarketplaceAccount)
        if marketplace:
            query = query.where(MarketplaceAccount.marketplace == marketplace)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def upsert(
        self,
        marketplace: str,
        user_id: int,
        access_token: str,
        refresh_token: str,
        expires_at: datetime,
        nickname: str | None = None,
        country: str | None = None,
        scope: str | None = None,
    ) -> MarketplaceAccount:
        now = datetime.now(timezone.utc)
        existing = await self.get_by_marketplace_and_user(marketplace, user_id)

        if existing:
            existing.access_token = access_token
            existing.refresh_token = refresh_token
            existing.expires_at = expires_at
            existing.nickname = nickname or existing.nickname
            existing.country = country or existing.country
            existing.scope = scope or existing.scope
            existing.updated_at = now
            await self._session.commit()
            return existing

        account = MarketplaceAccount(
            marketplace=marketplace,
            user_id=user_id,
            nickname=nickname,
            country=country,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scope=scope,
            created_at=now,
            updated_at=now,
        )
        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def delete(self, marketplace: str, user_id: int) -> bool:
        account = await self.get_by_marketplace_and_user(marketplace, user_id)
        if not account:
            return False
        await self._session.delete(account)
        await self._session.commit()
        return True
