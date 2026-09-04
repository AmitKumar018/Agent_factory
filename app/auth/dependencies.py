from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.auth.models import User
from app.auth.service import JWTError, decode_token, get_user_by_id
from app.storage.database import AsyncSessionLocal

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """Validate an OAuth2 Bearer JWT and return the authenticated user."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "INVALID_TOKEN", "message": "Could not validate credentials"},
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        token_data = decode_token(token)
    except JWTError:
        raise credentials_exception

    async with AsyncSessionLocal() as db:
        user = await get_user_by_id(db, token_data.sub)

    if user is None:
        raise credentials_exception

    return user