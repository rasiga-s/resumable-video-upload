from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database Configuration
    database_url: str = "postgresql://user:password@localhost:5432/videodb"

    # AWS S3 / LocalStack Configuration
    # aws_endpoint_url: used internally by the backend to communicate with S3
    aws_endpoint_url: str = "http://localhost:4566"
    # aws_public_endpoint_url: used when generating presigned URLs for client browsers
    aws_public_endpoint_url: str = "http://localhost:4566"
    aws_region: str = "us-east-1"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    s3_bucket_name: str = "videos-bucket"

    # S3 Multipart constraints
    # AWS S3 enforces a 5 MB minimum part size for all parts except the final part
    min_part_size_bytes: int = 5 * 1024 * 1024  # 5 MB
    default_part_size_bytes: int = 10 * 1024 * 1024  # 10 MB default chunk
    presigned_url_expiration_seconds: int = 3600  # 1 hour


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton instance of the application settings."""
    return Settings()

