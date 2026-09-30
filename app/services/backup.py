import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url

from app.clock import utcnow
from app.config import Settings
from app.db import Database, advisory_lock
from app.models import CardImage, DeletionEvent
from app.services.maintenance import STORAGE_KEY, audit

logger = logging.getLogger(__name__)
MAGIC = b"KARTO-ENCRYPTED-1\n"
CHUNK_SIZE = 8 * 1024 * 1024
BACKUP_SUFFIX = ".kbk"


class BackupError(Exception):
    pass


def encrypt_file(key: str, source: Path, destination: Path) -> None:
    fernet = Fernet(key.encode())
    with source.open("rb") as reader, destination.open("xb") as writer:
        writer.write(MAGIC)
        index, chunk = 0, reader.read(CHUNK_SIZE)
        while True:
            following = reader.read(CHUNK_SIZE)
            final = b"\x01" if not following else b"\x00"
            token = fernet.encrypt(index.to_bytes(8, "big") + final + chunk)
            writer.write(len(token).to_bytes(4, "big") + token)
            if not following:
                return
            index, chunk = index + 1, following


def decrypt_file(key: str, source: Path, destination: Path) -> None:
    fernet = Fernet(key.encode())
    with source.open("rb") as reader, destination.open("xb") as writer:
        if reader.read(len(MAGIC)) != MAGIC:
            raise BackupError("Unknown backup format")
        expected, finished = 0, False
        while header := reader.read(4):
            if finished:
                raise BackupError("Data after final backup chunk")
            token = reader.read(int.from_bytes(header, "big"))
            try:
                plain = fernet.decrypt(token)
            except InvalidToken:
                raise BackupError("Backup key is wrong or data is damaged") from None
            if int.from_bytes(plain[:8], "big") != expected:
                raise BackupError("Backup chunks are out of order")
            finished = plain[8:9] == b"\x01"
            writer.write(plain[9:])
            expected += 1
        if not finished:
            raise BackupError("Backup is truncated")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        while chunk := reader.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def pg_environment(settings: Settings) -> dict[str, str]:
    url = make_url(settings.database_url.get_secret_value())
    values = {"PGHOST": url.host or "localhost", "PGPORT": str(url.port or 5432), "PGUSER": url.username or "",
              "PGPASSWORD": url.password or "", "PGDATABASE": url.database or ""}
    return {**os.environ, **values}


def run_postgres_tool(arguments: list[str], settings: Settings) -> None:
    executable = shutil.which(arguments[0])
    if not executable:
        raise BackupError(f"{arguments[0]} is not installed; run this command in the application container")
    result = subprocess.run([executable, *arguments[1:]], env=pg_environment(settings), capture_output=True,
                            text=True, timeout=3600, check=False)
    if result.returncode != 0:
        raise BackupError(f"{arguments[0]} failed: {result.stderr.strip()[-500:]}")


def build_archive(settings: Settings, workspace: Path, keys: list[str], revision: str) -> Path:
    dump = workspace / "database.dump"
    run_postgres_tool(["pg_dump", "--format=custom", "--no-owner", "--no-privileges", f"--file={dump}"], settings)
    present = [key for key in keys if STORAGE_KEY.fullmatch(key) and (settings.upload_dir / key).is_file()]
    manifest = {"format": 1, "created_at": utcnow().isoformat(), "alembic_revision": revision,
                "database_sha256": sha256(dump), "images": len(present), "missing_images": len(keys) - len(present)}
    (workspace / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive = workspace / "backup.tar"
    with tarfile.open(archive, "w") as bundle:
        bundle.add(workspace / "manifest.json", "manifest.json")
        bundle.add(dump, "database.dump")
        for key in present:
            bundle.add(settings.upload_dir / key, f"uploads/{key}")
    return archive


def storage_client(settings: Settings):
    import boto3

    return boto3.client("s3", endpoint_url=settings.backup_endpoint_url or None)


def store(settings: Settings, source: Path, name: str) -> str:
    try:
        if settings.backup_bucket:
            key = f"{settings.backup_prefix.strip('/')}/{name}".lstrip("/")
            extra = {"ServerSideEncryption": "AES256"} if not settings.backup_endpoint_url else None
            storage_client(settings).upload_file(str(source), settings.backup_bucket, key, ExtraArgs=extra)
            return f"s3://{settings.backup_bucket}/{key}"
        destination = settings.backup_dir / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return str(destination)
    except (OSError, BotoCoreError, ClientError) as error:
        raise BackupError(f"Backup storage failed: {type(error).__name__}") from None


def prune(settings: Settings, days: int = 30) -> int:
    cutoff = utcnow() - timedelta(days=days)
    removed = 0
    if settings.backup_bucket:
        client = storage_client(settings)
        prefix = f"{settings.backup_prefix.strip('/')}/backups/".lstrip("/")
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=settings.backup_bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                if item["LastModified"] < cutoff:
                    client.delete_object(Bucket=settings.backup_bucket, Key=item["Key"])
                    removed += 1
        return removed
    for path in settings.backup_dir.glob(f"backups/*{BACKUP_SUFFIX}"):
        if path.stat().st_mtime < cutoff.timestamp():
            path.unlink()
            removed += 1
    return removed


async def create_backup(db: Database, settings: Settings) -> str:
    key = settings.backup_key.get_secret_value()
    if not key:
        raise BackupError("BACKUP_KEY is required")
    async with db.transaction() as session:
        await advisory_lock(session, "maintenance")
        revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        keys = list((await session.scalars(select(CardImage.storage_key))).all())
        with tempfile.TemporaryDirectory(prefix="karto-backup-") as directory:
            workspace = Path(directory)
            archive = await asyncio.to_thread(build_archive, settings, workspace, keys, revision)
            encrypted = workspace / f"backup{BACKUP_SUFFIX}"
            await asyncio.to_thread(encrypt_file, key, archive, encrypted)
            name = f"backups/karto-{utcnow().strftime('%Y%m%dT%H%M%SZ')}{BACKUP_SUFFIX}"
            location = await asyncio.to_thread(store, settings, encrypted, name)
    removed = await asyncio.to_thread(prune, settings)
    audit(settings, "backup", {"location": location, "images": len(keys), "pruned": removed})
    return location


async def export_deletions(db: Database, settings: Settings) -> str:
    key = settings.backup_key.get_secret_value()
    if not key:
        raise BackupError("BACKUP_KEY is required")
    async with db.transaction() as session:
        events = (await session.scalars(select(DeletionEvent))).all()
        payload = [{"entity_type": event.entity_type, "entity_id": str(event.entity_id),
                    "requested_at": event.requested_at.isoformat()} for event in events]
        with tempfile.TemporaryDirectory(prefix="karto-registry-") as directory:
            plain, encrypted = Path(directory) / "registry.json", Path(directory) / f"registry{BACKUP_SUFFIX}"
            plain.write_text(json.dumps({"exported_at": utcnow().isoformat(), "events": payload}), encoding="utf-8")
            await asyncio.to_thread(encrypt_file, key, plain, encrypted)
            location = await asyncio.to_thread(store, settings, encrypted, f"deletions/latest{BACKUP_SUFFIX}")
        await session.execute(update(DeletionEvent).where(DeletionEvent.id.in_([event.id for event in events]))
                              .values(exported_at=utcnow()))
    return location


def fetch(settings: Settings, source: str, destination: Path) -> Path:
    try:
        if source.startswith("s3://"):
            bucket, _, key = source.removeprefix("s3://").partition("/")
            storage_client(settings).download_file(bucket, key, str(destination))
        else:
            shutil.copyfile(source, destination)
    except (OSError, BotoCoreError, ClientError) as error:
        raise BackupError(f"Cannot read {source}: {type(error).__name__}") from None
    return destination


def safe_extract(archive: Path, destination: Path) -> dict[str, Any]:
    with tarfile.open(archive, "r") as bundle:
        for member in bundle.getmembers():
            name = member.name
            allowed = name in {"manifest.json", "database.dump"} or (
                name.startswith("uploads/") and STORAGE_KEY.fullmatch(name.removeprefix("uploads/")))
            if not member.isfile() or not allowed:
                raise BackupError(f"Unexpected archive entry: {name[:80]}")
        bundle.extractall(destination, filter="data")
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    if sha256(destination / "database.dump") != manifest["database_sha256"]:
        raise BackupError("Database dump checksum does not match manifest")
    return manifest


def load_registry(settings: Settings, source: str, workspace: Path) -> list[dict[str, Any]]:
    encrypted = fetch(settings, source, workspace / f"registry{BACKUP_SUFFIX}")
    plain = workspace / "registry.json"
    decrypt_file(settings.backup_key.get_secret_value(), encrypted, plain)
    return json.loads(plain.read_text(encoding="utf-8"))["events"]


def restore_files(settings: Settings, workspace: Path, archive_source: str) -> dict[str, Any]:
    archive = fetch(settings, archive_source, workspace / f"archive{BACKUP_SUFFIX}")
    decrypt_file(settings.backup_key.get_secret_value(), archive, workspace / "archive.tar")
    extracted = workspace / "extracted"
    extracted.mkdir()
    manifest = safe_extract(workspace / "archive.tar", extracted)
    run_postgres_tool(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges", "--single-transaction",
                       f"--dbname={make_url(settings.database_url.get_secret_value()).database}",
                       str(extracted / "database.dump")], settings)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    for image in (extracted / "uploads").glob("*.jpg") if (extracted / "uploads").is_dir() else []:
        shutil.copyfile(image, settings.upload_dir / image.name)
    return manifest
