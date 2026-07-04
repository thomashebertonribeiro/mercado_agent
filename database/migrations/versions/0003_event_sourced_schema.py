"""
Mercado Livre Data Lake — Event-Sourced Schema
Migration: 0003_event_sourced_schema

Revision ID: 0003_event_sourced_schema
Revises: 0002_add_ml_accounts
Create Date: 2026-07-02

ESTRATÉGIA DE MIGRAÇÃO
======================
1. Tabelas dimensão (categories, sellers atualizada, products atualizada):
   criadas/alteradas via Alembic op.create_table / op.add_column.

2. Tabelas de eventos (price_events, product_events, etc.):
   criadas com DDL raw (op.execute) porque SQLAlchemy não abstrai
   PARTITION BY RANGE nativamente.
   Cada tabela usa uma SEQUENCE independente para a coluna id
   (não BIGSERIAL, que vincularia ao owner da tabela pai — incompatível
   com particionamento em PG<17; usamos DEFAULT nextval() explicitamente).

3. Partições iniciais:
   Criamos automaticamente as partições do mês atual, próximo mês
   e mês seguinte para cada tabela de eventos.

4. Tabelas operacionais (collection_jobs, collection_logs, insights):
   criadas via Alembic op.create_table normalmente.

DOWNGRADE
=========
Remove todas as tabelas de eventos e dimensão criadas nesta migration.
NÃO remove ml_accounts (criada em 0002).
"""

from typing import Sequence, Union
from datetime import datetime, timezone, timedelta
import calendar

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0003_event_sourced_schema"
down_revision: Union[str, None] = "0002_add_ml_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _month_range(year: int, month: int) -> tuple[str, str]:
    """Retorna (início_mês, início_próximo_mês) como strings ISO para PARTITION."""
    start = f"{year}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1}-01-01"
    else:
        end = f"{year}-{month + 1:02d}-01"
    return start, end


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _create_partitions_for_table(table: str, n_months: int = 3) -> None:
    """
    Cria N meses de partições mensais a partir do mês atual.
    Usa IF NOT EXISTS para ser idempotente (seguro rodar múltiplas vezes).
    """
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month

    for _ in range(n_months):
        start, end = _month_range(year, month)
        partition_name = f"{table}_{year}_{month:02d}"
        op.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {partition_name}
            PARTITION OF {table}
            FOR VALUES FROM ('{start}') TO ('{end}')
            """
        )
        year, month = _next_month(year, month)


# ─────────────────────────────────────────────────────────────────────────────
# UPGRADE
# ─────────────────────────────────────────────────────────────────────────────

def upgrade() -> None:

    # ── 1. CATEGORIES ────────────────────────────────────────────────────────
    op.create_table(
        "categories",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("parent_id", sa.String(50), nullable=True),
        sa.Column("path_from_root", JSONB, nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="api"),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Self-referential FK para hierarquia de categorias
        sa.ForeignKeyConstraint(
            ["parent_id"], ["categories.id"],
            name="fk_categories_parent_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_categories_parent_id", "categories", ["parent_id"])
    op.create_index("ix_categories_hash", "categories", ["hash"])

    # ── 2. SELLERS — adicionar colunas novas ─────────────────────────────────
    # A tabela sellers já existe (migration 0001). Adicionamos os campos novos.
    op.add_column("sellers", sa.Column("site_id", sa.String(10), nullable=True))
    op.add_column("sellers", sa.Column("level_id", sa.String(30), nullable=True))
    op.add_column("sellers", sa.Column("points", sa.Integer, nullable=True))
    op.add_column("sellers", sa.Column("transactions_total", sa.Integer, nullable=True))
    op.add_column(
        "sellers",
        sa.Column("source", sa.String(50), nullable=False, server_default="api"),
    )
    op.add_column("sellers", sa.Column("hash", sa.String(64), nullable=False, server_default=""))

    op.create_check_constraint(
        "ck_sellers_points_positive", "sellers", "points >= 0"
    )
    op.create_check_constraint(
        "ck_sellers_transactions_positive", "sellers", "transactions_total >= 0"
    )
    op.create_index("ix_sellers_site_id", "sellers", ["site_id"])
    op.create_index("ix_sellers_hash", "sellers", ["hash"])
    op.create_index("ix_sellers_nickname", "sellers", ["nickname"])

    # ── 3. PRODUCTS — recriar com novos campos ───────────────────────────────
    # product_metrics_history depende de products — removemos ela primeiro
    # (era a tabela de snapshots que estamos substituindo)
    op.drop_table("product_metrics_history")

    op.add_column(
        "products",
        sa.Column(
            "category_id",
            sa.String(50),
            sa.ForeignKey("categories.id", ondelete="SET NULL", name="fk_products_category_id"),
            nullable=True,
        ),
    )
    op.add_column("products", sa.Column("condition", sa.String(30), nullable=True))
    op.add_column("products", sa.Column("listing_type_id", sa.String(50), nullable=True))
    op.add_column("products", sa.Column("initial_price", sa.Numeric(12, 2), nullable=True))
    op.add_column("products", sa.Column("initial_quantity", sa.Integer, nullable=True))
    op.add_column(
        "products",
        sa.Column("source", sa.String(50), nullable=False, server_default="api"),
    )
    op.add_column("products", sa.Column("hash", sa.String(64), nullable=False, server_default=""))

    op.create_check_constraint(
        "ck_products_condition",
        "products",
        "condition IN ('new','used','refurbished')",
    )
    op.create_check_constraint(
        "ck_products_initial_price_positive",
        "products",
        "initial_price IS NULL OR initial_price > 0",
    )
    op.create_check_constraint(
        "ck_products_initial_qty_positive",
        "products",
        "initial_quantity IS NULL OR initial_quantity >= 0",
    )

    op.create_index("ix_products_category_id", "products", ["category_id"])
    op.create_index("ix_products_hash", "products", ["hash"])
    op.create_index("ix_products_listing_type", "products", ["listing_type_id"])

    # ── 4. SEQUENCES para tabelas de eventos ─────────────────────────────────
    # Usamos sequences independentes em vez de BIGSERIAL para compatibilidade
    # com tabelas particionadas (o owner fica na sequence, não na tabela pai).
    for seq_name in [
        "price_events_id_seq",
        "product_events_id_seq",
        "seller_events_id_seq",
        "ranking_events_id_seq",
        "review_events_id_seq",
        "question_events_id_seq",
    ]:
        op.execute(f"CREATE SEQUENCE IF NOT EXISTS {seq_name} START 1 INCREMENT 1")

    # ── 5. price_events (particionada) ───────────────────────────────────────
    op.execute("""
        CREATE TABLE price_events (
            id            BIGINT      NOT NULL DEFAULT nextval('price_events_id_seq'),
            product_id    VARCHAR(50) NOT NULL,
            price         NUMERIC(12,2) NOT NULL,
            original_price NUMERIC(12,2),
            currency_id   VARCHAR(10) NOT NULL DEFAULT 'BRL',
            available_qty INTEGER     NOT NULL,
            sold_qty      INTEGER,
            occurred_at   TIMESTAMPTZ NOT NULL,
            source        VARCHAR(50) NOT NULL DEFAULT 'api',
            hash          VARCHAR(64) NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_price_events         PRIMARY KEY (id, occurred_at),
            CONSTRAINT fk_price_events_product FOREIGN KEY (product_id)
                REFERENCES products(id) ON DELETE CASCADE,
            CONSTRAINT ck_price_events_price_positive    CHECK (price > 0),
            CONSTRAINT ck_price_events_qty_non_negative  CHECK (available_qty >= 0),
            CONSTRAINT ck_price_events_sold_non_negative CHECK (sold_qty IS NULL OR sold_qty >= 0)
        ) PARTITION BY RANGE (occurred_at)
    """)
    op.execute(
        "CREATE INDEX ix_price_events_product_occurred ON price_events (product_id, occurred_at)"
    )
    op.execute("CREATE INDEX ix_price_events_hash ON price_events (hash)")
    _create_partitions_for_table("price_events")

    # ── 6. product_events (particionada) ─────────────────────────────────────
    op.execute("""
        CREATE TABLE product_events (
            id           BIGINT      NOT NULL DEFAULT nextval('product_events_id_seq'),
            product_id   VARCHAR(50) NOT NULL,
            event_type   VARCHAR(60) NOT NULL,
            old_value    TEXT,
            new_value    TEXT,
            occurred_at  TIMESTAMPTZ NOT NULL,
            source       VARCHAR(50) NOT NULL DEFAULT 'api',
            hash         VARCHAR(64) NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_product_events         PRIMARY KEY (id, occurred_at),
            CONSTRAINT fk_product_events_product FOREIGN KEY (product_id)
                REFERENCES products(id) ON DELETE CASCADE,
            CONSTRAINT ck_product_events_type CHECK (
                event_type IN ('status_change','title_change','listing_type_change','thumbnail_change','other')
            )
        ) PARTITION BY RANGE (occurred_at)
    """)
    op.execute(
        "CREATE INDEX ix_product_events_product_occurred ON product_events (product_id, occurred_at)"
    )
    op.execute("CREATE INDEX ix_product_events_type ON product_events (event_type)")
    op.execute("CREATE INDEX ix_product_events_hash ON product_events (hash)")
    _create_partitions_for_table("product_events")

    # ── 7. seller_events (particionada) ──────────────────────────────────────
    op.execute("""
        CREATE TABLE seller_events (
            id                 BIGINT      NOT NULL DEFAULT nextval('seller_events_id_seq'),
            seller_id          BIGINT      NOT NULL,
            event_type         VARCHAR(50) NOT NULL,
            reputation_level   VARCHAR(30),
            positive_pct       NUMERIC(5,2),
            negative_pct       NUMERIC(5,2),
            neutral_pct        NUMERIC(5,2),
            transactions_total INTEGER,
            points             INTEGER,
            occurred_at        TIMESTAMPTZ NOT NULL,
            source             VARCHAR(50) NOT NULL DEFAULT 'api',
            hash               VARCHAR(64) NOT NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_seller_events         PRIMARY KEY (id, occurred_at),
            CONSTRAINT fk_seller_events_seller  FOREIGN KEY (seller_id)
                REFERENCES sellers(id) ON DELETE CASCADE,
            CONSTRAINT ck_seller_events_type CHECK (
                event_type IN ('reputation_change','level_change','transactions_milestone','other')
            ),
            CONSTRAINT ck_seller_events_positive_pct CHECK (
                positive_pct IS NULL OR (positive_pct >= 0 AND positive_pct <= 100)
            )
        ) PARTITION BY RANGE (occurred_at)
    """)
    op.execute(
        "CREATE INDEX ix_seller_events_seller_occurred ON seller_events (seller_id, occurred_at)"
    )
    op.execute("CREATE INDEX ix_seller_events_hash ON seller_events (hash)")
    _create_partitions_for_table("seller_events")

    # ── 8. ranking_events (particionada) ─────────────────────────────────────
    op.execute("""
        CREATE TABLE ranking_events (
            id           BIGINT       NOT NULL DEFAULT nextval('ranking_events_id_seq'),
            product_id   VARCHAR(50)  NOT NULL,
            search_term  VARCHAR(255) NOT NULL,
            position     INTEGER      NOT NULL,
            page         SMALLINT     NOT NULL DEFAULT 1,
            occurred_at  TIMESTAMPTZ  NOT NULL,
            source       VARCHAR(50)  NOT NULL DEFAULT 'api',
            hash         VARCHAR(64)  NOT NULL,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
            CONSTRAINT pk_ranking_events         PRIMARY KEY (id, occurred_at),
            CONSTRAINT fk_ranking_events_product FOREIGN KEY (product_id)
                REFERENCES products(id) ON DELETE CASCADE,
            CONSTRAINT ck_ranking_events_position_positive CHECK (position > 0),
            CONSTRAINT ck_ranking_events_page_positive     CHECK (page > 0)
        ) PARTITION BY RANGE (occurred_at)
    """)
    op.execute(
        "CREATE INDEX ix_ranking_events_product_term_occurred "
        "ON ranking_events (product_id, search_term, occurred_at)"
    )
    op.execute("CREATE INDEX ix_ranking_events_hash ON ranking_events (hash)")
    _create_partitions_for_table("ranking_events")

    # ── 9. review_events (particionada) ──────────────────────────────────────
    op.execute("""
        CREATE TABLE review_events (
            id            BIGINT      NOT NULL DEFAULT nextval('review_events_id_seq'),
            product_id    VARCHAR(50) NOT NULL,
            review_ml_id  BIGINT,
            rating        SMALLINT    NOT NULL,
            content       TEXT,
            reviewer_id   BIGINT,
            fulfilled     BOOLEAN,
            occurred_at   TIMESTAMPTZ NOT NULL,
            source        VARCHAR(50) NOT NULL DEFAULT 'api',
            hash          VARCHAR(64) NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_review_events         PRIMARY KEY (id, occurred_at),
            CONSTRAINT fk_review_events_product FOREIGN KEY (product_id)
                REFERENCES products(id) ON DELETE CASCADE,
            CONSTRAINT ck_review_events_rating_range CHECK (rating >= 1 AND rating <= 5)
        ) PARTITION BY RANGE (occurred_at)
    """)
    op.execute(
        "CREATE INDEX ix_review_events_product_occurred ON review_events (product_id, occurred_at)"
    )
    op.execute("CREATE INDEX ix_review_events_ml_id ON review_events (review_ml_id)")
    op.execute("CREATE INDEX ix_review_events_hash ON review_events (hash)")
    _create_partitions_for_table("review_events")

    # ── 10. question_events (particionada) ───────────────────────────────────
    op.execute("""
        CREATE TABLE question_events (
            id               BIGINT      NOT NULL DEFAULT nextval('question_events_id_seq'),
            product_id       VARCHAR(50) NOT NULL,
            question_ml_id   BIGINT      NOT NULL,
            question_text    TEXT,
            answer_text      TEXT,
            answered         BOOLEAN     NOT NULL DEFAULT false,
            occurred_at      TIMESTAMPTZ NOT NULL,
            source           VARCHAR(50) NOT NULL DEFAULT 'api',
            hash             VARCHAR(64) NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_question_events         PRIMARY KEY (id, occurred_at),
            CONSTRAINT fk_question_events_product FOREIGN KEY (product_id)
                REFERENCES products(id) ON DELETE CASCADE
        ) PARTITION BY RANGE (occurred_at)
    """)
    op.execute(
        "CREATE INDEX ix_question_events_product_occurred ON question_events (product_id, occurred_at)"
    )
    op.execute("CREATE INDEX ix_question_events_ml_id ON question_events (question_ml_id)")
    op.execute("CREATE INDEX ix_question_events_hash ON question_events (hash)")
    _create_partitions_for_table("question_events")

    # ── 11. collection_jobs ───────────────────────────────────────────────────
    op.create_table(
        "collection_jobs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("job_type", sa.String(60), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("total_items", sa.Integer, nullable=True),
        sa.Column("processed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("events_generated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("errors", sa.Integer, nullable=False, server_default="0"),
        sa.Column("skipped", sa.Integer, nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer, nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="scheduler"),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','done','failed','partial')",
            name="ck_collection_jobs_status",
        ),
        sa.CheckConstraint("processed >= 0", name="ck_collection_jobs_processed"),
        sa.CheckConstraint("errors >= 0", name="ck_collection_jobs_errors"),
    )
    op.create_index("ix_collection_jobs_status", "collection_jobs", ["status"])
    op.create_index("ix_collection_jobs_type", "collection_jobs", ["job_type"])
    op.create_index("ix_collection_jobs_started_at", "collection_jobs", ["started_at"])

    # ── 12. collection_logs ───────────────────────────────────────────────────
    op.create_table(
        "collection_logs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "job_id",
            sa.BigInteger,
            sa.ForeignKey("collection_jobs.id", ondelete="CASCADE", name="fk_collection_logs_job"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("events_generated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="scheduler"),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('ok','skipped','error')", name="ck_collection_logs_status"
        ),
        sa.CheckConstraint(
            "entity_type IN ('product','seller','review','question','ranking')",
            name="ck_collection_logs_entity_type",
        ),
    )
    op.create_index("ix_collection_logs_job_id", "collection_logs", ["job_id"])
    op.create_index("ix_collection_logs_status", "collection_logs", ["status"])
    op.create_index(
        "ix_collection_logs_entity", "collection_logs", ["entity_id", "entity_type"]
    )

    # ── 13. insights ──────────────────────────────────────────────────────────
    op.create_table(
        "insights",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "product_id",
            sa.String(50),
            sa.ForeignKey("products.id", ondelete="CASCADE", name="fk_insights_product"),
            nullable=False,
        ),
        sa.Column("insight_type", sa.String(100), nullable=False),
        sa.Column("value", sa.Numeric(15, 4), nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="computed"),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_insights_product_type_computed",
        "insights",
        ["product_id", "insight_type", "computed_at"],
    )
    op.create_index("ix_insights_valid_until", "insights", ["valid_until"])
    op.create_index("ix_insights_hash", "insights", ["hash"])


# ─────────────────────────────────────────────────────────────────────────────
# DOWNGRADE
# ─────────────────────────────────────────────────────────────────────────────

def downgrade() -> None:
    # Operacionais
    op.drop_table("insights")
    op.drop_table("collection_logs")
    op.drop_table("collection_jobs")

    # Eventos particionados (DROP CASCADE remove as partições filhas automaticamente)
    for table in [
        "question_events", "review_events", "ranking_events",
        "seller_events", "product_events", "price_events",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    # Sequences
    for seq in [
        "price_events_id_seq", "product_events_id_seq", "seller_events_id_seq",
        "ranking_events_id_seq", "review_events_id_seq", "question_events_id_seq",
    ]:
        op.execute(f"DROP SEQUENCE IF EXISTS {seq}")

    # Rollback das colunas em products e sellers
    for col in ["category_id", "condition", "listing_type_id",
                "initial_price", "initial_quantity", "source", "hash"]:
        op.drop_column("products", col)

    for col in ["site_id", "level_id", "points", "transactions_total", "source", "hash"]:
        op.drop_column("sellers", col)

    # Categories
    op.drop_table("categories")

    # Recria product_metrics_history (reversão para schema anterior)
    op.create_table(
        "product_metrics_history",
        sa.Column("id", sa.BigInteger, nullable=False),
        sa.Column("product_id", sa.String(50), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("original_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("available_quantity", sa.Integer, nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("captured_at", sa.DateTime, nullable=False),
    )
