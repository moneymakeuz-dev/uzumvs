import asyncio
import re
from pathlib import Path

import httpx

from app.errors import AppError

HOST_API = "https://catbox.moe/user/api.php"
PUBLIC_URL = re.compile(r"^https://files\.catbox\.moe/[A-Za-z0-9._-]+$")


async def publish_images(paths: list[Path]) -> list[str]:
    """Uzum shabloni faqat ommaviy HTTPS havolalarni qabul qiladi, shuning uchun rasmlar tashqi hostga yuklanadi."""
    if not paths:
        raise AppError("uzum_no_images", "Kartochkada Uzum uchun rasm yo'q.", 422)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            return list(await asyncio.gather(*(_upload(client, path) for path in paths)))
    except httpx.HTTPError:
        raise AppError("image_host_unavailable", "Rasmlarni ommaviy hostga yuklab bo'lmadi.", 502) from None


async def _upload(client: httpx.AsyncClient, path: Path) -> str:
    data = await asyncio.to_thread(path.read_bytes)
    response = await client.post(HOST_API, data={"reqtype": "fileupload"},
                                 files={"fileToUpload": (path.name, data, "image/jpeg")})
    response.raise_for_status()
    url = response.text.strip()
    if not PUBLIC_URL.fullmatch(url):
        raise AppError("image_host_invalid", "Rasm hosti kutilmagan javob qaytardi.", 502)
    return url
