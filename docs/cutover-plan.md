# Controlled production cutover: Niklas only

The operator explicitly approved this phase on 20 October 2026 after manually
accepting the UI. This authorizes one normal real booking for Niklas at the next
API opening. It never authorizes cancellations, friend migration, blind claim
resets or removal of legacy files.

Niklas is website user 1, connection 2, rules 1–3. The friend remains entirely on
campushallen-friend.timer; its directory/config/auth/units must not change.

## Required controls

- Both --live and --confirm-real-bookings are required by the worker.
- Migration f2c9a71e6b04 adds operator-only live_booking_enabled (false),
  live_booking_claim_limit (zero) and an optional reviewed activity ID. Existing
  and new connections default to no live permission, including reused IDs.
- The live scheduler selects only eligible accounts. Immediately before claiming,
  the engine rechecks account/user/rule eligibility after fresh API reads.
  A serialized database write checks remaining allowance and reserves the durable
  claim together. Concurrent workers cannot overspend the allowance.
- This approval permits connection 2, one total claim, activity 4343840 only.
  Uncertain claims consume allowance permanently. After that claim no further
  real request is permitted, including after restart. A later booking needs a
  separately approved allowance/target update. No claim is reset automatically.
- Website forms cannot enable eligibility. The trusted local CLI requires the
  expected owner, positive allowance and reviewed activity ID.
- Disconnecting or changing the login identity revokes the grant without deleting
  existing claims; a replacement identity needs fresh operator approval.
- scripts/production selects deployment/compose.niklas-live.yaml only when the
  external production.env contains WORKER_MODE=live. Default Compose stays dry-run.
- Health requires a fresh heartbeat matching the explicitly expected mode.

## Ordered cutover

1. Record both timers/services, next runs and Docker health/mode. Inspect all
   application accounts. Verify sole enabled/authenticated connection 2 and
   remembered encrypted credentials without displaying secrets.
2. Fresh discovery must match Innebandy 85 Vallastaden: Tuesday/Thursday 18:45,
   Sunday 12:00. Preserve existing explicit waitlist opt-in. Next unbooked target:
   4343840, Tuesday 27 October 18:45 CET. API opening: 20 October 18:45 CEST.
   Observed regular availability before opening: 15. Never estimate release time.
3. Pass tests, lint, types, browser and isolated ARM64 smoke. Take an online SQLite
   backup, restore-test it and verify off-Pi DB/key recovery. Deploy support code
   and migrate in dry-run; prove all account live eligibility remains false.
4. Recheck that the old Niklas service is inactive/MainPID=0. Run only
   systemctl --user disable --now campushallen-bot.timer. Prove disabled/inactive,
   with the friend still enabled/active/waiting. Stop and investigate any in-flight
   or uncertain old operation; do not kill it or enable a second booking mechanism.
5. Stop the new dry-run worker. Through the web container run the trusted CLI:
   python -m campushallen.cli live-eligibility 2 --user niklas --enable
   --max-claims 1 --activity-id 4343840. Verify no unrelated account is eligible.
   Save private configuration; set external WORKER_MODE=live. Recreate only worker
   with scripts/production up -d --no-deps --wait worker. Inspect actual command,
   live heartbeat, finite scope and both legacy timer states again.
6. Final read-only discovery/duplicate check, then let the normal scheduler wait
   for API opening. Never invoke an early/manual booking operation.
7. Observe the durable outcome and independently GET bookings. Check local
   history/dashboard observations. Record safe IDs/timestamps; no raw responses.
   On failure/uncertainty leave the claim intact and investigate before any retry.
8. Verify friend unchanged, old Niklas disabled, finite live allowance persistent.
   Reboot only outside both booking windows and without unresolved power or
   operation risks. Keep original Niklas checkout/unit files for rollback.

## Rollback order

1. Stop new worker gracefully with scripts/production stop worker; verify its
   process stopped. Scheduler shutdown drains in-flight synchronous requests.
2. Inspect claims and remote bookings independently. Resolve uncertain outcomes;
   do not automatically reset a claim, cancel or repeat a POST.
3. Disable connection 2 through live-eligibility 2 --user niklas --disable.
   Set external WORKER_MODE=dry-run; recreate worker and prove dry-run command and
   heartbeat before permitting another live mechanism.
4. Only after the new worker cannot mutate and uncertainty is resolved, run
   systemctl --user enable --now campushallen-bot.timer. Never manually start its
   service. The friend's timer is not part of rollback.

An image/database rollback does not undo a remote booking. Retain matching keys,
pre-migration snapshot, previous image/source and non-secret configuration.
