import asyncio
import json
import logging
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from cryptography.fernet import Fernet

from app.config import get_settings
from app.db import Database
from app.runtime import run
from app.services import backup, maintenance, monitor

cli = typer.Typer(no_args_is_help=True, add_completion=False, help="Karto Studio operator commands")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def with_database(operation):
    settings = get_settings()
    db = Database(settings)
    try:
        return await operation(db, settings)
    finally:
        await db.close()


@cli.command()
def web(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the web application."""
    import uvicorn

    settings = get_settings()
    config = uvicorn.Config("app.main:app", host=host, port=port, proxy_headers=True, access_log=False,
                            forwarded_allow_ips=settings.forwarded_allow_ips, server_header=False, workers=1)
    run(uvicorn.Server(config).serve())


@cli.command()
def worker() -> None:
    """Run the durable AI generation worker."""
    from app.worker import serve

    run(serve())


@cli.command("maintenance")
def maintenance_command(dry_run: Annotated[bool, typer.Option("--dry-run")] = False) -> None:
    """Expire credentials and drafts, purge deleted data, and remove orphan files."""
    report = run(with_database(lambda db, settings: maintenance.run_maintenance(db, settings, dry_run)))
    typer.echo(json.dumps(asdict(report), indent=2))


@cli.command()
def check() -> None:
    """Verify quota and budget reservation invariants without changing data."""
    problems = run(with_database(lambda db, _: maintenance.quota_problems(db)))
    for problem in problems:
        typer.echo(problem)
    typer.echo("OK" if not problems else f"{len(problems)} problem(s)")
    raise typer.Exit(1 if problems else 0)


@cli.command("monitor")
def monitor_command(notify: bool = True) -> None:
    """Check worker, queue, provider, budget and disk alerts."""
    async def operation(db, settings):
        alerts = await monitor.collect_alerts(db, settings)
        if alerts and notify:
            await monitor.send_alerts(settings, alerts)
        return alerts

    alerts = run(with_database(operation))
    typer.echo("\n".join(alerts) or "OK")
    raise typer.Exit(1 if alerts else 0)


@cli.command("worker-health")
def worker_health() -> None:
    """Exit successfully only when a worker heartbeat is fresh."""
    alive = run(with_database(lambda db, _: monitor.worker_alive(db)))
    raise typer.Exit(0 if alive else 1)


@cli.command("create-backup-key")
def create_backup_key() -> None:
    """Print a new BACKUP_KEY. Store it in the secret manager, never in Git."""
    typer.echo(Fernet.generate_key().decode())


@cli.command("backup")
def backup_command() -> None:
    """Create an encrypted database and image backup."""
    try:
        location = run(with_database(backup.create_backup))
    except backup.BackupError as error:
        typer.echo(f"Backup failed: {error}", err=True)
        raise typer.Exit(1) from None
    typer.echo(location)


@cli.command("export-deletions")
def export_deletions() -> None:
    """Export the encrypted deletion registry used after restores."""
    try:
        typer.echo(run(with_database(backup.export_deletions)))
    except backup.BackupError as error:
        typer.echo(f"Export failed: {error}", err=True)
        raise typer.Exit(1) from None


@cli.command()
def restore(archive: str, registry: Annotated[str | None, typer.Option()] = None,
            confirm_database: Annotated[str, typer.Option(help="Target database name, typed again for safety")] = "",
            without_registry: Annotated[bool, typer.Option("--without-registry")] = False) -> None:
    """Restore a backup into an isolated environment and re-apply deletions."""
    settings = get_settings()
    target = settings.database_url.get_secret_value().rsplit("/", 1)[-1].split("?")[0]
    if confirm_database != target:
        typer.echo(f"Refusing to overwrite database. Pass --confirm-database {target}", err=True)
        raise typer.Exit(2)
    if not registry and not without_registry:
        typer.echo("The latest deletion registry is required (--registry).", err=True)
        raise typer.Exit(2)
    try:
        with tempfile.TemporaryDirectory(prefix="karto-restore-") as directory:
            workspace = Path(directory)
            manifest = backup.restore_files(settings, workspace, archive)
            events = backup.load_registry(settings, registry, workspace) if registry else []
            applied = run(with_database(lambda db, _: maintenance.apply_deletions(db, events)))
            report = run(with_database(lambda db, current: maintenance.run_maintenance(db, current)))
    except backup.BackupError as error:
        typer.echo(f"Restore failed: {error}", err=True)
        raise typer.Exit(1) from None
    maintenance.audit(settings, "restore", {"archive": archive, "registry": bool(registry), "reapplied": applied})
    typer.echo(json.dumps({"manifest": manifest, "reapplied_deletions": applied, "purge": asdict(report)}, indent=2))
    if not registry:
        typer.echo("WARNING: restored without a deletion registry. Do not open this environment publicly.", err=True)


async def scheduled(settings, db, last: dict[str, float], now: float) -> None:
    alerts = await monitor.collect_alerts(db, settings)
    if alerts and (set(alerts) != last.get("alerts") or now - last.get("alerted", 0) >= 1800):
        await monitor.send_alerts(settings, alerts)
        last["alerted"] = now
    last["alerts"] = set(alerts)
    if now - last.get("maintenance", -3600) >= 3600:
        await maintenance.run_maintenance(db, settings)
        if settings.backup_key.get_secret_value():
            await backup.export_deletions(db, settings)
        last["maintenance"] = now
    if settings.backup_key.get_secret_value() and now - last.get("backup", -86400) >= 86400:
        await backup.create_backup(db, settings)
        last["backup"] = now


@cli.command()
def scheduler() -> None:
    """Run monitoring every minute, maintenance hourly and backups daily."""
    async def loop(db, settings):
        last: dict = {}
        while True:
            try:
                await scheduled(settings, db, last, asyncio.get_running_loop().time())
            except Exception as error:
                logging.getLogger(__name__).error("scheduler_task_failed type=%s", type(error).__name__)
            await asyncio.sleep(60)

    run(with_database(loop))


@cli.command("eval")
def eval_command(dataset: Path, output: Path, live: Annotated[bool, typer.Option("--live")] = False,
                 max_cost_usd: float = 1.0) -> None:
    """Evaluate prompt quality on a labeled sample set. Mock unless --live."""
    from app.evaluation import evaluate

    summary = run(evaluate(get_settings(), dataset, output, live, max_cost_usd))
    typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))
    raise typer.Exit(0 if summary["accepted"] else 1)


if __name__ == "__main__":
    sys.exit(cli())
