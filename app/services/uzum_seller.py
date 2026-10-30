import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from app.config import ROOT, Settings
from app.errors import AppError

SELLER_API = "https://api-seller.uzum.uz/api/seller-openapi/v1/shops"
UPLOAD_TIMEOUT_SECONDS = 240
TEMPLATE_TIMEOUT_SECONDS = 240
TEMPLATE_CACHE = ROOT / "var" / "uzum-template" / "template.xlsm"
TEMPLATE_MAX_AGE_SECONDS = 24 * 3600
REQUIRED_CACHE = ROOT / "var" / "uzum-template" / "required-characteristics.json"
# Uzum OpenAPI vaqti-vaqti bilan 503 beradi; oxirgi muvaffaqiyatli do'konlar ro'yxati shu yerda saqlanadi.
SHOPS_CACHE = ROOT / "var" / "uzum-template" / "shops.json"
_browser_lock = asyncio.Lock()
logger = logging.getLogger(__name__)


async def get_template(settings: Settings) -> bytes:
    """Uzum kabinetidagi rasmiy shablonni (“Скачать файл”) o'zi yuklab, bir kunga keshlaydi."""
    if TEMPLATE_CACHE.is_file() and time.time() - TEMPLATE_CACHE.stat().st_mtime < TEMPLATE_MAX_AGE_SECONDS:
        return TEMPLATE_CACHE.read_bytes()
    shops = await list_shops(settings)
    shop_id = settings.uzum_shop_id or shops[0]["id"]
    TEMPLATE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    target = TEMPLATE_CACHE.with_suffix(".part")
    target.unlink(missing_ok=True)
    returncode, result = await run_script(settings, "download", target, shop_id, TEMPLATE_TIMEOUT_SECONDS)
    if returncode != 0 or result.get("status") != "downloaded" or not target.is_file():
        target.unlink(missing_ok=True)
        if result.get("code") == "auth_timeout":
            raise AppError("uzum_auth_timeout", "Uzum kirishi, CAPTCHA yoki SMS tasdig'i vaqtida tugamadi.", 408)
        if TEMPLATE_CACHE.is_file():
            return TEMPLATE_CACHE.read_bytes()
        raise AppError("uzum_template_unavailable", "Uzum shablonini kabinetdan olib bo'lmadi. Faylni qo'lda tanlang.", 502)
    target.replace(TEMPLATE_CACHE)
    return TEMPLATE_CACHE.read_bytes()


async def required_characteristics(settings: Settings, category_id: int) -> list[dict]:
    """Kategoriya uchun Uzum majburiy xarakteristikalari (Rang, O'lcham...); shablonda yo'q, kabinet API'sidan olinadi."""
    cache = json.loads(REQUIRED_CACHE.read_text(encoding="utf-8")) if REQUIRED_CACHE.is_file() else {}
    entry = cache.get(str(category_id))
    if entry and time.time() - entry["at"] < TEMPLATE_MAX_AGE_SECONDS:
        return entry["items"]
    shop_id = settings.uzum_shop_id or (await list_shops(settings))[0]["id"]
    returncode, result = await run_script(settings, "required", REQUIRED_CACHE, shop_id, TEMPLATE_TIMEOUT_SECONDS,
                                          {"UZUM_CATEGORY_ID": str(category_id)})
    if returncode != 0 or result.get("status") != "ok":
        raise AppError("uzum_characteristics_unavailable", "Kategoriya majburiy xarakteristikalarini olib bo'lmadi.", 502)
    items = []
    for item in result.get("characteristics") or []:
        title = (item.get("characteristicTitle") or {}).get("ru") if isinstance(item, dict) else None
        if item.get("requiredType") == "REQUIRED" and isinstance(title, str):
            field = "color" if title == "Цвет" else "size" if title.startswith("Размер") else None
            items.append({"title": title, "field": field})
    cache[str(category_id)] = {"at": time.time(), "items": items}
    REQUIRED_CACHE.parent.mkdir(parents=True, exist_ok=True)
    REQUIRED_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return items


async def run_script(settings: Settings, mode: str, path: Path, shop_id: str, limit_seconds: int,
                      extra_env: dict[str, str] | None = None) -> tuple[int, dict]:
    if not settings.uzum_browser:
        raise AppError("uzum_browser_disabled", "Uzum brauzer yuklashi bu serverda o'chirilgan.", 503)
    email = settings.uzum_seller_email.get_secret_value()
    password = settings.uzum_seller_password.get_secret_value()
    if not email or not password:
        raise AppError("uzum_login_not_configured", "Uzum login va paroli sozlanmagan.", 503)
    node = shutil.which("node")
    script = ROOT / "scripts" / "uzum_upload.mjs"
    if not node or not script.is_file():
        raise AppError("uzum_browser_unavailable", "Mahalliy Playwright yuklagichi mavjud emas.", 503)
    environment = os.environ.copy()
    environment.update({"UZUM_SELLER_EMAIL": email, "UZUM_SELLER_PASSWORD": password, "UZUM_MODE": mode,
                        "UZUM_BROWSER_PROFILE": str(ROOT / "var" / "uzum-browser-profile"),
                        "UZUM_DEBUG_DIR": str(ROOT / "var" / "uzum-debug"), **(extra_env or {})})
    command = [node, str(script), str(path), shop_id]
    if os.name != "nt" and not environment.get("DISPLAY"):
        # Serverda ekran yo'q: ko'rinadigan brauzer virtual displey (Xvfb) ichida ishlaydi.
        xvfb = shutil.which("xvfb-run")
        if not xvfb:
            raise AppError("uzum_browser_unavailable", "Serverda virtual displey (xvfb) o'rnatilmagan.", 503)
        command = [xvfb, "-a", *command]
        home = ROOT / "var" / "browser-home"
        home.mkdir(parents=True, exist_ok=True)
        environment["HOME"] = str(home)
    async with _browser_lock:
        try:
            # Veb-server Windows'da selector event loop ishlatadi, u asyncio subprocess'ni qo'llamaydi.
            completed = await asyncio.to_thread(
                subprocess.run, command,
                cwd=ROOT, env=environment, capture_output=True, timeout=limit_seconds, check=False,
            )
        except subprocess.TimeoutExpired:
            raise AppError("uzum_upload_timeout", "Uzum javobi olinmadi. Kabinetni tekshiring va darhol qayta yubormang.", 504) from None
        except OSError:
            raise AppError("uzum_browser_unavailable", "Playwright brauzerini ishga tushirib bo'lmadi.", 503) from None
    try:
        result = json.loads(completed.stdout.decode("utf-8").splitlines()[-1])
    except (UnicodeDecodeError, IndexError, json.JSONDecodeError):
        result = {}
    if completed.returncode != 0:
        logger.warning("uzum_script_failed mode=%s code=%s stderr=%s", mode, result.get("code"),
                       completed.stderr.decode("utf-8", "replace").strip()[:500])
    return completed.returncode, result


async def list_shops(settings: Settings) -> list[dict[str, str]]:
    try:
        shops = await _fetch_shops(settings)
    except AppError as error:
        if error.code != "uzum_api_unavailable":
            raise
        cached = json.loads(SHOPS_CACHE.read_text(encoding="utf-8")) if SHOPS_CACHE.is_file() else []
        if cached:
            return cached
        if settings.uzum_shop_id:
            return [{"id": settings.uzum_shop_id, "name": f"Do'kon {settings.uzum_shop_id}"}]
        raise
    SHOPS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SHOPS_CACHE.write_text(json.dumps(shops, ensure_ascii=False), encoding="utf-8")
    return shops


async def _fetch_shops(settings: Settings) -> list[dict[str, str]]:
    token = settings.uzum_seller_api_key.get_secret_value()
    if not token:
        raise AppError("uzum_api_not_configured", "Uzum API kaliti sozlanmagan.", 503)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(SELLER_API, headers={"Authorization": token})
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        raise AppError("uzum_api_unavailable", "Uzum do'konlari ro'yxatini olib bo'lmadi.", 502) from None
    if not isinstance(payload, list):
        raise AppError("uzum_api_invalid", "Uzum do'konlari javobi kutilgan formatda emas.", 502)
    shops = []
    for item in payload:
        if isinstance(item, dict) and item.get("id") is not None and isinstance(item.get("name"), str):
            shops.append({"id": str(item["id"]), "name": item["name"][:160]})
    if not shops:
        raise AppError("uzum_shop_missing", "Uzum API akkaunt uchun do'kon topmadi.", 404)
    return sorted(shops, key=lambda item: item["name"].casefold())


async def upload_template(settings: Settings, data: bytes, shop_id: str) -> dict[str, str]:
    if not settings.uzum_browser:
        raise AppError("uzum_browser_disabled", "Uzum brauzer yuklashi bu serverda o'chirilgan.", 503)
    email = settings.uzum_seller_email.get_secret_value()
    password = settings.uzum_seller_password.get_secret_value()
    if not email or not password:
        raise AppError("uzum_login_not_configured", "Uzum login va paroli sozlanmagan.", 503)
    shops = await list_shops(settings)
    if shop_id not in {shop["id"] for shop in shops}:
        raise AppError("uzum_shop_invalid", "Tanlangan do'kon API ro'yxatida topilmadi.", 422)
    runtime_dir = ROOT / "var"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="uzum-upload-", dir=runtime_dir) as temporary:
        template = Path(temporary) / "product-template.xlsm"
        template.write_bytes(data)
        returncode, result = await run_script(settings, "upload", template, shop_id, UPLOAD_TIMEOUT_SECONDS)
        if returncode != 0 or result.get("status") != "accepted":
            code = result.get("code")
            if code == "auth_timeout":
                raise AppError("uzum_auth_timeout", "Uzum kirishi, CAPTCHA yoki SMS tasdig'i vaqtida tugamadi.", 408)
            if code == "import_errors":
                raise AppError("uzum_import_errors", f"Uzum faylni rad etdi: {str(result.get('message') or '')[:600]}", 422)
            if code == "upload_rejected":
                raise AppError("uzum_upload_rejected", "Uzum faylni qabul qilmadi; import oynasidagi xatoni tekshiring.", 422)
            if code == "unknown_outcome":
                return {"status": "sent", "shop_id": shop_id,
                        "message": "Fayl Uzum'ga yuborildi, lekin natija aniqlanmadi. Kabinetdagi \u201c\u041c\u043e\u0438 \u0442\u043e\u0432\u0430\u0440\u044b\u201d ni tekshiring."}
            raise AppError("uzum_upload_failed", "Uzum faylini yuborib bo'lmadi. Kabinetdagi holatni tekshiring.", 502)
        return {"status": "accepted", "shop_id": shop_id, "message": "Uzum faylni qabul qildi; mahsulot importi bir necha daqiqa olishi mumkin."}