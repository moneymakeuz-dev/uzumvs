import asyncio
import re
from pathlib import Path

import httpx

from app.errors import AppError

HOST_API = "https://uguu.se/upload"
PUBLIC_URL = re.compile(r"^https://([a-z0-9-]+\.)?uguu\.se/[A-Za-z0-9._-]+$")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}


async def publish_images(paths: list[Path]) -> list[str]:
    """Uzum shabloni faqat ommaviy HTTPS havolalarni qabul qiladi; uguu.se fayllarni taxminan 3 soat saqlaydi, import esa daqiqalarda bajariladi."""
    if not paths:
        raise AppError("uzum_no_images", "Kartochkada Uzum uchun rasm yo'q.", 422)
    try:
        async with httpx.AsyncClient(timeout=60, headers=HEADERS) as client:
            return list(await asyncio.gather(*(_upload(client, path) for path in paths)))
    except (httpx.HTTPError, ValueError):
        raise AppError("image_host_unavailable", "Rasmlarni ommaviy hostga yuklab bo'lmadi.", 502) from None


async def _upload(client: httpx.AsyncClient, path: Path) -> str:
    data = await asyncio.to_thread(path.read_bytes)
    response = await client.post(HOST_API, files={"files[]": (path.name, data, "image/jpeg")})
    response.raise_for_status()
    files = response.json().get("files") or []
    url = files[0].get("url") if files and isinstance(files[0], dict) else None
    if not isinstance(url, str) or not PUBLIC_URL.fullmatch(url):
        raise AppError("image_host_invalid", "Rasm hosti kutilmagan javob qaytardi.", 502)
    return url
