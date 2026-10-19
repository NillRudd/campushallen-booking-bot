# Product and security phase design

This phase extends `feature/web-app` on `feature/pre-deployment-app` without
merging, rewriting, pushing, or deploying the previous branch. The baseline was
55 passing tests, Ruff and mypy. Existing CLI booking logic and systemd files are
unchanged.

## Schema and migration

Migration `7a36bb9c35dc` builds on `5cb3ccd7962c`. It adds:

- `users.is_admin`, default false for existing and new users.
- `campushallen_credentials`, one versioned encrypted password per account.
- `invitations`, hashed tokens with expiry, consumption/revocation timestamps,
  nullable creator and recipient references.
- `audit_events`, allowlisted event codes with optional actor/subject/account IDs.
- `worker_states`, per-process start, heartbeat, stop, version, and dry-run mode.
- `booking_opportunities`, unique per account/rule/occurrence, containing safe
  activity metadata, observed capacity, existing-booking status and next action.
- Account authentication timestamps, error code, reconnect latch, retry count/
  deadline, authentication lease, and discovery/result timestamps.

No existing user becomes an admin automatically. No credentials are inferred or
copied from local `.env`, YAML, browser files, or token state. SQLite native column
add/drop operations avoid recreating referenced account/user tables and
accidentally triggering cascading deletion. Migration tests preserve previous
users, tokens and sessions across upgrade, downgrade and re-upgrade. SQLite
3.35+ is required for downgrade column removal; the tested Python 3.12/3.14
runtimes provide newer SQLite versions. Downgrade removes this phase's new data.

## Authentication and cryptography

`TokenVault` and `CredentialVault` use distinct HKDF-SHA256 purpose strings from
one random Fernet-format master key. Fernet provides authenticated encryption;
no custom cryptographic primitive is implemented. New tokens have a `v1:` prefix;
unversioned previous-phase tokens use a legacy reader. Credential version 1
payloads bind the password to the account ID to reject ciphertext swapping.
Keys stay outside SQLite, using the existing private development generation or
required environment injection outside development.

Manual connect verifies Campushallen login before storing a newly remembered
password. Unchecking remember forgets an earlier password. Forgetting alone keeps
a current valid token; disconnect clears tokens and credentials and preserves
rules/history. Credential values and ciphertext are never rendered by the UI.

The account authentication service validates the current token through the
existing customer GET. It uses a remembered password only if the token is expired,
unavailable or rejected. A conditional database lease, including comparison of
the original ciphertext, limits concurrent automatic logins to one. Credentials
are decrypted only for the attempt. Successful state is replaced only if the
lease and saved credential still match and account/owner remain enabled.
Rejected passwords and unreadable credentials stop automatic retries. Temporary
errors back off and eventually require manual action. There is no recursive login
or automatic replay of an operation that already started. Token refresh endpoint
semantics remain unimplemented; this phase uses fresh login with explicit consent.

Website passwords remain Argon2id with a 12-character minimum and a generous
4096-character resource bound. Changing one revokes all sessions. Session creation
and password changes use database write boundaries to reject stale password
state. Admin demotion and user deletion protect the last active admin.

## Invitations and administration

Invites use 32 random bytes encoded as URL-safe tokens, with SHA-256 digests in
SQLite. Only the creation response or CLI output has a plaintext link. It is
never persisted in a session, flash, audit payload, example or image. Invalid,
expired, revoked and consumed links share an unavailable response. A conditional
UPDATE consumes the invitation in the same transaction that inserts the user;
username collision rolls back consumption. No client input can set admin status.

The CLI is a trusted local operator interface. It can create/promote/demote admins
and batch-generate/list/revoke invites without a browser login. Browser invite
management requires an active admin. Admins see safe health/status and security
codes; account routes still require ownership even for an admin. There is no
impersonation or credential-display capability.

## Operations, UI, and notifications

A separate heartbeat coroutine reports even while discovery is waiting on APIs.
No heartbeat means not running; stale reports mean attention is needed. Graceful
shutdown marks the process stopped. These records are status, not leader election.
Discovery snapshots persist API opening times, facility, capacity, waitlist and
existing bookings. Concurrent snapshots use SQLite upsert, with writes postponed
until network reads complete to avoid holding write locks during HTTP calls.
The dashboard labels snapshots and timestamps so cached availability is explicit.
History filters remain ownership-scoped and use Stockholm day boundaries.

Basic rules focus on product/activity, weekday, time, facility and waitlist.
Advanced fields retain the original expressiveness. Blank windows derive one
hour around a preferred time, or the whole day when no preference is supplied;
existing ambiguity checks still fail closed. Creating a rule from a snapshot
uses product/facility/time and the source rule's category, never a persistent
activity occurrence ID.

Notifications are provider-neutral events with enum kind and numeric IDs only.
No-op is the default. A log implementation exists; provider failure cannot undo a
committed booking result. No external notification provider is configured.

## Local deletion policy

- Deleting a rule keeps booking attempts, with its rule reference set to null.
- Forgetting a credential removes only its encrypted password and invalidates an
  in-flight automatic-login lease; existing token state remains.
- Disconnecting removes tokens/credential, keeping rules and history.
- Deleting a connection removes its credentials, tokens, rules, opportunities,
  claims, and local booking attempts through foreign-key cascades.
- Full website-account deletion requires current website password and literal
  DELETE confirmation. It revokes sessions and removes owned local data and
  user-linked audit events. An anonymous deletion code remains. Invitations keep
  their consumed timestamp/hash with recipient references and labels cleared, so deletion
  never reopens an invite.

These actions never call Campushallen cancellation. Requests already in flight
cannot be recalled; checking local ownership/enabled state does not cancel a
remote action that has already been issued. The development UI has no live
booking toggle. Production/ingress decisions remain deferred to the next phase.
