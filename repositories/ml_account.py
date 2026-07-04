from typing import Optional, List
from sqlalchemy import select
from models.ml_account import MLAccount
from repositories.base import BaseRepository

class MLAccountRepository(BaseRepository[MLAccount]):
    """Repository handling database operations for Mercado Livre accounts."""
    
    def __init__(self, session) -> None:
        super().__init__(MLAccount, session)

    async def get_by_user_id(self, user_id: int) -> Optional[MLAccount]:
        """Retrieves credentials for an ML account by its official user ID."""
        query = select(MLAccount).filter(MLAccount.user_id == user_id)
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_all_accounts(self) -> List[MLAccount]:
        """Lists all stored Mercado Livre accounts."""
        query = select(MLAccount).order_by(MLAccount.nickname)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def delete_by_user_id(self, user_id: int) -> bool:
        """Deletes stored credentials for an ML account by its official user ID."""
        account = await self.get_by_user_id(user_id)
        if account:
            await self.session.delete(account)
            await self.session.commit()
            return True
        return False
