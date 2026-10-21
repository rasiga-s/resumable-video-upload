import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, BigInteger, DateTime
from app.database import Base


class User(Base):
    __tablename__ = "users"

    user_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )
    email = Column(String(255), unique=True, nullable=False, index=True)
    api_key = Column(String(128), unique=True, nullable=False, index=True)
    # Default quota: 50 GB per user
    storage_quota_bytes = Column(
        BigInteger,
        nullable=False,
        default=50 * 1024 * 1024 * 1024,
    )
    # Total accumulated bytes of ready videos
    used_storage_bytes = Column(
        BigInteger,
        nullable=False,
        default=0,
    )
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<User(user_id='{self.user_id}', email='{self.email}', used='{self.used_storage_bytes}/{self.storage_quota_bytes}')>"

