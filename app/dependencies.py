from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.schemas import TokenData
from app.auth.service import JWTError, decode_token, get_user_by_id
from app.storage.database import AsyncSessionLocal

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    """
    Validate a Bearer JWT and return the authenticated user.
    Missing credentials fail before opening a DB session.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authenticated",
        )

    try:
        token_data: TokenData = decode_token(credentials.credentials)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "Token is invalid or expired"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    async with AsyncSessionLocal() as db:
        user = await get_user_by_id(db, token_data.sub)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "USER_NOT_FOUND", "message": "User associated with token no longer exists"},
        )

    return user


async def get_current_user_dependency(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    return await get_current_user(credentials)


# FastAPI Depends() expects a callable with Depends-bound parameters.
async def current_user_from_bearer(credentials: HTTPAuthorizationCredentials | None = bearer_scheme) -> User:
    return await get_current_user(credentials)


async def check_project_membership(project, user: User, db: AsyncSession | None = None) -> bool:
    """
    Single-user project guard used by workflow routers.
    Returns 404 for non-owned projects to match the rest of the API.
    """
    if getattr(project, "owner_id", None) != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROJECT_NOT_FOUND", "message": "Project not found"},
        )
    return True