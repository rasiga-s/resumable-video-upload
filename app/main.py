import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine, SessionLocal
from app.dependencies.auth import seed_default_user_if_needed
from app.routers import uploads_router
from app.services.s3_service import S3Service

# Configure logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application startup and shutdown events:
    1. Auto-creates PostgreSQL database tables if not already present.
    2. Auto-provisions the LocalStack S3 bucket and configures CORS exposing ETag.
    """
    logger.info("Initializing database tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialized successfully.")

    # Seed default creator user if not present
    with SessionLocal() as db:
        seed_default_user_if_needed(db)

    logger.info("Ensuring S3 bucket and CORS rules exist...")
    s3_service = S3Service()
    try:
        s3_service.ensure_bucket_and_cors()
        logger.info("S3 storage initialized successfully.")
    except Exception as e:
        logger.warning("Could not initialize S3 bucket on startup (LocalStack might still be starting): %s", e)

    yield

    logger.info("Shutting down video upload service...")


app = FastAPI(
    title="Resumable Video Upload Service",
    description="YouTube-style resumable video upload system using FastAPI, PostgreSQL, and AWS S3 multipart uploads.",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for browser clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check route for Docker & container orchestration
@app.get("/health", tags=["Health"])
def health_check() -> dict:
    return {"status": "ok", "service": "video-upload-service"}


# Mount the uploads router
app.include_router(uploads_router)

