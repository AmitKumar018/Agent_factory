
from pydantic import BaseModel


class LoginRequest(BaseModel):
    """Body for POST /auth/login"""
    username: str
    password: str


class RegisterRequest(BaseModel):
    """Body for POST /auth/register (dev-only)"""
    username: str
    password: str


class TokenResponse(BaseModel):
    """Returned on successful login"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int   # seconds until expiry


class TokenData(BaseModel):
    """Parsed claims from a validated JWT"""
    sub: str          # user_id
    username: str
