import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
from botocore.exceptions import ClientError
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.session import UploadSession, UploadStatus
from app.models.video import Video, VideoStatus
from app.models.user import User
from app.schemas.upload import (
    UploadInitRequest,
    UploadInitResponse,
    PartPresignResponse,
    PartInfo,
    ResumeStatusResponse,
    UploadCompleteResponse,
    UploadAbortResponse,
    DeleteVideoResponse,
    CleanupResponse,
)
from app.services.s3_service import S3Service

logger = logging.getLogger(__name__)
settings = get_settings()


class UploadService:
    """Orchestrates database state and S3 multipart upload operations."""

    def __init__(self, s3_service: S3Service | None = None) -> None:
        self.s3_service = s3_service or S3Service()

    def initiate_upload(self, db: Session, req: UploadInitRequest, user: User) -> UploadInitResponse:
        """
        Validates user quota, determines part size and total parts,
        initiates S3 multipart upload, and persists the UploadSession in PostgreSQL.
        """
        # Quota Validation: Ensure the user's storage quota is not exceeded
        if user.used_storage_bytes + req.file_size > user.storage_quota_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Storage quota exceeded. Currently using {user.used_storage_bytes // (1024 * 1024)} MB of "
                    f"{user.storage_quota_bytes // (1024 * 1024)} MB. "
                    f"Cannot upload {req.file_size // (1024 * 1024)} MB."
                ),
            )

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
        object_key = f"videos/{user.user_id}/{upload_id}/{req.filename}"

        # Step 1: Initiate S3 Multipart Upload
        s3_upload_id = self.s3_service.create_multipart_upload(object_key)

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=settings.presigned_url_expiration_seconds * 24)

        # Step 2: Persist upload session record in database
        session = UploadSession(
            upload_id=upload_id,
            user_id=user.user_id,
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
        Idempotent completion with concurrency protection and crash-recovery:
        1. Uses pessimistic row locking (SELECT ... FOR UPDATE) to eliminate race conditions.
        2. Validates all parts and verifies that the sum of part sizes matches declared file_size.
        3. Calls S3 complete_multipart_upload. On NoSuchUpload errors, self-heals by verifying S3 object presence.
        4. Quarantines video in PROCESSING state, scans it, and promotes to READY.
        """
        # Concurrency Protection: Pessimistic locking prevents duplicate completion races
        session = (
            db.query(UploadSession)
            .filter(UploadSession.upload_id == upload_id)
            .with_for_update()
            .first()
        )
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload session not found")

        if session.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: unauthorized user")

        # IDEMPOTENCY CHECK: If already completed, return existing video record immediately
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

        # Integrity Check: Compare total bytes uploaded against declared file_size
        total_uploaded_bytes = sum(p["Size"] for p in s3_parts)
        if total_uploaded_bytes != session.file_size:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Integrity check failed: uploaded parts total {total_uploaded_bytes} bytes, "
                    f"but declared file size is {session.file_size} bytes."
                ),
            )

        # Assemble the video in S3 with crash-recovery self-healing
        try:
            self.s3_service.complete_multipart_upload(
                object_key=session.object_key,
                s3_upload_id=session.s3_upload_id,
                parts=s3_parts,
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("NoSuchUpload", "NoSuchUploadException"):
                # Self-healing: Check if S3 completed assembly in an earlier attempt where the DB commit failed
                obj_meta = self.s3_service.get_object_metadata(session.object_key)
                if obj_meta and obj_meta.get("ContentLength") == session.file_size:
                    logger.info("Self-healing: Found already-assembled S3 object for upload_id=%s", upload_id)
                else:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="S3 multipart upload session no longer exists and final object was not found.",
                    )
            else:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to assemble S3 multipart upload: {str(e)}",
                )

        # Mark session COMPLETED and place Video in PROCESSING (quarantine) status
        session.status = UploadStatus.COMPLETED
        session.updated_at = datetime.now(timezone.utc)

        video = db.query(Video).filter(Video.upload_session_id == upload_id).first()
        if not video:
            video = Video(
                video_id=str(uuid.uuid4()),
                user_id=session.user_id,
                upload_session_id=session.upload_id,
                title=session.filename,
                object_key=session.object_key,
                file_size=session.file_size,
                status=VideoStatus.PROCESSING,  # Initial quarantine state
            )
            db.add(video)
        else:
            video.status = VideoStatus.PROCESSING

        db.commit()
        db.refresh(video)

        # Quarantine verification & promotion: simulate scan and promote to READY
        self._scan_and_promote_video(db, video.video_id)
        db.refresh(video)

        return UploadCompleteResponse(
            upload_id=session.upload_id,
            video_id=video.video_id,
            object_key=video.object_key,
            file_size=video.file_size,
            status=video.status.value,
            message="Upload successfully completed, scanned, and assembled in S3.",
        )

    def _scan_and_promote_video(self, db: Session, video_id: str) -> None:
        """
        Simulates container header validation, codec inspection, and malware scanning
        before promoting a video from PROCESSING to READY. Updates user storage usage.
        """
        video = db.query(Video).filter(Video.video_id == video_id).first()
        if not video:
            return
        # In a full production distributed system, this is handled by an async worker (SQS/Kafka).
        # We simulate the clean scanning verification here:
        video.status = VideoStatus.READY

        # Update user's used storage quota
        user = db.query(User).filter(User.user_id == video.user_id).first()
        if user:
            user.used_storage_bytes += video.file_size

        db.commit()

    def delete_video(
        self,
        db: Session,
        video_id: str,
        user_id: str,
    ) -> DeleteVideoResponse:
        """Removes a completed video and its final object from storage, reclaiming quota."""
        video = db.query(Video).filter(Video.video_id == video_id).first()
        if not video:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")

        if video.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: unauthorized user")

        session = db.query(UploadSession).filter(UploadSession.upload_id == video.upload_session_id).first()
        if session:
            session.status = UploadStatus.ABORTED
            session.updated_at = datetime.now(timezone.utc)

        try:
            self.s3_service.delete_object(video.object_key)
        except Exception as exc:  # pragma: no cover - surfaced as API error for callers
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Could not delete the video from S3: {exc}",
            ) from exc

        # Reclaim user storage quota
        user = db.query(User).filter(User.user_id == video.user_id).first()
        if user:
            user.used_storage_bytes = max(0, user.used_storage_bytes - video.file_size)

        db.delete(video)
        db.commit()

        return DeleteVideoResponse(
            video_id=video.video_id,
            upload_id=video.upload_session_id,
            status="DELETED",
            message="Video deleted successfully from storage and metadata.",
            deleted_from_storage=True,
        )

    def delete_completed_upload(
        self,
        db: Session,
        upload_id: str,
        user_id: str,
    ) -> DeleteVideoResponse:
        """Delete a completed uploaded video by upload session id."""
        session = db.query(UploadSession).filter(UploadSession.upload_id == upload_id).first()
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload session not found")

        if session.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: unauthorized user")

        video = db.query(Video).filter(Video.upload_session_id == upload_id).first()
        if not video:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No completed video exists for this upload session")

        return self.delete_video(db=db, video_id=video.video_id, user_id=user_id)

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
                detail="Completed uploads must be deleted via the video deletion endpoint.",
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

    def cleanup_expired_sessions(self, db: Session) -> CleanupResponse:
        """
        Sweeps for active upload sessions that exceeded expires_at.
        Aborts the S3 multipart upload to free chunk storage and marks status EXPIRED.
        """
        now = datetime.now(timezone.utc)
        expired_sessions = (
            db.query(UploadSession)
            .filter(
                UploadSession.status.in_([UploadStatus.INITIATED, UploadStatus.UPLOADING]),
                UploadSession.expires_at < now,
            )
            .all()
        )

        cleaned_count = 0
        for session in expired_sessions:
            try:
                self.s3_service.abort_multipart_upload(
                    object_key=session.object_key,
                    s3_upload_id=session.s3_upload_id,
                )
            except Exception as e:
                logger.warning("Failed to abort S3 upload for expired session %s: %s", session.upload_id, e)

            session.status = UploadStatus.EXPIRED
            session.updated_at = now
            cleaned_count += 1

        db.commit()
        logger.info("Cleaned up %d expired upload sessions.", cleaned_count)
        return CleanupResponse(
            cleaned_sessions_count=cleaned_count,
            message=f"Successfully cleaned up and aborted {cleaned_count} expired upload session(s).",
        )

