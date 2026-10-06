from datetime import datetime, timedelta, timezone
from typing import Optional
import base64
import hashlib
import hmac
import json
import os
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import get_settings
from app.auth.models import User
from app.auth.schemas import TokenData

def _ensure_bcrypt_passlib_compat() -> None:
    """Provide bcrypt.__about__.__version__ for passlib 1.7.x with bcrypt 4.x."""
    try:
        import bcrypt
    except Exception:
        return
    if hasattr(bcrypt, "__about__"):
        return
    version = getattr(bcrypt, "__version__", "4")
    bcrypt.__about__ = type("_BcryptAbout", (), {"__version__": version})()


_ensure_bcrypt_passlib_compat()

try:
    from jose import jwt, JWTError
except ModuleNotFoundError:
    class JWTError(Exception):
        pass

    class _LocalJWT:
        @staticmethod
        def _b64encode(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

        @staticmethod
        def _b64decode(data: str) -> bytes:
            padding = "=" * (-len(data) % 4)
            return base64.urlsafe_b64decode((data + padding).encode("ascii"))

        @staticmethod
        def encode(payload: dict, secret: str, algorithm: str = "HS256") -> str:
            if algorithm != "HS256":
                raise JWTError("Local JWT fallback supports HS256 only")

            serializable_payload = dict(payload)
            for key in ("iat", "exp"):
                value = serializable_payload.get(key)
                if isinstance(value, datetime):
                    serializable_payload[key] = int(value.timestamp())

            header = {"alg": algorithm, "typ": "JWT"}
            header_b64 = _LocalJWT._b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
            payload_b64 = _LocalJWT._b64encode(json.dumps(serializable_payload, separators=(",", ":")).encode("utf-8"))
            signing_input = f"{header_b64}.{payload_b64}"
            signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
            return f"{signing_input}.{_LocalJWT._b64encode(signature)}"

        @staticmethod
        def decode(token: str, secret: str, algorithms: list[str] | None = None) -> dict:
            if algorithms and "HS256" not in algorithms:
                raise JWTError("Unsupported algorithm")

            try:
                header_b64, payload_b64, signature_b64 = token.split(".")
            except ValueError as exc:
                raise JWTError("Malformed token") from exc

            signing_input = f"{header_b64}.{payload_b64}"
            expected = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
            actual = _LocalJWT._b64decode(signature_b64)
            if not hmac.compare_digest(expected, actual):
                raise JWTError("Invalid signature")

            try:
                payload = json.loads(_LocalJWT._b64decode(payload_b64))
            except Exception as exc:
                raise JWTError("Invalid payload") from exc

            exp = payload.get("exp")
            if exp is not None and datetime.now(timezone.utc).timestamp() > float(exp):
                raise JWTError("Token expired")

            return payload

    jwt = _LocalJWT()

try:
    from passlib.context import CryptContext
except ModuleNotFoundError:
    CryptContext = None

settings = get_settings()


def _build_pwd_context():
    if not CryptContext:
        return None
    try:
        context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        probe = context.hash("password-backend-probe")
        if not context.verify("password-backend-probe", probe):
            return None
        return context
    except Exception:
        return None


pwd_context = _build_pwd_context()


def _hash_password_local(plain: str) -> str:
    salt = os.urandom(16)
    rounds = 260000
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, rounds)
    return "pbkdf2_sha256${}${}${}".format(
        rounds,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def _verify_password_local(plain: str, hashed: str) -> bool:
    try:
        scheme, rounds_raw, salt_raw, digest_raw = hashed.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        rounds = int(rounds_raw)
        salt = base64.b64decode(salt_raw)
        expected = base64.b64decode(digest_raw)
    except Exception:
        return False

    actual = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(actual, expected)


def hash_password(plain: str) -> str:
    """Hash a plain-text password."""
    if pwd_context:
        try:
            return pwd_context.hash(plain)
        except Exception:
            pass
    return _hash_password_local(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Check a plain password against a stored hash."""
    if hashed.startswith("pbkdf2_sha256$"):
        return _verify_password_local(plain, hashed)
    if not pwd_context:
        return False
    try:
        return bool(pwd_context.verify(plain, hashed))
    except Exception:
        return False


def create_access_token(user_id: str, username: str) -> tuple[str, int]:
    """
    Issue a signed JWT.
    Returns (token_string, expires_in_seconds).
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "username": username,
        "iat": datetime.now(timezone.utc),
        "exp": expire,
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, settings.JWT_EXPIRE_MINUTES * 60


def decode_token(token: str) -> TokenData:
    """
    Validate and decode a JWT.
    Raises JWTError if invalid or expired.
    """
    payload = jwt.decode(
        token,
        settings.JWT_SECRET,
        algorithms=[settings.JWT_ALGORITHM]
    )
    return TokenData(sub=payload["sub"], username=payload["username"])


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    """Fetch a user by username from the DB."""
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: str) -> Optional[User]:
    """Fetch a user by ID from the DB."""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def create_user(db: AsyncSession, username: str, password: str) -> User:
    """Create and persist a new user with a hashed password."""
    user = User(
        id=str(uuid.uuid4()),
        username=username,
        hashed_password=hash_password(password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

