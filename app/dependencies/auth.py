import logging
from typing import Optional
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, APIKeyHeader
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User

logger = logging.getLogger(__name__)

# Support both standard Bearer tokens and X-API-Key header
bearer_scheme = HTTPBearer(auto_error=False)
api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

DEFAULT_TEST_USER_ID = "test_creator_123"
DEFAULT_TEST_API_KEY = "test-api-key-123"
DEFAULT_TEST_EMAIL = "creator@example.com"


def get_current_user(
    bearer: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    api_key_header: Optional[str] = Security(api_key_header_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Authenticates the request via Bearer token or X-API-Key header.
    Returns the authenticated User record or raises HTTP 401 Unauthorized.
    """
    token: Optional[str] = None
    if bearer and bearer.credentials:
        token = bearer.credentials
    elif api_key_header:
        token = api_key_header

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide 'Authorization: Bearer <token>' or 'X-API-Key: <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.api_key == token).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token or API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def seed_default_user_if_needed(db: Session) -> User:
    """Seeds a default test user if the database has no users yet."""
    user = db.query(User).filter(User.api_key == DEFAULT_TEST_API_KEY).first()
    if not user:
        logger.info("Seeding default test user: %s (API Key: %s)", DEFAULT_TEST_EMAIL, DEFAULT_TEST_API_KEY)
        user = User(
            user_id=DEFAULT_TEST_USER_ID,
            email=DEFAULT_TEST_EMAIL,
            api_key=DEFAULT_TEST_API_KEY,
            storage_quota_bytes=50 * 1024 * 1024 * 1024,  # 50 GB
            used_storage_bytes=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

