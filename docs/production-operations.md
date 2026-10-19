# Production operations (dry-run web worker)

These commands apply after the deployment runbook is completed. They do not mean
production is currently running. SSH as niklas and `cd ~/campushallenbot`.
`scripts/production` always selects `compose.prod.yaml` and the non-secret
`~/.config/campushallenbot/production.env`. Do not use development Compose here.

## Status, logs and health

```sh
./scripts/production ps
./scripts/production logs --tail 100 web worker cloudflared
./scripts/production exec -T web python -c 'from urllib.request import Request,urlopen; print(urlopen(Request("http://127.0.0.1:8000/health",headers={"Host":"campushallenbot.se"})).status)'
./scripts/production exec -T worker python -m campushallen.healthcheck worker
./scripts/production exec -T cloudflared cloudflared tunnel --metrics localhost:2000 ready
docker stats --no-stream
df -h /
du -sh ~/campushallenbot-data ~/campushallenbot-backups
ls -lt ~/campushallenbot-backups/campushallen-*.sqlite3 | head
systemctl --user status --no-pager campushallen-bot.timer campushallen-friend.timer
sudo ss -tulpn
```

Check the website dashboard's recent Bokningsmotor heartbeat and Testläge status.
The worker probe requires a fresh heartbeat **and dry-run**. Healthchecks report
failure; Docker restart policy restarts crashed processes, not merely unhealthy
ones. Investigate stale heartbeat rather than blindly restarting repeatedly.
Cloudflared request/error logs are intentionally suppressed below fatal because
invite URLs contain bearer secrets; readiness and dashboard status are the
primary tunnel checks. Never enable body/debug logging to diagnose real accounts.

Web/worker are limited to 256 MiB each; cloudflared to 128 MiB. These ceilings are
not capacity verification on a 1 GiB Pi alongside old bots. Monitor actual free
memory, OOM events, thermal/undervoltage flags and disk growth before accepting
production reliability. Backup archives contain sensitive encrypted account data.

## Restart one component

```sh
./scripts/production restart web
./scripts/production restart worker
./scripts/production restart cloudflared
```

Restarting worker preserves dry-run. Never append real-booking flags. No host
ports are published; use `exec` for diagnostics. A tunnel token replacement needs
container recreation to remount a replaced secret file:
`./scripts/production up -d --force-recreate cloudflared`.

## Admin and invitations

Prefer the Swedish Admin page for temporary test invitations; invite links are
shown once. CLI commands are trusted operator actions:

```sh
./scripts/production exec web python -m campushallen.cli invite create --label "Friend" --expires-days 7
./scripts/production exec web python -m campushallen.cli invite list
./scripts/production exec web python -m campushallen.cli invite revoke INVITE_ID
```

The create command deliberately displays bearer links in the operator's private
terminal. Do not run it in captured logs/chat or record its output. Public links
must begin with `https://campushallenbot.se/invite/`. Revoked/used/expired/unknown
invitations share an unavailable response. Do not bypass tenant ownership as admin.

## Website login limits

Website password failures use persistent SQLite buckets keyed by HMAC of the
normalized submitted username. Unknown usernames receive identical counters,
status codes and messages. Ten failures within a 30-minute observation window
lock that name for 30 minutes. Blocked requests do not extend the lock. A valid
password after expiry resets the count; ordinary temporary locks need no admin
intervention. A successful login resets account failures, not the IP quota.

All login submissions also use a persistent fixed-window 20-per-15-minute IP
quota plus a bounded in-memory front limiter. The tunnel supplies the trusted
source IP; untrusted forwarding headers cannot change it. Failure responses have
a 300 ms minimum duration to reduce timing differences. Blocked requests skip
Argon2; unknown names below the limit use the dummy Argon2 hash. This reduces
practical timing signals but does not promise constant-time behavior.

A SQLite write boundary serializes password verification and counter updates;
this prevents concurrent threshold bypass and caps Argon2 concurrency at one.
It briefly blocks other database writers during a website login. The one-process,
small invite-only deployment deliberately trades throughput for bounded RAM and
correct counters. Monitor the Pi under load before considering wider use.
Buckets expire after a day without new verification activity. Passwords, raw IPs
and submitted names are not stored in buckets/audit events. Changing SECRET_KEY
invalidates sessions and changes HMAC bucket identities; do not rotate it merely
to bypass a lock. Session and CSRF behavior remain in place.

Campushallen credential retry behavior is separate and unchanged: one saved-login
attempt per operation; explicit rejection requires reconnecting, temporary errors
use bounded backoff. The website ten-failure policy does not retry Campushallen
passwords.

## Backups and daily schedule

```sh
./scripts/backup-production
./scripts/production exec -T worker python -m campushallen.cli restore-test /backups/BACKUP_FILENAME --expect-user niklas
```

Install only the new website backup units after a successful manual backup:

```sh
install -d -m 700 ~/.config/systemd/user
install -m 600 deployment/systemd/campushallen-web-backup.service ~/.config/systemd/user/
install -m 600 deployment/systemd/campushallen-web-backup.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now campushallen-web-backup.timer
systemctl --user list-timers --all
```

Check user lingering before relying on the timer across logout/reboot:
`loginctl show-user niklas -p Linger`. Enable lingering with sudo only if absent
and appropriate; confirm existing user timer behavior. Neither legacy booking
unit is edited or manually started by these commands. The daily timer retains
the latest 14 completed snapshots; extra manual backups count toward retention.
See [backup/recovery](backup-restore.md) for off-Pi copies and restoration.

## Update and rollback

1. Verify a new source commit locally with all checks and build its arm64 image.
   Transfer the committed archive to a new release directory and load the image.
2. While the old dry-run stack is running, take a backup and restore-test it.
   Keep the matching key, old image, old source and old non-secret configuration.
3. Stop web/worker (not the legacy bots). Change APP_IMAGE in the external
   production.env and switch the source symlink to the new verified release.
4. With web stopped, apply explicit migrations using `production run --rm
   --no-deps web alembic upgrade head`, then `current` and `check`.
5. Run `./scripts/production up -d --wait web worker cloudflared`, then check
   HTTPS/login, heartbeat, dry-run mode, persistence and old timer state.

If only application code changed and schema is compatible, roll back by stopping
web/worker, restoring the previous APP_IMAGE and source symlink, then starting the
same three services. If schema/data compatibility is uncertain, keep the site
unavailable and follow the explicit database restore procedure. Do not run a
destructive Alembic downgrade blindly. Restoring an old DB also restores old
invitation/session/audit state: revoke stale sessions/invitations deliberately.
Do not prune the previous image until recovery has been tested.

A controlled Pi reboot is permitted only after backups, timer checks and resource
preflight, at a time that avoids imminent old booking services. After SSH returns,
repeat every status/HTTPS check, confirm the known admin persists and ask the user
to test login. Reboot itself and real public checks remain outstanding until the
Pi is reachable and the production stack is configured.
