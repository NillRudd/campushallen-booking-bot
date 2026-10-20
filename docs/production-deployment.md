# Raspberry Pi production deployment

## Current status

**The Pi stack is installed; public production acceptance is still in progress.**
The source and arm64 image currently deployed are
`bedf9fb5f3b11d32e0a8c6f2757c6b0caeb0da45`. Source lives in
`/home/niklas/campushallenbot-releases/<commit>`; `~/campushallenbot` points to that
release. Documentation commits can advance independently of the deployed image.

Debian 13.7 (trixie), arm64, UID/GID 1000 runs Docker CE 29.9.1 and Compose 5.6.0,
installed from Docker's official repository. Web, dry-run worker and the official
cloudflared connector all passed their health checks on 20 October 2026. Admin
niklas was created with the operator's private password. Alembic is at
`d8426c9ef017` with no schema drift. The operator is configuring the public route;
HTTPS/admin/invite acceptance remains pending.

Separate production configuration/data/backup directories and private keys exist.
The daily backup succeeded automatically, and an isolated restore verified admin
metadata and synthetic encryption checks. A database snapshot is also copied to
`~/Documents/CampushallenBot-Backups/` on the Mac; the encryption key is stored
separately in `~/Documents/CampushallenBot-Recovery/`.

Current undervoltage/throttling (`0x50005`) persists. The operator explicitly
instructed continuing despite this condition. Reliability remains unproven.
Docker initially lacked memory-controller support. Appending
`cgroup_memory=1 cgroup_enable=memory` to the boot command line and a prerequisite
reboot enabled enforced container limits. The original boot line is retained at
`~/.cache/campushallen-provision/cmdline.txt.before-memory`.

Legacy live timers remain enabled and active; neither service was manually
triggered. All three containers recovered healthy in a controlled full-stack reboot; public
HTTPS acceptance remains pending the route. The
[verification record](production-validation.md) separates completed checks from
outstanding acceptance. No live cutover has occurred.

## Architecture and boundaries

`https://campushallenbot.se` → Cloudflare → remotely managed Cloudflare Tunnel →
`cloudflared` → `http://web:8000` on a dedicated Docker bridge. Web and the single
**dry-run** worker share one commit-tagged arm64 application image and persistent
SQLite directory. There are no published host ports. The tunnel's metrics port
is also private. Uvicorn uses one process and ignores generic proxy headers.

Only peer `172.30.85.3` may supply Cloudflare client identity and HTTPS scheme.
`CF-Connecting-IP` must be a single valid IP; `X-Forwarded-For` is ignored.
Production requires the public hostname, secure cookies and same-origin mutating
forms. Do not add another container to this trusted network or publish web ports.
The fixed subnet `172.30.85.0/29` must be checked for conflicts on the Pi.

The old `/home/niklas/booking_bot` and `booking_bot_friend` checkouts and their
live systemd timers remain untouched. No legacy credentials are read or copied.
No new Campushallen accounts are connected automatically. Real booking requires
a later, explicitly approved [cutover](cutover-plan.md).

## Preflight and Docker

From the Mac, connect with `ssh niklas@192.168.1.127`. Enter SSH/sudo passwords
only in your own terminal, never in chat, scripts or environment files. Run the
read-only checks in `scripts/pi-preflight` on the Pi. Record OS/architecture,
free memory/disk, time sync, listening ports, timer state and throttling flags.

Stop host changes if disk space is dangerously low or there is current
undervoltage (bit 0 of `get_throttled`). Bits 16–19 are historical conditions;
record them separately. Confirm both legacy timers are enabled and waiting,
without invoking either booking service. Check for imminent timers before any
reboot. Review existing SSH and firewall exposure without altering booking files.

If Docker/Compose already work, retain them. For missing Docker use the
[current official Debian instructions](https://docs.docker.com/engine/install/debian/)
for the detected release and arm64 architecture. Use its signed apt repository,
not a convenience curl-to-shell installer. Install only `docker-ce`,
`docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`, and `docker-compose-plugin`.
Do not remove conflicting packages before inspecting how the host uses them.
Use sudo interactively if needed; Docker group membership grants root-equivalent
host control. Check Docker's daemon starts at boot and run a harmless test.

## Build and transfer an immutable release

Run all tests, Ruff, mypy, browser smoke and the isolated production smoke before
selecting a source commit. `Dockerfile.prod` pins its multi-architecture Python
base digest. Runtime and wheel-build dependencies are separately locked with
hashes; installation uses prebuilt wheels. Browser/test/build packages are absent
from the runtime. Build on the Mac to avoid Pi compilation and memory pressure:

```sh
DEPLOY_COMMIT=$(git rev-parse HEAD)
APP_IMAGE="campushallenbot:$DEPLOY_COMMIT"
docker buildx build --platform linux/arm64 --load -f Dockerfile.prod \
  --build-arg VCS_REF="$DEPLOY_COMMIT" -t "$APP_IMAGE" .
docker image inspect "$APP_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
```

Use a clean committed source tree. Transfer only `git archive` output into a new
`/home/niklas/campushallenbot-releases/<commit>` directory. Save the source SHA as
`DEPLOYED_COMMIT` there. Transfer the image with `docker save "$APP_IMAGE" | gzip -1`
over SSH into `gunzip | docker load` on the Pi. No Git push is needed. Confirm
image architecture and OCI revision agree with the intended release.

Create `/home/niklas/campushallenbot` as a symlink to that release only after
checking it does not already contain an unrelated installation. Do not overwrite
or reuse either legacy checkout. Future updates use a new release directory and
switch this symlink; retain the previous image and source for rollback.

## Private configuration, keys and data

On the Pi as niklas:

```sh
umask 077
install -d -m 700 "$HOME/.config/campushallenbot" \
  "$HOME/campushallenbot-data" "$HOME/campushallenbot-backups"
cp ~/campushallenbot/deployment/production.env.example \
  ~/.config/campushallenbot/production.env
chmod 600 ~/.config/campushallenbot/production.env
id -u
id -g
```

Edit this **non-secret** environment file: set the commit-tagged APP_IMAGE,
actual nonzero operator UID/GID, CONFIG_DIR, DATA_DIR and BACKUP_DIR. Containers
run as that UID so 0600 secret files are readable without making them public.
The default image user is also non-root. Do not run production as UID 0.

Generate independent keys without displaying them, using the verified image
and the provisioning script from the matching release:

```sh
APP_IMAGE=$(sed -n 's/^APP_IMAGE=//p' "$HOME/.config/campushallenbot/production.env")
docker run --rm --network none --user "$(id -u):$(id -g)" \
  -v "$HOME/.config/campushallenbot:/provision" \
  -v "$HOME/campushallenbot/deployment/provision_secrets.py:/provision.py:ro" \
  "$APP_IMAGE" python /provision.py
```

The script creates only absent `secret_key` and `encryption_key` files at 0600;
it never replaces existing keys or reads old booking-bot files. Keep the config
directory at 0700. The application reads `/run/secrets/secret_key` and
`/run/secrets/encryption_key` via backwards-compatible `_FILE` settings. Ordinary
environment settings still work in development; explicit values take precedence.
Do not run Compose config rendering with secret values in the environment.

Before public exposure, copy the production encryption key over SSH to a private
Mac recovery directory, without displaying it:

```sh
umask 077
install -d -m 700 "$HOME/Documents/CampushallenBot-Recovery"
scp niklas@192.168.1.127:/home/niklas/.config/campushallenbot/encryption_key \
  "$HOME/Documents/CampushallenBot-Recovery/encryption_key"
chmod 600 "$HOME/Documents/CampushallenBot-Recovery/encryption_key"
```

Do not overwrite an existing recovery key until its identity and matching backup
set are checked. Record which deployment uses the copy; never record its value.
Production data is `/home/niklas/campushallenbot-data/web.db`, independent of
legacy state and development Docker volumes. Database/WAL files inherit umask
0077. Ordinary Compose down/recreate/reboot must retain it.

## Migrate and bootstrap before publishing

In `~/campushallenbot`, with web/worker not yet running:

```sh
./scripts/production run --rm --no-deps web alembic upgrade head
./scripts/production run --rm --no-deps web alembic current
./scripts/production run --rm --no-deps web alembic check
./scripts/production run --rm --no-deps web python -m campushallen.cli create-user niklas --admin
./scripts/production up -d --wait web worker
```

The website password is entered twice interactively by the user. Do not generate,
store or relay it. If niklas already exists, inspect safe metadata and ask the
user before creating/changing that identity. Migrations never run automatically
on production web startup. Avoid `compose run` for a service already running:
static network addresses would collide. Use `exec` for operator commands then.

## Cloudflare setup

Reviewed official docs on 19 October 2026:
[get started](https://developers.cloudflare.com/tunnel/get-started/),
[token-file](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/run-parameters/#token-file),
[client headers](https://developers.cloudflare.com/fundamentals/reference/http-headers/),
[DNS routes](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/dns/).

In the user's own authenticated Cloudflare dashboard, Networking → Tunnels →
Create Tunnel, create/select `campushallenbot-production`. Obtain its remotely
managed connector token. Do not run the dashboard's token-bearing command or
paste it into chat. Save only the token into
`~/.config/campushallenbot/cloudflare_tunnel_token` on the Pi, mode 0600, using a
hidden prompt in the user's terminal:
`python3 ~/campushallenbot/deployment/save_tunnel_token.py`. A compromised
tunnel token permits another connector; rotate it through Cloudflare if exposed.

Compose pins official cloudflared 2026.10.1 by digest and reads `--token-file`.
After the token file exists, start `./scripts/production up -d cloudflared`.
In the tunnel's Routes, add a Published application for the root hostname
`campushallenbot.se`, HTTP service `http://web:8000`. Preserve the public Host
header. Cloudflare supports proxied apex CNAMEs through
[CNAME flattening](https://developers.cloudflare.com/dns/cname-flattening/).
Do not silently choose a subdomain, add home-IP A/AAAA records, configure www,
expose SSH, open router ports, or use Workers as the application origin.

Keep automatic edge HTTPS/redirect behavior tested. Do not add Access login,
Under Attack Mode, aggressive bot settings or cache-everything rules. App HTML
is `private, no-store`. No tunnel debug/body logging: cloudflared logs only fatal
errors to avoid recording invite bearer paths in request-error logs; health is
checked using its private readiness endpoint and the dashboard.

## Acceptance before declaring deployment complete

Check real public HTTPS, valid certificate, Swedish login/admin, secure SameSite
cookies, same-origin CSRF, no mixed content/internal-host redirects, no debug
schema/tracebacks, no public caching and HTTPS invite links. Have the user log
into the real admin UI; create/copy/revoke a temporary invite and confirm it is
unavailable afterwards. Do not connect Campushallen accounts during this smoke.

Verify web and worker health, dry-run heartbeat, no published service ports,
private directory permissions, backup creation, isolated restore and off-Pi key
recovery copy. Enable the separate daily website-backup timer only after its
manual command works. Recheck both old live timers before and after a controlled
reboot. Confirm Docker/tunnel/worker recover, database and keys persist, admin
login still works, and public HTTPS returns. Keep the new worker dry-run and stop
at the proposed cutover plan; do not declare the live booking migration complete.
