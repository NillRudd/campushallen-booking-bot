# Pre-deployment review and next-phase checklist

This document records development findings and work required **before** public
access. It is not a deployment procedure. Nothing in this phase configures a
Raspberry Pi, systemd, a domain, DNS, Cloudflare, a reverse proxy, or TLS.

## Security model implemented in development

- Application passwords use Argon2id. Sessions are random opaque cookies with
  server-side HMAC digests, expiry, and database revocation. Password change
  invalidates all sessions; concurrent session creation checks the password hash
  under the database write boundary. CSRF protects every mutating browser form.
- Registration requires a 256-bit random invitation. Only SHA-256 digests are
  stored. Conditional consumption and user creation commit together. Used invite
  tombstones remain consumed when their user is deleted. Invitations can expire
  and be revoked; unavailable links share one generic error.
- Admin is a single explicit flag. Migration does not promote existing users.
  Admin routes require that flag; admin status does not bypass account ownership
  or provide credential access or impersonation. The last active admin cannot
  demote or delete themselves.
- Campushallen password storage is unchecked for a new connection and requires
  explicit per-account opt-in. Passwords are recoverably encrypted, not hashed,
  because reauthentication needs the original value. Tokens and credentials use
  Fernet with distinct HKDF-SHA256 purpose keys. Existing unversioned token
  ciphertext remains readable; newly saved token state uses a version prefix.
  Credential payloads bind the ciphertext to an account ID and version.
- A database lease prevents simultaneous credential login attempts for an
  account. One attempt is made per operation. Rejected credentials and unreadable
  secrets latch the account into a reconnect-required state. Temporary failures
  use 60/120/240/480-second backoff; the fifth consecutive failure requires manual
  intervention. Credential replacement or successful manual connection resets
  the latch. An operation that fails after token validation is not replayed,
  especially a booking POST.
- Failed authentication in one account does not stop discovery for another.
  Disabling, disconnecting, or forgetting credentials invalidates an in-flight
  reauthentication lease; its result cannot overwrite the user's action.
- SQLAlchemy hides bound parameters in exceptions. Invitation paths are redacted
  from Uvicorn access records; responses use no-store and no-referrer. Audit and
  notification interfaces accept event codes and IDs, without arbitrary secret
  payloads. No debug schema/docs endpoint is exposed. Form bodies are bounded
  to 256 KiB, including chunked bodies.
- The browser exposes read-only booking previews. Worker real execution still
  requires **both** operator flags; Compose remains dry-run. A durable live claim
  is kept after an uncertain request. No cancellation route exists. Local
  deletion never contacts a Campushallen cancellation endpoint.

**Residual credential risk:** Automatic Campushallen reauthentication requires
storing a recoverable encrypted password. Anyone who fully compromises both the
application data and its encryption key may be able to recover stored
credentials. This is optional per account. Encryption at rest does not protect
against a fully compromised running application, host, or operator account.

## Keys and configuration

- [ ] Generate independent production `SECRET_KEY` and master `ENCRYPTION_KEY`;
      require environment/secret injection. Never reuse development key files.
- [ ] Confirm secret provisioning does not bake keys into images, Git, CI output,
      shell history, Compose-rendered output, or ordinary logs.
- [ ] Set `APP_ENV` explicitly; absent keys must fail outside development.
- [ ] Set the correct production `PUBLIC_BASE_URL` before generating invites.
- [ ] Decide key rotation/version support and test recovery with legacy token
      ciphertext. A lost encryption key makes both tokens and remembered
      passwords unreadable; users must reconnect. There is no recovery backdoor.
- [ ] Review file ownership and `0600` key permissions, database/WAL permissions,
      and private data-directory permissions on the actual host.

## Transport and ingress (next phase only)

- [ ] Choose and review HTTPS, domain/DNS, and a reverse proxy or tunnel.
- [ ] Use `SECURE_COOKIES=true` and test login, registration, logout, and CSRF
      through the real HTTPS ingress. Decide HSTS and trusted host/origin policy.
- [ ] Configure trusted forwarded headers only for the actual ingress; do not
      trust arbitrary client-supplied IP headers for rate limiting.
- [ ] Restrict exposed container ports to intended web ingress. The development
      loopback binding is not a production network design.
- [ ] Configure proxy request limits/timeouts, redact invite paths in proxy and
      monitoring logs, and avoid Referer/query/body capture containing secrets.
- [ ] Test that API errors and ingress error pages cannot display request bodies,
      cookies, authorization headers, invite links, or SQL parameters.

## Process and host operations

- [ ] Prepare a production Docker configuration: no reload, no source bind mounts,
      intended immutable image/version, non-root user, reviewed dependency pins,
      resource limits, and the correct timezone/time synchronization.
- [ ] Decide web/worker process restart policy and orderly shutdown timing.
      Heartbeat is monitoring, not distributed leader election. Keep a single
      worker unless duplicate/race behavior is deliberately reviewed.
- [ ] Replace or share the in-process rate limiter before multiple web processes.
      Review login, registration/invite lookup, reconnection, and password-change
      limits against expected traffic and actual ingress IP behavior.
- [ ] Configure a host firewall and restrict SSH/administration access.
- [ ] Plan OS, container base-image, Python, and dependency security updates.
- [ ] Verify health monitoring for HTTP, stale worker heartbeat, authentication
      failures, pending requests, database availability, disk space, and time sync.
- [ ] Define safe log levels, rotation, retention, access, and incident response.
      No external notification provider is enabled yet; choose providers and
      non-sensitive message contents separately.
- [ ] Decide operational recovery for uncertain live claims. Never reset a claim
      blindly; independently inspect the Campushallen booking first.

## Backups and restoration

- [ ] Back up SQLite using its online backup interface or a coordinated snapshot;
      copying only the main file while WAL is active is not a sufficient policy.
- [ ] Back up encryption keys separately, encrypted and access-controlled. Test
      restoration of both database and matching keys into an isolated system.
- [ ] Establish backup schedule, retention, integrity checks, and recovery targets.
- [ ] Verify upgrade from the preceding schema on a copy of real data. Downgrading
      this phase drops its new credential/invite/audit/snapshot/heartbeat tables;
      back up first. Legacy users, tokens, rules, sessions, and attempts survive.
- [ ] Review retention of booking history, snapshots, audit events, and consumed
      invite tombstones; define future maintenance/cleanup appropriate to usage.

## Administration and final acceptance

- [ ] Create or explicitly promote an operator/admin; verify a second recovery
      path without default passwords or built-in invites.
- [ ] Generate friend invitations explicitly. Save plaintext links once to a
      private location; don't put them into examples, images, Git, or logs.
- [ ] Verify admin pages expose operational status only, not private account
      contents or ciphertext, and that ownership checks still apply to admins.
- [ ] Re-run all tests, Ruff, mypy, migration checks, Docker and browser smoke
      tests with no real Campushallen mutations.
- [ ] Review the actual public threat model, dependency advisories and final
      configuration before enabling ingress. Do not treat these development
      checks as a penetration test or production-readiness certification.
- [ ] Keep real booking disabled until the operator explicitly approves a live
      test in a later phase. Do not weaken the two-flag guard.
