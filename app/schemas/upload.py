from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class UploadInitRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255, description="Name of the video file")
    file_size: int = Field(
        ...,
        gt=0,
        le=50 * 1024 * 1024 * 1024,
        description="Total file size in bytes (maximum 50 GB per file)",
    )
    part_size: Optional[int] = Field(
        None,
        ge=5 * 1024 * 1024,
        description="Optional custom part size in bytes (minimum 5 MB per AWS S3 rule)",
    )


class UploadInitResponse(BaseModel):
    upload_id: str
    s3_upload_id: str
    object_key: str
    part_size: int
    total_parts: int
    status: str
    expires_at: datetime


class PartPresignResponse(BaseModel):
    upload_id: str
    part_number: int
    presigned_url: str
    expires_in_seconds: int


class PartInfo(BaseModel):
    part_number: int
    etag: str
    size_bytes: int


class ResumeStatusResponse(BaseModel):
    upload_id: str
    status: str
    file_size: int
    part_size: int
    total_parts: int
    uploaded_parts_count: int
    uploaded_bytes: int
    percentage_complete: float
    missing_part_numbers: List[int]
    uploaded_parts: List[PartInfo]


class UploadCompleteResponse(BaseModel):
    upload_id: str
    video_id: str
    object_key: str
    file_size: int
    status: str
    message: str


class UploadAbortResponse(BaseModel):
    upload_id: str
    status: str
    message: str


class DeleteVideoResponse(BaseModel):
    video_id: str
    upload_id: str
    status: str
    message: str
    deleted_from_storage: bool = True


class CleanupResponse(BaseModel):
    cleaned_sessions_count: int
    message: str


