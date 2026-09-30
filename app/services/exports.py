import csv
import io
import json
import re
from typing import Any
from uuid import UUID

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.rules import check_business_rules
from app.errors import AppError, not_found
from app.models import Card, GenerationJob
from app.schemas import CardContent
from app.services.cards import ACTIVE, actor, unresolved_reviews

FIELDS = ("title", "short_description", "description", "suggested_category", "color", "material", "keywords")
HEADERS = ["card_id", "version", "created_at_utc", *[f"{name}_{language}" for name in FIELDS for language in ("uz", "ru")], "attributes_json"]
BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
MAX_EXPORT_BYTES = 10 * 1024 * 1024


def export_row(card: Card) -> list[str | int]:
    content = CardContent.model_validate(card.content_json)
    values: list[str | int] = [str(card.id), card.version, card.created_at.isoformat()]
    data = content.model_dump()
    for name in FIELDS:
        for language in ("uz", "ru"):
            value = data[name][language]
            values.append(json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value or "")
    values.append(json.dumps(data["attributes"], ensure_ascii=False))
    return values


async def collect_rows(session: AsyncSession, user_id: UUID, identifiers: list[UUID]) -> list[list]:
    await actor(session, user_id)
    cards = (await session.scalars(select(Card).where(
        Card.user_id == user_id, Card.id.in_(identifiers), Card.deleted_at.is_(None),
    ).with_for_update())).all()
    if len(cards) != len(identifiers):
        raise not_found()
    active = await session.scalar(select(GenerationJob.id).where(
        GenerationJob.card_id.in_(identifiers), GenerationJob.status.in_(ACTIVE),
    ))
    if active:
        raise AppError("job_active", "Eksportdan oldin vazifa tugashini kuting.", 409)
    indexed = {card.id: card for card in cards}
    for card in cards:
        if card.status != "ready" or unresolved_reviews(card):
            raise AppError("review_required", "Eksportdan oldin belgilangan maydonlarni tekshiring.", 409)
        try:
            check_business_rules(CardContent.model_validate(card.content_json))
        except ValueError as error:
            title = card.content_json["title"]["uz"][:80]
            raise AppError("export_validation", f"\"{title}\" eksport qilinmadi: {error}",
                           fields={"card_id": str(card.id)}) from None
    return [export_row(indexed[identifier]) for identifier in identifiers]


def clean_cell(value: Any, csv_format: bool = False) -> str:
    text = BAD_XML.sub("", str(value))
    if len(text) > 32767:
        raise AppError("export_cell_limit", "Eksportdagi maydon 32767 belgidan oshgan.")
    leading = text.lstrip(" \t\r\n\v\f\ufeff\u200b")
    if csv_format and leading.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def make_csv(rows: list[list]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(HEADERS)
    for row in rows:
        writer.writerow([clean_cell(value, csv_format=True) for value in row])
        if output.tell() > MAX_EXPORT_BYTES:
            raise AppError("export_size", "Eksport hajmi katta. Kamroq kartochka tanlang.", 413)
    return check_size(output.getvalue().encode("utf-8-sig"))


def make_xlsx(rows: list[list]) -> bytes:
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Cards")
    sheet.freeze_panes = "A2"
    sheet.column_dimensions["A"].width = 38
    sheet.column_dimensions["D"].width = 42
    sheet.column_dimensions["E"].width = 42
    for row_number, row in enumerate([HEADERS, *rows]):
        cells = []
        for value in row:
            cell = WriteOnlyCell(sheet, value=clean_cell(value))
            cell.data_type = "s"
            if row_number == 0:
                cell.font = Font(bold=True)
            cells.append(cell)
        sheet.append(cells)
    output = io.BytesIO()
    try:
        workbook.save(output)
    finally:
        workbook.close()
    return check_size(output.getvalue())


def check_size(data: bytes) -> bytes:
    if len(data) > MAX_EXPORT_BYTES:
        raise AppError("export_size", "Eksport hajmi katta. Kamroq kartochka tanlang.", 413)
    return data
