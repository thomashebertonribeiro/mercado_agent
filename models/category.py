"""
models/category.py

Tabela dimensão de categorias do Mercado Livre.

Cresce lentamente — novas categorias são adicionadas raramente.
O campo `path_from_root` (JSONB) armazena a hierarquia completa
sem necessitar de joins recursivos em tempo de consulta.

hash: SHA-256 de (id + name + parent_id) — detecta mudanças de nome/hierarquia.
"""

from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from models.base import Base


class Category(Base):
    __tablename__ = "categories"

    # ML category ID ex: "MLB1234", "MLA5678"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Hierarquia — null se for categoria raiz
    parent_id: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        # Sem FK circular — categoria raiz não tem pai
    )

    # Caminho completo da raiz até esta categoria
    # Ex: [{"id": "MLB5672", "name": "Eletrônicos"}, {"id": "MLB1234", "name": "Celulares"}]
    path_from_root: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Obrigatórios em todas as tabelas
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="api")
    hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relacionamentos
    products = relationship("Product", back_populates="category", lazy="noload")

    __table_args__ = (
        Index("ix_categories_parent_id", "parent_id"),
        Index("ix_categories_hash", "hash"),
    )

    def __repr__(self) -> str:
        return f"<Category(id='{self.id}', name='{self.name}')>"
