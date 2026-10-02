import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

import httpx

from app.config import ROOT, Settings
from app.errors import AppError

SELLER_API = "https://api-seller.uzum.uz/api/seller-openapi/v1/shops"
UPLOAD_TIMEOUT_SECONDS = 240


async def list_shops(settings: Settings) -> list[dict[str, str]]:
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
    if settings.app_env == "production":
        raise AppError("uzum_upload_local_only", "Uzum brauzer yuklashi hozircha faqat lokal ilovada ishlaydi.", 503)
    email = settings.uzum_seller_email.get_secret_value()
    password = settings.uzum_seller_password.get_secret_value()
    if not email or not password:
        raise AppError("uzum_login_not_configured", "Uzum login va paroli sozlanmagan.", 503)
    shops = await list_shops(settings)
    if shop_id not in {shop["id"] for shop in shops}:
        raise AppError("uzum_shop_invalid", "Tanlangan do'kon API ro'yxatida topilmadi.", 422)
    node = shutil.which("node")
    script = ROOT / "scripts" / "uzum_upload.mjs"
    if not node or not script.is_file():
        raise AppError("uzum_browser_unavailable", "Mahalliy Playwright yuklagichi mavjud emas.", 503)
    runtime_dir = ROOT / "var"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    profile = runtime_dir / "uzum-browser-profile"
    with tempfile.TemporaryDirectory(prefix="uzum-upload-", dir=runtime_dir) as temporary:
        template = Path(temporary) / "product-template.xlsm"
        template.write_bytes(data)
        environment = os.environ.copy()
        environment.update({"UZUM_SELLER_EMAIL": email, "UZUM_SELLER_PASSWORD": password,
                            "UZUM_BROWSER_PROFILE": str(profile)})
        try:
            process = await asyncio.create_subprocess_exec(
                node, str(script), str(template), shop_id,
                cwd=ROOT, env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=UPLOAD_TIMEOUT_SECONDS)
        except TimeoutError:
            if "process" in locals() and process.returncode is None:
                process.kill()
                await process.wait()
            raise AppError("uzum_upload_timeout", "Yuklash javobi olinmadi. Uzum kabinetini tekshiring va darhol qayta yubormang.", 504) from None
        except OSError:
            raise AppError("uzum_browser_unavailable", "Playwright brauzerini ishga tushirib bo'lmadi.", 503) from None
        try:
            result = json.loads(stdout.decode("utf-8").splitlines()[-1])
        except (UnicodeDecodeError, IndexError, json.JSONDecodeError):
            result = {}
        if process.returncode != 0 or result.get("status") != "accepted":
            code = result.get("code")
            if code == "auth_timeout":
                raise AppError("uzum_auth_timeout", "Uzum kirishi, CAPTCHA yoki SMS tasdig'i vaqtida tugamadi.", 408)
            if code == "upload_rejected":
                raise AppError("uzum_upload_rejected", "Uzum faylni qabul qilmadi; import oynasidagi xatoni tekshiring.", 422)
            raise AppError("uzum_upload_failed", "Uzum faylini yuborib bo'lmadi. Kabinetdagi holatni tekshiring.", 502)
        return {"status": "accepted", "shop_id": shop_id, "message": "Uzum faylni qabul qildi; mahsulot importi bir necha daqiqa olishi mumkin."}