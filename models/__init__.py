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
from models.recommendation import Recommendation
from models.opportunity_score import OpportunityScoreResult
from models.analysis_report import AnalysisReport
from models.competitor import Competitor
from models.competitor_history import CompetitorHistory
from models.market_trend import MarketTrend
from models.marketplace_account import MarketplaceAccount
from models.finance import ProductCost, CostHistory, FinancialSnapshot

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
    "Recommendation",
    "OpportunityScoreResult",
    "AnalysisReport",
    "Competitor",
    "CompetitorHistory",
    "MarketTrend",
    "MarketplaceAccount",
    "ProductCost",
    "CostHistory",
    "FinancialSnapshot",
]
