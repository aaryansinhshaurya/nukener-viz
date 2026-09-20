import uuid
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserOut
from app.schemas.auth import LoginRequest, TokenResponse, RefreshRequest, ForgotPasswordRequest, ResetPasswordRequest
from app.models.token import PasswordReset
from app.services.tokens import new_token, token_digest
from app.services.email import send_email, email_is_configured, EmailDeliveryError
from app.config import settings
from app.auth.security import hash_password, verify_password
from app.auth.jwt_handler import create_access_token, create_refresh_token, decode_token
from app.auth.dependencies import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def signup(payload: UserCreate, db: Session = Depends(get_db)):
    if len(payload.password) < 8 or len(payload.password.encode("utf-8")) > 72:
        raise HTTPException(status_code=422, detail="Password must be 8–72 bytes")
    email = payload.email.lower()
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=email,
        name=payload.name,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    return TokenResponse(
        access_token=create_access_token(user.id, user.session_version),
        refresh_token=create_refresh_token(user.id, user.session_version),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    try:
        data = decode_token(payload.refresh_token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    if data.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = uuid.UUID(data["sub"])
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    if data.get("sv", 0) != user.session_version:
        raise HTTPException(status_code=401, detail="Session expired")

    return TokenResponse(
        access_token=create_access_token(user.id, user.session_version),
        refresh_token=create_refresh_token(user.id, user.session_version),
    )


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Keep the response independent of account existence when email is available."""
    if not email_is_configured():
        logger.error("Password reset email delivery is not configured")
        raise HTTPException(status_code=503, detail="Password reset email is unavailable. Please try again later.")

    message = {"message": "If this email has an account, a reset link has been sent."}
    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if user is None:
        return message

    recent = db.query(PasswordReset).filter(
        PasswordReset.user_id == user.id,
        PasswordReset.created_at > datetime.now(timezone.utc) - timedelta(minutes=2),
        PasswordReset.used_at.is_(None),
        PasswordReset.expires_at > datetime.now(timezone.utc),
    ).first()
    if recent:
        return message

    raw, digest = new_token()
    reset = PasswordReset(
        user_id=user.id, token_hash=digest,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(reset)
    db.commit()
    try:
        send_email(user.email, "Reset your NukeNER Review password",
                   f"Open this link to reset your password (valid for one hour):\n"
                   f"{settings.FRONTEND_URL}/?reset={raw}\n\n"
                   "If you did not request this, you can ignore this email.")
    except EmailDeliveryError:
        logger.exception("Password reset email delivery failed")
        db.delete(reset)
        db.commit()
        raise HTTPException(status_code=503, detail="Password reset email is unavailable. Please try again later.")
    return message


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    if len(payload.password) < 8 or len(payload.password.encode("utf-8")) > 72:
        raise HTTPException(status_code=422, detail="Password must be 8–72 bytes")
    reset = db.query(PasswordReset).filter(PasswordReset.token_hash == token_digest(payload.token)).with_for_update().first()
    now = datetime.now(timezone.utc)
    if reset is None or reset.used_at is not None or reset.expires_at < now:
        raise HTTPException(status_code=400, detail="This reset link is invalid or expired")
    user = db.get(User, reset.user_id)
    user.hashed_password = hash_password(payload.password)
    user.session_version += 1
    reset.used_at = now
    db.commit()
    return {"message": "Password changed. You can now sign in."}
