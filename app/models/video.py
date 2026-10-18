import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, BigInteger, Enum, DateTime, ForeignKey
from app.database import Base


class VideoStatus(str, enum.Enum):
    READY = "READY"
    PROCESSING = "PROCESSING"
    FAILED = "FAILED"


class Video(Base):
    __tablename__ = "videos"

    video_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )
    user_id = Column(String(100), nullable=False, index=True)
    upload_session_id = Column(
        String(36),
        ForeignKey("upload_sessions.upload_id"),
        nullable=False,
        unique=True,
    )
    title = Column(String(255), nullable=False)
    object_key = Column(String(512), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    status = Column(
        Enum(VideoStatus),
        default=VideoStatus.READY,
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Video(video_id='{self.video_id}', title='{self.title}', status='{self.status}')>"

