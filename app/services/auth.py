import asyncio
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.db import advisory_lock
from app.errors import AppError
from app.models import AuthToken, LoginSession, User
from app.security import check_password, hash_password, new_token, normalize_email, token_hash


@dataclass(frozen=True)
class Identity:
    session_id: UUID | None
    user: User | None
    csrf: str


ANONYMOUS = Identity(None, None, "")


async def create_session(session: AsyncSession, user_id: UUID | None = None) -> tuple[LoginSession, str]:
    token = new_token()
    record = LoginSession(user_id=user_id, token_hash=token_hash(token), csrf_secret=new_token(),
                          expires_at=utcnow() + timedelta(days=7))
    session.add(record)
    await session.flush()
    return record, token


async def find_identity(session: AsyncSession, token: str | None) -> Identity | None:
    if not token or len(token) > 128:
        return None
    record = await session.scalar(select(LoginSession).where(
        LoginSession.token_hash == token_hash(token), LoginSession.revoked_at.is_(None),
        LoginSession.expires_at > utcnow(),
        LoginSession.last_seen_at > utcnow() - timedelta(hours=24),
    ))
    if record is None:
        return None
    user = await session.get(User, record.user_id) if record.user_id else None
    if record.user_id and (user is None or user.deletion_requested_at):
        record.revoked_at = utcnow()
        return None
    if record.last_seen_at < utcnow() - timedelta(minutes=5):
        record.last_seen_at = utcnow()
    return Identity(record.id, user, record.csrf_secret)


async def anonymous_identity(session: AsyncSession) -> tuple[Identity, str]:
    record, token = await create_session(session)
    return Identity(record.id, None, record.csrf_secret), token


async def register(session: AsyncSession, email: str, password: str) -> User | None:
    email = normalize_email(email)
    hashed = await asyncio.to_thread(hash_password, password)
    await advisory_lock(session, f"email:{email}")
    existing = await session.scalar(select(User).where(User.email_normalized == email))
    if existing:
        return None
    user = User(email_normalized=email, password_hash=hashed)
    session.add(user)
    await session.flush()
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User:
    try:
        normalized = normalize_email(email)
    except AppError:
        normalized = "invalid"
    user = await session.scalar(select(User).where(User.email_normalized == normalized))
    valid = await asyncio.to_thread(check_password, password, user.password_hash if user else None)
    if not valid or not user or user.deletion_requested_at:
        raise AppError("invalid_login", "Email yoki parol noto'g'ri.", 401)
    return user


async def rotate_session(session: AsyncSession, identity: Identity, user: User) -> str:
    await session.execute(update(LoginSession).where(LoginSession.id == identity.session_id)
                          .values(revoked_at=utcnow()))
    _, token = await create_session(session, user.id)
    return token


async def issue_auth_token(session: AsyncSession, user: User, purpose: str) -> str:
    await advisory_lock(session, f"auth-token:{user.id}:{purpose}")
    await session.execute(update(AuthToken).where(
        AuthToken.user_id == user.id, AuthToken.purpose == purpose, AuthToken.used_at.is_(None),
    ).values(used_at=utcnow()))
    token = new_token()
    expiry = timedelta(hours=24) if purpose == "verify_email" else timedelta(minutes=30)
    session.add(AuthToken(user_id=user.id, purpose=purpose, token_hash=token_hash(token),
                          expires_at=utcnow() + expiry))
    return token


async def consume_auth_token(session: AsyncSession, token: str, purpose: str) -> User:
    record = await session.scalar(select(AuthToken).where(
        AuthToken.token_hash == token_hash(token), AuthToken.purpose == purpose,
        AuthToken.used_at.is_(None), AuthToken.expires_at > utcnow(),
    ).with_for_update())
    if not record:
        raise AppError("invalid_token", "Havola eskirgan yoki ishlatilgan. Yangi xat so'rang.")
    user = await session.get(User, record.user_id, with_for_update=True)
    if not user or user.deletion_requested_at:
        raise AppError("invalid_token", "Havola eskirgan yoki ishlatilgan. Yangi xat so'rang.")
    record.used_at = utcnow()
    return user


async def verify_email(session: AsyncSession, token: str) -> None:
    user = await consume_auth_token(session, token, "verify_email")
    user.email_verified_at = utcnow()


async def reset_password(session: AsyncSession, token: str, password: str) -> None:
    hashed = await asyncio.to_thread(hash_password, password)
    user = await consume_auth_token(session, token, "reset_password")
    user.password_hash = hashed
    await revoke_all(session, user.id)


async def revoke_all(session: AsyncSession, user_id: UUID) -> None:
    await session.execute(update(LoginSession).where(LoginSession.user_id == user_id)
                          .values(revoked_at=utcnow()))
    await session.execute(update(AuthToken).where(AuthToken.user_id == user_id)
                          .values(used_at=utcnow()))
