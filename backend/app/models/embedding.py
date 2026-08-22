"""Generic vector-search table (pgvector). One row per embedded text chunk, tagged by
the entity it describes — rating commentary, filing excerpts, product notes, WCDS
narrative fields. Keeps semantic search decoupled from any one entity's schema instead
of scattering `vector` columns across tables. embedding dimension defaults to 1536
(OpenAI/Voyage-class embedding size); change at migration time if a different model
is standardized on.
"""

from datetime import date
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import Date, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid

EMBEDDING_DIM = 1536


class DocumentEmbedding(Base, TimestampMixin):
    __tablename__ = "document_embedding"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_uuid)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)  # institution/filing/rating_action/...
    entity_id: Mapped[str] = mapped_column(String(150), nullable=False)
    chunk_index: Mapped[int] = mapped_column(default=0)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    as_of_date: Mapped[Optional[date]] = mapped_column(Date)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
