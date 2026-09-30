import hashlib
import io
import shutil
import warnings
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from app.errors import AppError

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000


@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    sha256: str
    width: int
    height: int


def prepare_image(data: bytes) -> PreparedImage:
    if not data or len(data) > MAX_FILE_BYTES:
        raise AppError("image_size", "Har bir rasm 10 MiB dan kichik bo'lishi kerak.", 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as original:
                validate_image(original)
                original.load()
                clean = ImageOps.exif_transpose(original)
                clean.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                if "A" in clean.getbands() or "transparency" in clean.info:
                    rgba = clean.convert("RGBA")
                    clean = Image.new("RGB", rgba.size, "white")
                    clean.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    clean = clean.convert("RGB")
                output = io.BytesIO()
                clean.save(output, format="JPEG", quality=90, exif=b"")
                result = output.getvalue()
                return PreparedImage(result, hashlib.sha256(result).hexdigest(), *clean.size)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise AppError("invalid_image", "Rasmni o'qib bo'lmadi yoki uning o'lchami juda katta.", 415) from None


def validate_image(image: Image.Image) -> None:
    if image.format not in {"JPEG", "PNG", "WEBP"}:
        raise AppError("image_type", "Faqat JPEG, PNG va WebP qabul qilinadi.", 415)
    if getattr(image, "is_animated", False) or image.width * image.height > MAX_PIXELS:
        raise AppError("image_dimensions", "Animatsiya yoki 40 megapikseldan katta rasm qabul qilinmaydi.", 415)


def write_image(root: Path, image: PreparedImage) -> str:
    key = f"{uuid4().hex}.jpg"
    try:
        root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(root)
        if usage.free < max(100 * 1024 * 1024, usage.total // 10):
            raise AppError("storage_full", "Fayl xizmati vaqtincha band.", 503)
        with (root / key).open("xb") as destination:
            destination.write(image.data)
    except OSError:
        (root / key).unlink(missing_ok=True)
        raise AppError("storage_unavailable", "Rasmni saqlab bo'lmadi. Qayta urinib ko'ring.", 503) from None
    return key


def image_path(root: Path, key: str) -> Path:
    if len(key) != 36 or not key.endswith(".jpg") or any(char not in "0123456789abcdef" for char in key[:32]):
        raise AppError("invalid_image_key", "Rasm topilmadi.", 404)
    return root / key
