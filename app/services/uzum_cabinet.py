"""Uzum bilan HTTP orqali ishlash: OpenAPI (a'zoning API tokeni) va seller kabineti API'si (a'zoning logini).

Kabinet OAuth2 password grant ishlatadi va bitta akkauntning barcha sessiyalariga bitta access token beradi.
Kimdir refresh qilsa, eski token hamma joyda bekor bo'ladi (401). Shuning uchun bu yerda refresh ishlatilmaydi:
401 da qayta login qilinadi — u amaldagi tokenni qaytaradi va sotuvchining o'z brauzeridagi sessiyasini buzmaydi.
"""
import re
from typing import Any

import httpx

from app.errors import AppError

API = "https://api-seller.uzum.uz/api"
OPENAPI = "https://api-seller.uzum.uz/api/seller-openapi"
# Kabinet frontendidagi ochiq OAuth client (b2b-front:clientSecret) — har bir brauzerga yuboriladi, sir emas.
CLIENT_AUTH = "Basic YjJiLWZyb250OmNsaWVudFNlY3JldA=="
PAGE_SIZE = 20
MAX_RELOGINS = 2
# Testlar httpx.MockTransport qo'yadi.
transport: httpx.AsyncBaseTransport | None = None


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10), transport=transport,
                             headers={"Accept": "application/json"})


def unavailable() -> AppError:
    return AppError("uzum_unavailable", "Uzum javob bermadi. Birozdan keyin qayta urinib ko'ring.", 502)


def error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return errors[0].get("code")
    return None


def login_candidates(login: str) -> list[str]:
    """Email o'zgarishsiz; telefon raqami kabinet qabul qiladigan ikki ko'rinishda sinab ko'riladi."""
    login = login.strip()
    if "@" in login:
        return [login]
    digits = re.sub(r"\D", "", login)
    return [digits, f"+{digits}"] if digits else [login]


def login_hint(login: str) -> str:
    login = login.strip()
    if "@" in login:
        name, _, domain = login.partition("@")
        return f"{name[:2]}***@{domain}"[:80]
    digits = re.sub(r"\D", "", login)
    return f"+{digits[:5]}***{digits[-2:]}" if len(digits) > 7 else "***"


async def fetch_shops(api_token: str) -> list[dict[str, str]]:
    """OpenAPI: token egasining do'konlari. Token noto'g'ri/eskirgan bo'lsa uzum_token_invalid."""
    try:
        async with client() as http:
            response = await http.get(f"{OPENAPI}/v1/shops", headers={"Authorization": api_token})
    except httpx.HTTPError:
        raise unavailable() from None
    if response.status_code == 401 or (response.status_code == 403 and error_code(response) == "forbidden-001"):
        raise AppError("uzum_token_invalid", "Uzum API tokeni noto'g'ri yoki muddati tugagan. Kabinetda yangi token yarating.",
                       422, fields={"api_token": "Tokenni tekshiring."})
    try:
        payload = response.json() if response.status_code == 200 else None
    except ValueError:
        payload = None
    if not isinstance(payload, list):
        raise unavailable()
    shops = [{"id": str(item["id"]), "name": item["name"][:160]} for item in payload
             if isinstance(item, dict) and item.get("id") is not None and isinstance(item.get("name"), str)]
    if not shops:
        raise AppError("uzum_shop_missing", "Bu token bilan birorta do'kon topilmadi.", 422, fields={"api_token": "Tokenni tekshiring."})
    return sorted(shops, key=lambda item: item["name"].casefold())


async def password_login(http: httpx.AsyncClient, username: str, password: str) -> str:
    try:
        response = await http.post(f"{API}/oauth/token", headers={"Authorization": CLIENT_AUTH},
                                   data={"grant_type": "password", "username": username, "password": password, "referer": ""})
    except httpx.HTTPError:
        raise unavailable() from None
    if response.status_code == 200:
        try:
            token = response.json().get("access_token")
        except (ValueError, AttributeError):
            token = None
        if isinstance(token, str) and token:
            return token
        raise unavailable()
    if response.status_code == 429 or "throttl" in response.text.casefold():
        raise AppError("uzum_login_throttled", "Uzum kirish urinishlari ko'payib ketdi. 15 daqiqadan keyin qayta urinib ko'ring.", 429)
    if response.status_code in {400, 401} and "invalid_grant" in response.text:
        raise AppError("uzum_login_invalid", "Uzum kabineti logini yoki paroli noto'g'ri.", 422,
                       fields={"password": "Login va parolni tekshiring."})
    raise unavailable()


class Cabinet:
    """Bitta a'zoning kabinet sessiyasi. `token` o'zgarsa, chaqiruvchi uni saqlab qo'yadi."""

    def __init__(self, http: httpx.AsyncClient, username: str, password: str, token: str | None = None) -> None:
        self.http, self.username, self.password, self.token = http, username, password, token
        self.relogins = 0

    async def login(self) -> str:
        self.token = await password_login(self.http, self.username, self.password)
        return self.token

    async def call(self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None) -> httpx.Response:
        if not self.token:
            await self.login()
        response = await self._send(method, path, json, params)
        # 401 da Uzum so'rovni bajarmaydi: qayta login qilib, so'rov bir marta qaytariladi.
        if response.status_code == 401 and self.relogins < MAX_RELOGINS:
            self.relogins += 1
            await self.login()
            response = await self._send(method, path, json, params)
        return response

    async def _send(self, method: str, path: str, json: Any, params: dict[str, Any] | None) -> httpx.Response:
        try:
            return await self.http.request(method, f"{API}{path}", json=json, params=params,
                                           headers={"Authorization": f"Bearer {self.token}", "Accept-Language": "ru-RU"})
        except httpx.HTTPError:
            raise unavailable() from None


def body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


async def can_open_shop(cabinet: Cabinet, shop_id: str) -> bool:
    response = await cabinet.call("GET", f"/seller/shop/{shop_id}/invoice", params={"page": 0, "size": 1})
    return response.status_code == 200


async def list_invoices(cabinet: Cabinet, shop_id: str) -> list[dict[str, Any]]:
    """«Создана» holatidagi FBO yuk xatlari (kabinetdagi ro'yxat bilan bir xil)."""
    invoices: list[dict[str, Any]] = []
    for page in range(5):
        response = await cabinet.call("GET", f"/seller/shop/{shop_id}/invoice",
                                      params={"page": page, "size": PAGE_SIZE, "statuses": "CREATED"})
        items = body(response)
        if response.status_code != 200 or not isinstance(items, list):
            raise AppError("uzum_invoices_unavailable", "Uzum kabinetidan yuk xatlarini olib bo'lmadi.", 502)
        invoices += [item for item in items if isinstance(item, dict)]
        if len(items) < PAGE_SIZE:
            break
    return invoices


async def required_characteristics(cabinet: Cabinet, shop_id: str, category_id: int) -> list[dict[str, Any]]:
    response = await cabinet.call("GET", f"/seller/shop/{shop_id}/product/required-characteristics",
                                  params={"categoryId": category_id})
    payload = body(response)
    if response.status_code != 200 or not isinstance(payload, dict):
        raise AppError("uzum_characteristics_unavailable", "Kategoriya majburiy xarakteristikalarini olib bo'lmadi.", 502)
    items = payload.get("characteristics")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
