from sqlalchemy.ext.asyncio import AsyncSession
from models.raw_payload import RawPayload
from repositories.base import BaseRepository

class RawPayloadRepository(BaseRepository[RawPayload]):
    """Repository handling database access for RawPayload entity."""
    
    def __init__(self, session: AsyncSession):
        super().__init__(RawPayload, session)
