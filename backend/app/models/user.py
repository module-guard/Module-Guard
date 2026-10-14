from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.common import utcnow


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # Null only for system accounts: there is no GitHub identity that can reach them.
    github_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    login: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(2048))
    # Owned by the service (mirrors). Never gets a session, and its login is reserved.
    is_system: Mapped[bool] = mapped_column(default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
