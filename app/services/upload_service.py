import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.session import UploadSession, UploadStatus
from app.models.video import Video, VideoStatus
from app.schemas.upload import (
    UploadInitRequest,
    UploadInitResponse,
    PartPresignResponse,
    PartInfo,
    ResumeStatusResponse,
    UploadCompleteResponse,
    UploadAbortResponse,
)
from app.services.s3_service import S3Service

settings = get_settings()


class UploadService:
    """Orchestrates database state and S3 multipart upload operations."""

    def __init__(self, s3_service: S3Service | None = None) -> None:
        self.s3_service = s3_service or S3Service()

    def initiate_upload(self, db: Session, req: UploadInitRequest) -> UploadInitResponse:
        """
        Validates file size, determines part size and total parts,
        initiates S3 multipart upload, and persists the UploadSession in PostgreSQL.
        """
        # Determine part size (minimum 5 MB per AWS S3 constraints)
        part_size = req.part_size or settings.default_part_size_bytes
        if part_size < settings.min_part_size_bytes:
            part_size = settings.min_part_size_bytes

        # AWS S3 limit: maximum 10,000 parts per multipart upload
        total_parts = math.ceil(req.file_size / part_size)
        if total_parts > 10000:
            # Adjust part size up to keep total parts <= 10,000
            part_size = math.ceil(req.file_size / 10000)
            total_parts = math.ceil(req.file_size / part_size)

        upload_id = str(uuid.uuid4())
        # Destination key in S3: videos/{user_id}/{upload_id}/{filename}
        object_key = f"videos/{req.user_id}/{upload_id}/{req.filename}"

        # Step 1: Initiate S3 Multipart Upload
        s3_upload_id = self.s3_service.create_multipart_upload(object_key)

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=settings.presigned_url_expiration_seconds * 24)

        # Step 2: Persist upload session record in database
        session = UploadSession(
            upload_id=upload_id,
            user_id=req.user_id,
            filename=req.filename,
            file_size=req.file_size,
            part_size=part_size,
            total_parts=total_parts,
            s3_upload_id=s3_upload_id,
            object_key=object_key,
            status=UploadStatus.INITIATED,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        return UploadInitResponse(
            upload_id=session.upload_id,
            s3_upload_id=session.s3_upload_id,
            object_key=session.object_key,
            part_size=session.part_size,
            total_parts=session.total_parts,
            status=session.status.value,
            expires_at=session.expires_at,
        )

    def get_presigned_part_url(
        self,
        db: Session,
        upload_id: str,
        part_number: int,
        user_id: str,
    ) -> PartPresignResponse:
        """
        Validates ownership and generates a presigned PUT URL for a specific part number.
        """
        session = db.query(UploadSession).filter(UploadSession.upload_id == upload_id).first()
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload session not found")

        if session.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: unauthorized user")

        if session.status in (UploadStatus.COMPLETED, UploadStatus.ABORTED, UploadStatus.EXPIRED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot upload to a session in status {session.status.value}",
            )

        if part_number < 1 or part_number > session.total_parts:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid part number {part_number}. Must be between 1 and {session.total_parts}",
            )

        # Update session status to UPLOADING if it was INITIATED
        if session.status == UploadStatus.INITIATED:
            session.status = UploadStatus.UPLOADING
            db.commit()

        presigned_url = self.s3_service.generate_presigned_part_url(
            object_key=session.object_key,
            s3_upload_id=session.s3_upload_id,
            part_number=part_number,
            expires_in=settings.presigned_url_expiration_seconds,
        )

        return PartPresignResponse(
            upload_id=session.upload_id,
            part_number=part_number,
            presigned_url=presigned_url,
            expires_in_seconds=settings.presigned_url_expiration_seconds,
        )

    def get_resume_status(
        self,
        db: Session,
        upload_id: str,
        user_id: str,
    ) -> ResumeStatusResponse:
        """
        Authoritative resume query.
        Fetches the confirmed parts stored in S3, compares against expected parts,
        and returns the missing parts to upload.
        """
        session = db.query(UploadSession).filter(UploadSession.upload_id == upload_id).first()
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload session not found")

        if session.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: unauthorized user")

        # Query S3 directly for confirmed parts
        s3_parts = self.s3_service.list_all_uploaded_parts(
            object_key=session.object_key,
            s3_upload_id=session.s3_upload_id,
        )

        uploaded_part_numbers = {p["PartNumber"] for p in s3_parts}
        all_part_numbers = set(range(1, session.total_parts + 1))
        missing_part_numbers = sorted(list(all_part_numbers - uploaded_part_numbers))

        total_uploaded_bytes = sum(p["Size"] for p in s3_parts)
        percentage = round((total_uploaded_bytes / session.file_size) * 100, 2)
        if percentage > 100.0:
            percentage = 100.0

        part_info_list = [
            PartInfo(
                part_number=p["PartNumber"],
                etag=p["ETag"],
                size_bytes=p["Size"],
            )
            for p in sorted(s3_parts, key=lambda x: x["PartNumber"])
        ]

        return ResumeStatusResponse(
            upload_id=session.upload_id,
            status=session.status.value,
            file_size=session.file_size,
            part_size=session.part_size,
            total_parts=session.total_parts,
            uploaded_parts_count=len(s3_parts),
            uploaded_bytes=total_uploaded_bytes,
            percentage_complete=percentage,
            missing_part_numbers=missing_part_numbers,
            uploaded_parts=part_info_list,
        )

    def complete_upload(
        self,
        db: Session,
        upload_id: str,
        user_id: str,
    ) -> UploadCompleteResponse:
        """
        Idempotent completion:
        1. Checks if already completed (returns existing Video).
        2. Validates that all parts are present in S3.
        3. Calls S3 complete_multipart_upload to stitch parts.
        4. Transitions session to COMPLETED and creates Video record.
        """
        session = db.query(UploadSession).filter(UploadSession.upload_id == upload_id).first()
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload session not found")

        if session.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: unauthorized user")

        # IDEMPOTENCY CHECK: If already completed, return existing video record
        if session.status == UploadStatus.COMPLETED:
            video = db.query(Video).filter(Video.upload_session_id == upload_id).first()
            if video:
                return UploadCompleteResponse(
                    upload_id=session.upload_id,
                    video_id=video.video_id,
                    object_key=video.object_key,
                    file_size=video.file_size,
                    status=video.status.value,
                    message="Upload was already completed successfully (idempotent response).",
                )

        if session.status in (UploadStatus.ABORTED, UploadStatus.EXPIRED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot complete an upload in status {session.status.value}",
            )

        # Authoritative S3 check: verify all parts exist
        s3_parts = self.s3_service.list_all_uploaded_parts(
            object_key=session.object_key,
            s3_upload_id=session.s3_upload_id,
        )

        uploaded_part_numbers = {p["PartNumber"] for p in s3_parts}
        expected_part_numbers = set(range(1, session.total_parts + 1))
        missing = sorted(list(expected_part_numbers - uploaded_part_numbers))

        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot complete upload. Missing {len(missing)} part(s): {missing[:10]}{'...' if len(missing) > 10 else ''}",
            )

        # Assemble the video in S3
        self.s3_service.complete_multipart_upload(
            object_key=session.object_key,
            s3_upload_id=session.s3_upload_id,
            parts=s3_parts,
        )

        # Mark session COMPLETED and create Video record
        session.status = UploadStatus.COMPLETED
        video = Video(
            video_id=str(uuid.uuid4()),
            user_id=session.user_id,
            upload_session_id=session.upload_id,
            title=session.filename,
            object_key=session.object_key,
            file_size=session.file_size,
            status=VideoStatus.READY,
        )
        db.add(video)
        db.commit()
        db.refresh(video)

        return UploadCompleteResponse(
            upload_id=session.upload_id,
            video_id=video.video_id,
            object_key=video.object_key,
            file_size=video.file_size,
            status=video.status.value,
            message="Upload successfully completed and video assembled in S3.",
        )

    def abort_upload(
        self,
        db: Session,
        upload_id: str,
        user_id: str,
    ) -> UploadAbortResponse:
        """Aborts an active upload session in S3 and marks it ABORTED in PostgreSQL."""
        session = db.query(UploadSession).filter(UploadSession.upload_id == upload_id).first()
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload session not found")

        if session.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: unauthorized user")

        if session.status == UploadStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot abort a completed upload.",
            )

        if session.status != UploadStatus.ABORTED:
            self.s3_service.abort_multipart_upload(
                object_key=session.object_key,
                s3_upload_id=session.s3_upload_id,
            )
            session.status = UploadStatus.ABORTED
            db.commit()

        return UploadAbortResponse(
            upload_id=session.upload_id,
            status=session.status.value,
            message="Upload session aborted and S3 storage freed.",
        )

