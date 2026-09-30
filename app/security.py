import hashlib
import hmac
import json
import secrets
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from email_validator import EmailNotValidError, validate_email

from app.errors import AppError

PASSWORDS = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2)
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))


def normalize_email(email: str) -> str:
    try:
        return validate_email(email, check_deliverability=False).normalized.casefold()
    except EmailNotValidError:
        raise AppError("invalid_email", "Email manzilini tekshiring.") from None


def validate_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise AppError("invalid_password", "Parol 12-128 belgidan iborat bo'lishi kerak.")
    return password


def hash_password(password: str) -> str:
    return PASSWORDS.hash(validate_password(password))


def check_password(password: str, password_hash: str | None) -> bool:
    try:
        verified = PASSWORDS.verify(password_hash or DUMMY_HASH, password[:129])
        return verified and password_hash is not None and len(password) <= 128
    except (VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def fingerprint(value: Any) -> str:
    return token_hash(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def csrf_matches(expected: str, actual: str | None) -> bool:
    return bool(actual and hmac.compare_digest(expected, actual))
