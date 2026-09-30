import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def database_url() -> str:
    url = os.environ.get("E2E_DATABASE_URL", "")
    if not url:
        from dotenv import dotenv_values

        test_url = dotenv_values(ROOT / ".env").get("TEST_DATABASE_URL") or ""
        url = test_url.rsplit("/", 1)[0] + "/karto_e2e" if test_url else ""
    if not url.split("?")[0].endswith("/karto_e2e"):
        raise SystemExit("E2E_DATABASE_URL must point to a dedicated karto_e2e database")
    return url


def configure(port: int) -> None:
    runtime = ROOT / "var" / "e2e"
    shutil.rmtree(runtime, ignore_errors=True)
    (runtime / "mail").mkdir(parents=True)
    os.environ.update({
        "APP_ENV": "test", "APP_BASE_URL": f"http://127.0.0.1:{port}", "DATABASE_URL": database_url(),
        "AI_PROVIDER": "mock", "MAIL_BACKEND": "file", "MAIL_DIR": str(runtime / "mail"),
        "UPLOAD_DIR": str(runtime / "uploads"), "AUDIT_LOG": str(runtime / "audit.log"),
        "BACKUP_DIR": str(runtime / "backups"), "ALLOWED_HOSTS": "127.0.0.1,localhost",
    })


def reset_database() -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")


async def serve(port: int) -> None:
    import uvicorn

    from app.config import get_settings
    from app.worker import serve as serve_worker

    server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=port, access_log=False, log_level="warning"))
    await asyncio.gather(server.serve(), serve_worker(get_settings()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Isolated web + worker server for browser tests")
    parser.add_argument("--port", type=int, default=8765)
    port = parser.parse_args().port
    configure(port)
    reset_database()
    from app.runtime import run

    run(serve(port))


if __name__ == "__main__":
    main()
