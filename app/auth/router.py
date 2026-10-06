from fastapi import APIRouter, Depends, HTTPException, status, Form, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.storage.database import get_db
from app.auth.schemas import RegisterRequest, TokenResponse
from app.auth import service as auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user (dev-only)"
)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    Create a new user account and return a JWT immediately.
    In production this endpoint would be disabled or admin-gated.
    """
    existing = await auth_service.get_user_by_username(db, body.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "USERNAME_TAKEN", "message": "Username already exists"}
        )

    user = await auth_service.create_user(db, body.username, body.password)
    token, expires_in = auth_service.create_access_token(user.id, user.username)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login and receive a JWT bearer token"
)
async def login(
    request: Request,
    username: Optional[str] = Form(default=None),
    password: Optional[str] = Form(default=None),
    grant_type: Optional[str] = Form(default=None),
    scope: Optional[str] = Form(default=""),
    client_id: Optional[str] = Form(default=None),
    client_secret: Optional[str] = Form(default=None),
    db: AsyncSession = Depends(get_db)
):
    """
    Validate credentials and return a signed JWT.
    Use this token in the Authorization: Bearer <token> header for all other calls.
    """
    if username is None or password is None:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        username = username or payload.get("username")
        password = password or payload.get("password")

    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "MISSING_CREDENTIALS", "message": "Username and password are required"},
        )

    user = await auth_service.get_user_by_username(db, username)

    if not user or not auth_service.verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_CREDENTIALS", "message": "Username or password is incorrect"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    token, expires_in = auth_service.create_access_token(user.id, user.username)
    return TokenResponse(access_token=token, expires_in=expires_in)
