# Release and migration runbook

This is the operator procedure for a staging or production release. It does not replace the staging acceptance in `TODO.md`; record the image digest, commands, timestamps, and health results for each real deployment. Never put secrets or customer data in release notes.

## Preconditions

- Use an immutable image tag or digest and keep the previous image available.
- Confirm the target environment has the intended `.env`/secret-manager values, encrypted backup key, offsite backup destination, HTTPS domain, SMTP, and `APP_ENV=production`.
- Confirm the database and app image versions are compatible. A migration is run once by the `migrate` service, never by web or worker startup.
- Keep public traffic disabled if a required secret, backup, or deletion registry is missing.

## Upgrade

Run from the release directory with the production environment loaded:

```powershell
docker compose pull
docker compose run --rm --no-deps ops karto export-deletions
docker compose run --rm --no-deps ops karto backup
docker compose run --rm migrate
docker compose up -d web worker ops caddy
docker compose ps
docker compose exec web karto check
docker compose exec web karto monitor --notify false
```

Keep the output locations for the encrypted deletion registry and backup in the deployment record. Do not continue if either export/backup, migration, health check, invariant check, or monitoring check fails. Verify HTTPS, registration/email, one seller-owned card flow, export, and worker heartbeat on staging before opening traffic.

## Failed release and rollback

- Before migration failure, leave the old web/worker image in service and inspect the migration error. Do not start web or worker against an unknown schema state.
- Do not run `alembic downgrade` automatically. Migrations may not be data-reversible.
- If the new image is backward-compatible with the migrated schema, roll back only the app image and verify health/invariants.
- If schema/data compatibility is uncertain, keep public traffic disabled. Restore the pre-release encrypted backup and its latest deletion registry into an isolated environment using `karto restore`; verify cards, images, schema, and reapplied deletions there. Cut over only after the isolated restore passes and the operator has an explicit recovery decision.
- Never restore over the live database as an exploratory step. Preserve the failed environment and logs for diagnosis.

## Required release evidence

Record the commit and image digest, database migration revision before/after, backup and deletion-registry locations, migration result, health/monitor results, HTTPS smoke test, and any incident or rollback. Measure restore duration and data loss window to support the RTO/RPO targets; local backup unit tests do not establish those operational targets.