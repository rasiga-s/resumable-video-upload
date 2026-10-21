from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.upload import (
    UploadInitRequest,
    UploadInitResponse,
    PartPresignResponse,
    ResumeStatusResponse,
    UploadCompleteResponse,
    UploadAbortResponse,
    DeleteVideoResponse,
)
from app.services.upload_service import UploadService

router = APIRouter(prefix="/uploads", tags=["Uploads"])
upload_service = UploadService()


@router.post(
    "",
    response_model=UploadInitResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Initiate a resumable upload session",
)
def initiate_upload(
    req: UploadInitRequest,
    db: Session = Depends(get_db),
) -> UploadInitResponse:
    """
    Creates an upload session in PostgreSQL and starts a multipart upload in S3.
    Returns session metadata including total parts and expected part sizes.
    """
    return upload_service.initiate_upload(db=db, req=req)


@router.get(
    "/{upload_id}/resume",
    response_model=ResumeStatusResponse,
    summary="Query authoritative resume status and missing parts",
)
def get_resume_status(
    upload_id: str,
    user_id: str = Query(..., description="User ID for authorization"),
    db: Session = Depends(get_db),
) -> ResumeStatusResponse:
    """
    Queries S3 directly to inspect which parts have been safely stored.
    Returns the list of missing parts that the client still needs to upload.
    """
    return upload_service.get_resume_status(
        db=db,
        upload_id=upload_id,
        user_id=user_id,
    )


@router.get(
    "/{upload_id}/parts/{part_number}/url",
    response_model=PartPresignResponse,
    summary="Generate presigned S3 PUT URL for a specific part",
)
def get_presigned_part_url(
    upload_id: str,
    part_number: int,
    user_id: str = Query(..., description="User ID for authorization"),
    db: Session = Depends(get_db),
) -> PartPresignResponse:
    """
    Generates a secure, short-lived presigned URL directly to S3/LocalStack.
    The client streams the chunk bytes directly to S3 without burdening the API server.
    """
    return upload_service.get_presigned_part_url(
        db=db,
        upload_id=upload_id,
        part_number=part_number,
        user_id=user_id,
    )


@router.post(
    "/{upload_id}/complete",
    response_model=UploadCompleteResponse,
    summary="Assemble uploaded parts into final video",
)
def complete_upload(
    upload_id: str,
    user_id: str = Query(..., description="User ID for authorization"),
    db: Session = Depends(get_db),
) -> UploadCompleteResponse:
    """
    Verifies that all parts are safely stored in S3 and triggers S3 assembly.
    Idempotent: safe to call multiple times.
    """
    return upload_service.complete_upload(
        db=db,
        upload_id=upload_id,
        user_id=user_id,
    )


@router.delete(
    "/{upload_id}",
    response_model=UploadAbortResponse,
    summary="Cancel an active upload session",
)
def abort_upload(
    upload_id: str,
    user_id: str = Query(..., description="User ID for authorization"),
    db: Session = Depends(get_db),
) -> UploadAbortResponse:
    """
    Aborts the S3 multipart upload and cleans up stored part bytes.
    """
    return upload_service.abort_upload(
        db=db,
        upload_id=upload_id,
        user_id=user_id,
    )


@router.delete(
    "/videos/{video_id}",
    response_model=DeleteVideoResponse,
    summary="Delete a completed uploaded video from storage and metadata",
)
def delete_video(
    video_id: str,
    user_id: str = Query(..., description="User ID for authorization"),
    db: Session = Depends(get_db),
) -> DeleteVideoResponse:
    """Deletes a completed video object from S3 and removes its database record."""
    return upload_service.delete_video(
        db=db,
        video_id=video_id,
        user_id=user_id,
    )


@router.delete(
    "/{upload_id}/video",
    response_model=DeleteVideoResponse,
    summary="Delete a completed upload by session id",
)
def delete_completed_upload(
    upload_id: str,
    user_id: str = Query(..., description="User ID for authorization"),
    db: Session = Depends(get_db),
) -> DeleteVideoResponse:
    """Deletes the final video for a completed upload session."""
    return upload_service.delete_completed_upload(
        db=db,
        upload_id=upload_id,
        user_id=user_id,
    )

