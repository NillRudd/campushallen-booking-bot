# Database backup and recovery

## What is backed up

`campushallen-admin backup` (or `python -m campushallen.cli backup`) uses Python's
SQLite online backup API, including committed data still in the WAL. It checks
integrity and foreign keys, fsyncs the output, atomically publishes a timestamped
snapshot and retains the newest 14 completed snapshots by default. Do not copy
only `web.db` while writers may be active.

`./scripts/backup-production` executes this in the existing worker container.
It requires the worker to be running; it does not start another scheduler.
Snapshots live at `~/campushallenbot-backups/campushallen-<UTC>-<random>.sqlite3`,
mode 0600 inside a 0700 directory. Keys are never included. A partial failure
removes only its own incomplete file, and retention deletes only this tool's
completed snapshot names. Keep enough disk space for an additional full snapshot
and a restore-test copy.

An actual disaster recovery set requires both the database **and the matching
production encryption key**. The session secret can be replaced to invalidate all
sessions. Without the encryption key, existing Campushallen tokens/remembered
passwords cannot be recovered; users must reconnect. Keep the encryption key on
the Mac in the separate private recovery directory described in the deployment
runbook. Also copy selected snapshots off-Pi over SSH to a different private
backup directory; same-card backups do not survive microSD failure. Define an
offsite/encrypted storage policy before relying on this as a full disaster plan.

## Isolated restore test

```sh
cd ~/campushallenbot
./scripts/backup-production
./scripts/production exec -T worker python -m campushallen.cli restore-test \
  /backups/BACKUP_FILENAME --expect-user niklas
```

The command opens the snapshot read-only and restores into a private temporary
subdirectory next to the backup. It checks SQLite integrity, foreign keys,
migration metadata and the expected active admin. It exercises purpose-separated
token and credential encryption with synthetic data and the configured key;
it never prints stored credentials or modifies production. The temporary database
is removed afterwards. Keeping the temporary copy on disk avoids consuming a
small container tmpfs with a full database.

For a new database with no Campushallen accounts, the cipher round-trip confirms
the key is valid; it cannot establish a historical DB/key association that has
never been used. Preserve the matched recovery set and verify existing encrypted
credentials in an isolated recovery exercise before relying on a later populated
backup. Do not authenticate to Campushallen as part of restore testing.

## Restore production deliberately

This is a recovery action, not part of initial smoke tests. First preserve the
current data and verify the selected snapshot and matching key. Stop web and
worker so no process holds the database open. Keep the tunnel stopped or serving
an unavailable origin while recovering. Do not touch legacy bots.

Rename the whole existing application state directory to a dated, private
quarantine directory. Create a new 0700 state directory owned by the configured
non-root UID. Put the verified snapshot there as `web.db`, mode 0600, with no
stale WAL/SHM sidecars. Use a matching external encryption-key file, also 0600.
Never copy a snapshot over a live WAL database, and never delete unrelated files
using a wildcard. Check ownership and disk space.

Use the matching source/image, run explicit Alembic `current`, any necessary
forward migration and `check` while web is stopped, then start web/worker and
validate metadata, admin login, dry-run heartbeat and HTTPS. Invalidate recovered
web sessions and review invite revocations, because a backup reflects earlier
security state. Confirm existing Campushallen bookings manually before any
future live retries; restoring a backup may remove newer durable attempt claims.
Retain the quarantined state until the recovery is accepted. Real booking remains
disabled throughout recovery.
