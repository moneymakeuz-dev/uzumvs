"""Uzum OpenAPI va seller kabineti API'sining xotiradagi o'xshashi (httpx.MockTransport uchun).

Haqiqiy Uzum kabi: bitta akkauntga bitta access token beriladi, login amaldagi tokenni qaytaradi,
`rotate()` sotuvchining o'z brauzeri refresh qilganini taqlid qiladi (eski token 401 beradi).
"""
import json
import re
from typing import Any
from urllib.parse import parse_qs

import httpx

from app.services import uzum_cabinet

HOUR_MS = 3_600_000


def invoice(invoice_id: int, slot_ms: int | None, status: str = "CREATED", remaining: int = 2) -> dict[str, Any]:
    return {"id": invoice_id, "invoiceNumber": f"00{invoice_id}",
            "invoiceStatus": {"value": status, "text": "Создана" if status == "CREATED" else "Принята на складе"},
            "remainingAmountOfUpdates": remaining, "stock": {"id": 9, "title": "Toshkent", "poolSource": "POOL"},
            "timeSlotReservation": {"timeFrom": slot_ms, "timeTo": slot_ms + 2 * HOUR_MS} if slot_ms else None}


class FakeUzum:
    def __init__(self) -> None:
        # API tokeni -> do'konlar; "expired" Uzum'dagi "Token expired" javobini beradi.
        self.api_tokens: dict[str, list[dict[str, Any]]] = {"api-token-seller-1": [{"id": 77, "name": "Shop"}]}
        self.expired: set[str] = set()
        self.passwords = {"seller@uzum.test": "uzum-pass-1"}
        self.shop_access = {"seller@uzum.test": {"77"}}
        self.tokens: dict[str, str] = {}
        self.invoices: dict[int, dict[str, Any]] = {}
        self.slots: list[dict[str, Any]] = []
        self.set_errors: dict[int, httpx.Response] = {}
        self.characteristics: list[dict[str, Any]] = []
        self.openapi_down = False
        self.logins: list[str] = []
        self.calls: list[tuple[str, str, Any]] = []
        self.generation = 0

    def add_seller(self, login: str, password: str, api_token: str, shops: list[dict[str, Any]]) -> None:
        self.passwords[login] = password
        self.api_tokens[api_token] = shops
        self.shop_access[login] = {str(shop["id"]) for shop in shops}

    def rotate(self, login: str = "seller@uzum.test") -> None:
        """Sotuvchi o'z brauzerida refresh qildi: eski access token endi 401 qaytaradi."""
        self.generation += 1
        self.tokens[login] = f"access-{login}-{self.generation}"

    def install(self, monkeypatch) -> "FakeUzum":
        monkeypatch.setattr(uzum_cabinet, "transport", httpx.MockTransport(self.handle))
        return self

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = json.loads(request.content) if request.content and request.headers.get("content-type", "").startswith("application/json") else None
        self.calls.append((request.method, path, payload))
        if path == "/api/seller-openapi/v1/shops":
            return self.openapi(request)
        if path == "/api/oauth/token":
            return self.login(request)
        user = next((login for login, token in self.tokens.items() if request.headers.get("authorization") == f"Bearer {token}"), None)
        if user is None:
            return httpx.Response(401, json={"errors": [{"code": "unauthorized-001", "message": "Unauthorized"}]})
        match = re.fullmatch(r"/api/seller/shop/(\d+)(/.*)", path)
        if not match or match.group(1) not in self.shop_access.get(user, set()):
            return httpx.Response(403, json={"errors": [{"code": "forbidden-001", "message": "Forbidden"}]})
        return self.cabinet(request.method, match.group(2), request, payload)

    def openapi(self, request: httpx.Request) -> httpx.Response:
        if self.openapi_down:
            return httpx.Response(503, text="Service Unavailable")
        token = request.headers.get("authorization", "")
        if token in self.expired:
            return httpx.Response(403, json={"errors": [{"code": "forbidden-001", "message": "Token expired"}]})
        if token not in self.api_tokens:
            return httpx.Response(403, json={"errors": [{"code": "forbidden-001", "message": "Token not found"}]})
        return httpx.Response(200, json=self.api_tokens[token])

    def login(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == uzum_cabinet.CLIENT_AUTH
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        self.logins.append(form["username"])
        if form.get("grant_type") != "password" or self.passwords.get(form["username"]) != form.get("password"):
            return httpx.Response(400, json={"errors": [{"code": "bad-request-001", "detailMessage":
                                                         '{"error":"invalid_grant","error_description":"Bad credentials"}'}]})
        if form["username"] not in self.tokens:
            self.rotate(form["username"])
        return httpx.Response(200, json={"access_token": self.tokens[form["username"]], "refresh_token": "refresh",
                                         "expires_in": 2_592_000, "token_type": "bearer", "scope": "read write"})

    def cabinet(self, method: str, path: str, request: httpx.Request, payload: Any) -> httpx.Response:
        if method == "GET" and path == "/invoice":
            statuses = request.url.params.get("statuses")
            items = [item for item in self.invoices.values() if not statuses or item["invoiceStatus"]["value"] == statuses]
            page, size = int(request.url.params["page"]), int(request.url.params["size"])
            return httpx.Response(200, json=items[page * size:(page + 1) * size])
        if method == "GET" and (found := re.fullmatch(r"/invoice/(\d+)", path)):
            item = self.invoices.get(int(found.group(1)))
            return httpx.Response(200, json=item) if item else httpx.Response(404, json={"errors": [{"code": "not-found"}]})
        if method == "POST" and path == "/v2/invoice/time-slot/get":
            return httpx.Response(200, json={"payload": {"timeSlots": self.slots}})
        if method == "POST" and path == "/v2/invoice/time-slot/set":
            invoice_id = payload["invoiceIds"][0]
            if invoice_id in self.set_errors:
                return self.set_errors[invoice_id]
            slot = payload["timeFrom"]
            self.invoices[invoice_id]["timeSlotReservation"] = {"timeFrom": slot, "timeTo": slot + 2 * HOUR_MS}
            self.invoices[invoice_id]["remainingAmountOfUpdates"] -= 1
            return httpx.Response(200, json={"payload": None})
        if method == "GET" and path == "/product/required-characteristics":
            return httpx.Response(200, json={"characteristics": self.characteristics})
        return httpx.Response(404, json={"errors": [{"code": "not-found"}]})

    def requests(self, suffix: str) -> list[tuple[str, str, Any]]:
        return [call for call in self.calls if call[1].endswith(suffix)]
