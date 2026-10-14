from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.common import JSON_DATA, utcnow


class GraphEdge(Base):
    __tablename__ = "graph_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scan_id", "source_entity_id"],
            ["parsed_entities.scan_id", "parsed_entities.id"],
            ondelete="CASCADE",
            name="fk_graph_source_scan",
        ),
        ForeignKeyConstraint(
            ["scan_id", "target_entity_id"],
            ["parsed_entities.scan_id", "parsed_entities.id"],
            ondelete="CASCADE",
            name="fk_graph_target_scan",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    scan_id: Mapped[UUID] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"), index=True)
    source_entity_id: Mapped[UUID | None] = mapped_column(index=True)
    target_entity_id: Mapped[UUID | None] = mapped_column(index=True)
    target_address: Mapped[str | None] = mapped_column(Text)
    edge_type: Mapped[str] = mapped_column(String(80), index=True)
    expression: Mapped[str | None] = mapped_column(Text)
    attribute_name: Mapped[str | None] = mapped_column(Text)
    edge_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON_DATA, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
