import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import delete, select

from app.ai import replies
from app.ai.provider import ProviderFailure, ProviderResult
from app.clock import utcnow
from app.models import DailyUsage, ReviewReply, ReviewSettings, ReviewTemplate
from app.schemas import ReviewSettingsRequest
from app.services import uzum_accounts, uzum_reviews
from tests.fake_uzum import FakeUzum, review
from tests.helpers import add_user, logged_client

CONNECT = {"api_token": "api-token-seller-1", "login": "seller@uzum.test", "password": "uzum-pass-1"}
NEGATIVE_UZ = uzum_reviews.DEFAULT_TEMPLATES[3][3]


@pytest.fixture
async def uzum(monkeypatch, database, account):
    fake = FakeUzum().install(monkeypatch)
    db, settings = database
    await uzum_accounts.connect(db, settings, account.id, **CONNECT)
    return fake


def later(seconds: int = 60) -> int:
    return int((utcnow() + timedelta(seconds=seconds)).timestamp() * 1000)


async def configure(db, user_id, **values):
    rules = {"auto_reply": True, "text_method": "ai", "empty_method": "template", **values}
    async with db.transaction() as session:
        return await uzum_reviews.update_settings(session, user_id, ReviewSettingsRequest(**rules))


async def stored(db, user_id) -> dict[int, ReviewReply]:
    async with db.transaction() as session:
        return {row.review_id: row for row in await session.scalars(select(ReviewReply).where(ReviewReply.user_id == user_id))}


async def last_message(db, user_id) -> str:
    async with db.transaction() as session:
        return (await session.get(ReviewSettings, user_id)).last_message


async def test_reviews_page_lists_only_own_unanswered_reviews(database, account, uzum):
    uzum.reviews[11] = review(11, 5, "Отличный товар, <b>рекомендую</b>", name="Ольга")
    uzum.reviews[12] = review(12, 4)
    uzum.reviews[13] = review(13, 5, "Zo'r")
    uzum.reviews[13]["reply"] = {"replyId": 1, "content": "Rahmat", "status": "PUBLISHED"}
    uzum.reviews[14] = review(14, 5, "Чужой отзыв", shop_id=88)
    async with logged_client(database, account.email_normalized) as client:
        page = await client.get("/reviews")
        assert page.status_code == 200 and 'id="review-settings-form"' in page.text and 'hx-get="/reviews/pending"' in page.text
        assert page.text.count('class="review-template-form" data-template-id') == len(uzum_reviews.DEFAULT_TEMPLATES)
        pending = (await client.get("/reviews/pending")).text
    assert "Mahsulot 11" in pending and "Mahsulot 12" in pending
    assert "Mahsulot 13" not in pending and "Mahsulot 14" not in pending
    assert "&lt;b&gt;рекомендую&lt;/b&gt;" in pending and "Javob ruscha yoziladi" in pending and "Matnsiz sharh" in pending

    other = await add_user(database, "other@example.com")
    async with logged_client(database, other.email_normalized) as client:
        page = (await client.get("/reviews")).text
        assert "Uzum'ni ulash" in page and 'id="review-pending"' not in page
        assert 'class="notice danger"' in (await client.get("/reviews/pending")).text


async def test_member_drafts_with_template_or_ai_edits_and_sends(database, account, uzum):
    db, _ = database
    uzum.reviews[11] = review(11, 5, "Отличный товар", name="Ольга")
    uzum.reviews[90] = review(90, 5, "Чужой", shop_id=88)
    async with logged_client(database, account.email_normalized) as client:
        await client.get("/reviews")
        template = (await client.post("/api/reviews/11/draft", json={"method": "template"})).json()
        assert template["text"] == "Спасибо, Ольга! Нам очень приятно, что вы выбрали нас и оставили отзыв. С уважением, команда «Shop»."
        assert (template["method"], template["language"], template["status"]) == ("template", "ru", "draft")
        ai = (await client.post("/api/reviews/11/draft", json={"method": "ai"})).json()
        assert (ai["text"], ai["method"]) == ("Спасибо за отзыв! (тест)", "ai")
        assert (await client.post("/api/reviews/90/draft", json={"method": "ai"})).status_code == 404

        sent = await client.post("/api/reviews/11/reply", json={"text": "  Спасибо,   Ольга!  Ждём вас снова. "})
        assert sent.status_code == 200, sent.text
        assert (sent.json()["status"], sent.json()["method"], sent.json()["moderation_label"]) == ("sent", "ai", "Moderatsiyada")
        again = await client.post("/api/reviews/11/reply", json={"text": "Ещё раз"})
        assert (again.status_code, again.json()["error"]["code"]) == (409, "review_already_replied")
        history = (await client.get("/reviews")).text
    assert uzum.replies == [(11, "Спасибо, Ольга! Ждём вас снова.")]
    assert "Ждём вас снова" in history and "Moderatsiyada" in history
    async with db.transaction() as session:
        assert (await session.scalars(select(DailyUsage))).one().reply_ai_calls == 1


async def test_templates_are_validated_and_private(database, account, uzum):
    other = await add_user(database, "other@example.com")
    body = {"name": "Rahmat", "rating_min": 5, "rating_max": 5, "text_uz": "Rahmat, {ism}! «{mahsulot}» uchun.",
            "text_ru": "Спасибо, {ism}!"}
    async with logged_client(database, account.email_normalized) as client:
        bad = (await client.post("/api/review-templates", json={**body, "text_ru": "Спасибо, {name}!"})).json()["error"]
        assert (bad["code"], list(bad["fields"])) == ("review_template_invalid", ["text_ru"])
        assert (await client.post("/api/review-templates", json={**body, "rating_max": 2})).status_code == 422
        created = await client.post("/api/review-templates", json=body)
        assert created.status_code == 201 and created.json()["stars"] == "5★"
        template_id = created.json()["id"]
        updated = await client.put(f"/api/review-templates/{template_id}", json={**body, "rating_min": 3})
        assert updated.json()["stars"] == "3–5★"
        uzum.reviews[21] = review(21, 4, "", name="Dilnoza")
        draft = await client.post("/api/reviews/21/draft", json={"method": "template", "template_id": template_id})
        assert draft.json()["text"] == "Rahmat, Dilnoza! «Mahsulot 21» uchun."
    async with logged_client(database, other.email_normalized) as client:
        assert (await client.delete(f"/api/review-templates/{template_id}")).status_code == 404
    async with logged_client(database, account.email_normalized) as client:
        assert (await client.delete(f"/api/review-templates/{template_id}")).status_code == 204


async def test_auto_reply_answers_new_reviews_once_in_their_language(database, account, uzum):
    db, settings = database
    uzum.reviews[30] = review(30, 5, "Старый отзыв", created_ms=1_600_000_000_000)
    await configure(db, account.id)
    uzum.reviews[31] = review(31, 5, "Очень удобный", name="Ольга", created_ms=later())
    uzum.reviews[32] = review(32, 2, "", name="Aziz", created_ms=later())
    uzum.reviews[33] = review(33, 1, "Juda yomon, singan keldi", anonymous=True, created_ms=later())
    # Uzum ismi yo'q mijozni «Безымянный» deb qaytaradi: javobda bunday "ism" bo'lmasligi kerak.
    uzum.reviews[34] = review(34, 5, "", name="Безымянный", created_ms=later())

    assert await uzum_reviews.run_due(db, settings) == 1
    assert dict(uzum.replies) == {31: "Спасибо за отзыв! (тест)", 32: NEGATIVE_UZ.replace("{ism}", "Aziz"),
                                  33: "Fikringiz uchun rahmat! (sinov)",
                                  34: uzum_reviews.DEFAULT_TEMPLATES[0][3].replace(", {ism}", "")}
    rows = await stored(db, account.id)
    assert {key: (row.method, row.source, row.language) for key, row in rows.items()} == {
        31: ("ai", "auto", "ru"), 32: ("template", "auto", "uz"), 33: ("ai", "auto", "uz"), 34: ("template", "auto", "uz")}
    assert await last_message(db, account.id) == "4 ta javob yuborildi."

    await uzum_reviews.run_due(db, settings)
    assert len(uzum.replies) == 4
    await configure(db, account.id, backlog=True)
    await uzum_reviews.run_due(db, settings)
    assert uzum.replies[-1][0] == 30


async def test_auto_reply_falls_back_to_template_and_stops_after_repeated_failures(database, account, uzum, monkeypatch):
    db, settings = database

    async def unavailable(*args):
        raise ProviderFailure("provider_unavailable")

    monkeypatch.setattr(replies, "generate", unavailable)
    await configure(db, account.id, backlog=True)
    uzum.reviews[40] = review(40, 5, "Zo'r mahsulot", name="Jasur")
    uzum.reviews[41] = review(41, 3, "Normal")
    uzum.reply_errors[41] = httpx.Response(400, json={"errors": [{"code": "bad-request-001"}]})
    for _ in range(4):
        await uzum_reviews.run_due(db, settings)

    rows = await stored(db, account.id)
    assert (rows[40].status, rows[40].method) == ("sent", "template")
    assert dict(uzum.replies)[40].startswith("Assalomu alaykum, Jasur!")
    assert (rows[41].status, rows[41].attempts) == ("failed", uzum_reviews.MAX_ATTEMPTS)
    assert "bad-request-001" in rows[41].error
    assert len(uzum.requests("/reply/create")) == 1 + uzum_reviews.MAX_ATTEMPTS


async def test_missing_template_is_recorded_instead_of_retried_forever(database, account, uzum):
    db, settings = database
    await configure(db, account.id, text_method="template", backlog=True)
    async with db.transaction() as session:
        await session.execute(delete(ReviewTemplate))
    uzum.reviews[60] = review(60, 4, "Yaxshi")
    for _ in range(4):
        await uzum_reviews.run_due(db, settings)
    row = (await stored(db, account.id))[60]
    assert (row.status, row.attempts) == ("failed", uzum_reviews.MAX_ATTEMPTS) and "shablon" in row.error
    assert uzum.replies == []


async def test_auto_reply_skips_textless_and_leaves_fresh_drafts_alone(database, account, uzum):
    db, settings = database
    await configure(db, account.id, text_method="template", empty_method="skip", backlog=True)
    uzum.reviews[50] = review(50, 5)
    uzum.reviews[51] = review(51, 5, "Super")
    async with logged_client(database, account.email_normalized) as client:
        assert (await client.post("/api/reviews/51/draft", json={"method": "template"})).status_code == 200
    await uzum_reviews.run_due(db, settings)
    assert uzum.replies == []
    assert await last_message(db, account.id) == "0 ta javob yuborildi, 1 ta matnsiz sharh o'tkazib yuborildi."

    async with db.transaction() as session:
        (await session.scalars(select(ReviewReply))).one().updated_at = utcnow() - uzum_reviews.DRAFT_HOLD - timedelta(minutes=1)
    await uzum_reviews.run_due(db, settings)
    assert [item[0] for item in uzum.replies] == [51]


async def test_moderation_status_is_synced_from_uzum(database, account, uzum):
    db, settings = database
    await configure(db, account.id, text_method="template", backlog=True)
    uzum.reviews[70] = review(70, 5, "Rahmat")
    await uzum_reviews.run_due(db, settings)
    assert (await stored(db, account.id))[70].moderation == "CREATED"
    uzum.reviews[70]["replyStatus"] = "PUBLISHED"
    await uzum_reviews.run_due(db, settings)
    assert (await stored(db, account.id))[70].moderation == "PUBLISHED"
    async with logged_client(database, account.email_normalized) as client:
        assert "E&#39;lon qilindi" in (await client.get("/reviews")).text


async def test_ai_reply_limit_and_public_text_checks(database, account, uzum):
    _, settings = database
    settings.review_ai_daily_limit = 1
    uzum.reviews[80] = review(80, 5, "Хорошо")
    async with logged_client(database, account.email_normalized) as client:
        assert (await client.post("/api/reviews/80/draft", json={"method": "ai"})).status_code == 200
        limited = await client.post("/api/reviews/80/draft", json={"method": "ai"})
        assert (limited.status_code, limited.json()["error"]["code"]) == (429, "review_ai_limit")

    for unsafe in ["Пишите нам: shop@mail.ru", "Подробнее на https://example.com", "Звоните +998 90 123 45 67",
                   "<b>Спасибо</b> за отзыв", "Rahmat"]:
        with pytest.raises(ValueError):
            replies.parse(ProviderResult(json.dumps({"reply": unsafe}, ensure_ascii=False)))
    assert replies.parse(ProviderResult(json.dumps({"reply": " «Спасибо за отзыв!» "}))) == "Спасибо за отзыв!"


@pytest.mark.parametrize(("text", "language"), [
    ("Отличный товар", "ru"), ("Жуда зўр маҳсулот", "uz"), ("Zo'r mahsulot", "uz"), ("", "uz"),
    ("Super! Отлично, спасибо", "ru"),
])
def test_review_language(text, language):
    assert uzum_reviews.review_language({"content": text}) == language


@pytest.mark.parametrize(("text", "expected"), [
    ("Assalomu alaykum, {ism}! Rahmat.", "Assalomu alaykum! Rahmat."),
    ("Здравствуйте, {ism}! Спасибо за отзыв.", "Здравствуйте! Спасибо за отзыв."),
    ("{ism}, rahmat!", "Rahmat!"),
    ("Hurmatli {ism}, xaridingiz uchun rahmat!", "Xaridingiz uchun rahmat!"),
    ("Уважаемая {ism}! Спасибо за отзыв.", "Спасибо за отзыв."),
    ("Rahmat {ism}!", "Rahmat!"),
])
@pytest.mark.parametrize("customer", [{"customerName": "Безымянный"}, {"customerName": "***"}, {"customerName": ""},
                                      {"customerName": "Ольга", "anonymous": True}])
def test_nameless_customers_are_not_addressed(text, expected, customer):
    template = ReviewTemplate(name="Sinov", rating_min=1, rating_max=5, text_uz=text, text_ru=text)
    assert uzum_reviews.render(template, customer, "uz") == expected
    assert uzum_reviews.render(template, {"customerName": "Ольга"}, "uz") == text.replace("{ism}", "Ольга")
