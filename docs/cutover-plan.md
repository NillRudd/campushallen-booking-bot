# Proposed live-booking cutover — separate future approval required

**Do not execute this plan during deployment.** The new worker remains dry-run.
The old systemd bots are still the live system. Publishing the website is not a
completed booking migration, and this document does not authorize a live test.

1. Complete production acceptance first: stable HTTPS/admin/invites, trustworthy
   worker heartbeat, no current undervoltage, adequate disk/RAM, tested reboot,
   daily backups and an off-Pi matching encryption key. Resolve every warning.
2. Have niklas and the friend create/connect their own website Campushallen
   accounts and explicitly choose credential storage. Enter Tuesday, Thursday
   and Sunday Innebandy rules; preview matching passes and verify product,
   facility, local times, opening times and waitlist choices. Do not import old
   passwords, tokens or auth files. Observe dry-run behavior across real opening
   times without sending booking/cancellation requests.
3. Obtain a **separate explicit approval** identifying the accounts/rules and
   timing of the cutover. Choose a maintenance window before the next booking
   release, with no old booking service already in flight. Independently check
   existing Campushallen bookings and unresolved results/claims.
4. Stop the new dry-run worker. Only in that approved phase, disable and stop
   both old booking timers. Confirm neither corresponding service is running;
   wait for any in-flight work to finish rather than assuming a stopped timer
   recalls an issued request. Keep old directories/config/auth files intact for
   rollback. Do not manually trigger them.
5. Take and verify a fresh database backup. Record timer state, selected commit,
   rules and existing bookings. Reconfirm that there is exactly one intended
   live scheduler and no hidden legacy timer/crontab invoking another copy.
6. Review a future explicit production command change enabling the existing
   two-flag guard for that single worker. Neither real-mode flag is present in
   today's active Compose configuration. Start live mode only after steps 3–5
   have been explicitly accepted. Web remains a separate single-process service.
7. Supervise the first opening, inspect the durable outcome, and independently
   check Campushallen's booking list. A timeout or unverified result requires
   manual inspection before any retry; never reset a durable claim blindly.
   Do not cancel bookings as a verification technique.
8. After a successful observed cutover, confirm the old timers remain disabled,
   backups/monitoring still work and friend-account failures remain isolated.
   Only then report the live migration complete. Keep legacy files during an
   agreed rollback period; removal is a separate deliberate action.

Rollback order is critical: stop the new live worker first, confirm its work has
finished and inspect uncertain bookings, then restore the known old scheduling
state if approved. Never reactivate the old timers while the new worker can send
live requests. An image rollback or database restore does not undo bookings at
Campushallen.
