import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import LoginSession
from app.services.auth import (
    anonymous_identity,
    authenticate,
    create_session,
    find_identity,
    issue_auth_token,
    register,
    reset_password,
    rotate_session,
    verify_email,
)
from app.services.mail import send_auth_link

pytestmark = pytest.mark.integration


async def test_register_verify_login_and_rotation(database):
    db, settings = database
    async with db.transaction() as session:
        user = await register(session, "Seller@Example.com", "local-test-password-42")
        assert user.email_normalized == "seller@example.com"
        token = await issue_auth_token(session, user, "verify_email")
        await send_auth_link(settings, user.email_normalized, token, "verify_email")
        await verify_email(session, token)
        assert user.email_verified_at
        assert await register(session, "seller@example.com", "local-test-password-42") is None
    async with db.transaction() as session:
        with pytest.raises(AppError):
            await verify_email(session, token)
        user = await authenticate(session, "seller@example.com", "local-test-password-42")
        assert await find_identity(session, None) is None
        anonymous, cookie = await anonymous_identity(session)
        logged_cookie = await rotate_session(session, anonymous, user)
    async with db.transaction() as session:
        current = await find_identity(session, logged_cookie)
        assert current.user.id == user.id
        assert await find_identity(session, cookie) is None
    assert len(list(settings.mail_dir.glob("*.eml"))) == 1


async def test_password_reset_revokes_sessions_and_is_single_use(database, account):
    db, _ = database
    async with db.transaction() as session:
        record, _ = await create_session(session, account.id)
        token = await issue_auth_token(session, account, "reset_password")
    async with db.transaction() as session:
        await reset_password(session, token, "new-local-password-42")
    async with db.transaction() as session:
        stored = await session.scalar(select(LoginSession).where(LoginSession.id == record.id))
        assert stored.revoked_at is not None
        with pytest.raises(AppError):
            await reset_password(session, token, "another-password-42")
        user = await authenticate(session, account.email_normalized, "new-local-password-42")
        assert user.id == account.id