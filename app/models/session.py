import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, BigInteger, Integer, Enum, DateTime
from app.database import Base


class UploadStatus(str, enum.Enum):
    INITIATED = "INITIATED"
    UPLOADING = "UPLOADING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    EXPIRED = "EXPIRED"


class UploadSession(Base):
    __tablename__ = "upload_sessions"

    upload_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )
    user_id = Column(String(100), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    part_size = Column(BigInteger, nullable=False)
    total_parts = Column(Integer, nullable=False)
    s3_upload_id = Column(String(255), nullable=False, unique=True)
    object_key = Column(String(512), nullable=False)
    status = Column(
        Enum(UploadStatus),
        default=UploadStatus.INITIATED,
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<UploadSession(upload_id='{self.upload_id}', status='{self.status}', total_parts={self.total_parts})>"

