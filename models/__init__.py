# models package
# Importar todos os modelos para garantir que o Alembic e o SQLAlchemy
# os registrem na metadata antes de qualquer operação de schema.

from models.base import Base
from models.category import Category
from models.seller import Seller
from models.product import Product
from models.raw_payload import RawPayload
from models.events import (
    ProductEvent,
    PriceEvent,
    SellerEvent,
    RankingEvent,
    ReviewEvent,
    QuestionEvent,
)
from models.operational import CollectionJob, CollectionLog, Insight
from models.ml_account import MLAccount
from models.signal import Signal

__all__ = [
    "Base",
    "Category",
    "Seller",
    "Product",
    "RawPayload",
    "ProductEvent",
    "PriceEvent",
    "SellerEvent",
    "RankingEvent",
    "ReviewEvent",
    "QuestionEvent",
    "CollectionJob",
    "CollectionLog",
    "Insight",
    "MLAccount",
    "Signal",
]
