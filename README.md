# Campushallen Booking Bot

Local automation for booking configured Campushallen Go Active Booking activities.

## Status

The project uses Campushallen's Go Active/BRP HTTP API for read-only discovery
of configured group-activity slots. Live booking is intentionally guarded:
`--live` resolves and prints the exact target, then stops at `READY FOR LIVE TEST`
without sending a state-changing booking request.

Campushallen's public FAQ says group training and ball sport activities can be
booked no earlier than seven days before start. The default config uses seven
days and same local time as the activity, for example Tuesday 18:45 opens the
previous Tuesday at 18:45.

## Files

- `src/campushallen/auth.py` starts a manual login browser or API login from local `.env`.
- `src/campushallen/booking.py` finds a configured activity, waits locally until booking opens, and dry-runs or stops before live booking.
- `src/campushallen/inspect_schedule.py` opens the schedule and saves safe diagnostics for selector/API analysis.
- `config.example.yaml` documents configurable bookings and timing.
- `systemd/*.service` and `systemd/*.timer` are user-service templates.
- `scripts/install_user_systemd.sh` installs user-service units for this checkout path.
- `tests/` covers date selection, booking windows, timezone behavior, and status classification.

Sensitive local files are excluded by `.gitignore`: `config.yaml`, `.env`,
`state/`, `logs/`, `diagnostics/`, cookies, tokens, and Playwright storage state.

## Installation

Requires Python 3.10 or newer. Run these commands from the downloaded project
directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e .
.venv/bin/python -m playwright install chromium
cp config.example.yaml config.yaml
chmod 600 config.yaml
```

## Initial Login

```bash
.venv/bin/campushallen-login
```

Log in manually in the browser. If MFA, CAPTCHA, or another access check appears,
complete it yourself. After login, return to the terminal and press Enter. The
session is saved to `state/storage-state.json` with `0600` permissions.

Refresh the session with the same command whenever the bot reports that login is
required.

```bash
cp .env.example .env
chmod 600 .env
$EDITOR .env
.venv/bin/campushallen-login --env
```

## Configuration

Edit `config.yaml`.

```yaml
bookings:
  - name: "Tuesday group activity"
    activity: "Group activity name"
    product_name: "Product name shown by Campushallen"
    weekday: "Tuesday"
    booking_type: group_activity
    web_category_id: 40
    facility_contains: ["Facility name"]
    preferred_start_time: "18:45"
    allowed_start_window:
      earliest: "17:00"
      latest: "21:00"
```

Use `Europe/Stockholm` unless there is a specific reason to change it. Keep
`dry_run_default: true` until a dry-run has successfully found the intended
booking. Replace the example booking with your own activity, facility, weekday,
and time before running the bot.

If the live page needs a specific schedule URL or selector, set:

```yaml
schedule:
  activity_url: "https://campushallen.goactivebooking.com/..."
  selectors:
    booking_button: "button"
```

## Inspect Schedule

```bash
.venv/bin/campushallen-inspect-schedule
```

This opens the site with your saved session, records request metadata, and saves
a screenshot plus HTML under `diagnostics/`. Those files may reveal private page
contents and are ignored by git.

## Dry-Run

Configured bookings:

```bash
.venv/bin/campushallen-book --dry-run
```

Manual target:

```bash
.venv/bin/campushallen-book \
  --activity "Group activity name" \
  --date 2026-10-13 \
  --time 18:45 \
  --dry-run
```

Use `--wait` to start before the opening moment and wait locally without busy
waiting. Dry-run finds the pass through read-only API calls, computes opening
time from `bookableEarliest`, and stops before any booking request.

## Live Booking

After dry-run has found the exact intended pass:

```bash
.venv/bin/campushallen-book \
  --activity "Group activity name" \
  --date 2026-10-13 \
  --time 18:45 \
  --live \
  --wait
```

The bot logs the exact target and opening time, then stops with
`READY FOR LIVE TEST` before sending any state-changing request. If the API
reports full or waitlist-only and `allow_waitlist` is false, it exits without
retry spam.

## systemd User Services

The installer writes generated user units to `~/.config/systemd/user/` using
the current checkout path. It enables the dry-run timer only.

```bash
scripts/install_user_systemd.sh
systemctl --user list-timers 'campushallen*'
```

Test manually:

```bash
systemctl --user start campushallen-bot-dry-run.service
journalctl --user -u campushallen-bot-dry-run.service -n 100 --no-pager
```

Enable live timer only after dry-run and Terms review:

```bash
systemctl --user disable --now campushallen-bot-dry-run.timer
systemctl --user enable --now campushallen-bot.timer
systemctl --user list-timers 'campushallen*'
```

If the machine must run user timers while you are logged out:

```bash
loginctl enable-linger "$USER"
```

Lingering keeps the per-user systemd manager alive after logout. It does not
require the bot to run as root.

Stop automation:

```bash
systemctl --user disable --now campushallen-bot.timer
systemctl --user disable --now campushallen-bot-dry-run.timer
```

Uninstall local user units:

```bash
rm ~/.config/systemd/user/campushallen-bot*
systemctl --user daemon-reload
```

## Logs and Diagnostics

Application logs are written to `logs/campushallen-bot.log`. User-service logs:

```bash
journalctl --user -u campushallen-bot.service -n 100 --no-pager
```

Screenshots and HTML diagnostics are written to `diagnostics/` after failures.
These files are ignored by git because they can contain private account content.

## Time Sync and Power

Check NTP:

```bash
timedatectl status
```

Look for `System clock synchronized: yes` and an active NTP service. If the
command cannot access the bus from a sandbox, run it in a normal terminal.

Check sleep settings:

```bash
systemctl status sleep.target suspend.target hibernate.target hybrid-sleep.target
gsettings get org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type
```

The machine may turn off the display, but automatic suspend or hibernate can
prevent scheduled bookings.

## Tests

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest
```

## Before Sharing

This project is designed so local credentials and browser state stay out of git.
Prefer sharing from tracked files, not by zipping the whole working directory.
Before pushing, verify that no private files are tracked:

```bash
git status --ignored --short
git ls-files config.yaml .env state logs diagnostics
git grep -n -E '/home/[[:alnum:]_-]+|eyJ[A-Za-z0-9_-]+\.' || true
git grep -n 'CAMPUSHALLEN_PASSWORD=' -- . ':!.env.example' ':!README.md' || true
```

The second command should print nothing. The grep command should not find real
credentials, JWT-looking tokens, or personal absolute paths.

## Troubleshooting

- `login_required`: run `.venv/bin/campushallen-login` again.
- `unknown`: no configured group activity was returned for that date/time; verify the configured time against a dry-run.
- `full` or `waitlist`: the bot detected the page state and did not force retries.
- `not_open`: use `--wait` from a systemd timer that starts 5-10 minutes before opening.
- Playwright errors: inspect `diagnostics/` and `journalctl --user`.
