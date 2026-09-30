from typing import Any


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 422,
                 fields: dict[str, Any] | None = None, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.status = status
        self.fields = fields or {}
        self.retry_after = retry_after


def not_found() -> AppError:
    return AppError("not_found", "Ma'lumot topilmadi.", 404)
