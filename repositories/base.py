from typing import TypeVar, Generic, Type, Optional, Sequence
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models.base import Base

T = TypeVar("T", bound=Base)

class BaseRepository(Generic[T]):
    """Generic repository providing basic CRUD operations for SQLAlchemy models."""
    
    def __init__(self, model: Type[T], session: AsyncSession):
        self.model = model
        self.session = session

    async def get_by_id(self, id: any) -> Optional[T]:
        """Fetches a single entity by its primary key."""
        return await self.session.get(self.model, id)

    async def list_all(self) -> Sequence[T]:
        """Fetches all instances of this model."""
        result = await self.session.execute(select(self.model))
        return result.scalars().all()

    async def add(self, entity: T) -> T:
        """Adds an entity to the session."""
        self.session.add(entity)
        return entity

    async def delete(self, entity: T) -> None:
        """Deletes an entity from the session."""
        await self.session.delete(entity)

    async def commit(self) -> None:
        """Commits the current transaction."""
        await self.session.commit()
        
    async def refresh(self, entity: T) -> None:
        """Refreshes the state of the entity from the database."""
        await self.session.refresh(entity)
