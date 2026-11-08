# Changelog

All notable changes to cadence are documented here.

## [0.3.0] - 2026-11-08

### Changed

- Replaced `quiet_hours_urgent` with `urgent_bypass`, a list of gate names
  `urgent` may bypass: any subset of `quiet_hours` and `spacing` (`spacing` uses
  `spacing_minutes`). Hard gates (daily caps, catch-up, expiry, human proof and
  unanswered mute) are never bypassed.

### Added

- Create/update receipts include `spacing_until`, `cap_until` and warnings for
  spacing, exhausted daily caps, expiry before fire, catch-up misses and missing
  human proof.
- List output distinguishes `deferred:`, `paused:` and `error:` reasons.
- `/cadence update <id> urgent true|false` toggles the urgent flag explicitly.

### Fixed

- Silent beats no longer count as unanswered; only delivered replies do.
- Slash commands other than `/new` and `/reset` now count as human proof.
- Resuming a paused beat clears its pause reason and no-op streak.
- Stale claim recovery runs on every scheduler poll, not only at startup.

## [0.2.0] - 2026-11-08

### Added

- Per-beat `urgent` flag. It bypasses quiet hours only when the profile enables
  `quiet_hours_urgent` (default off).
- Create/update receipts include `quiet_hours_until`, `effective_next_at` and a
  `warning` when quiet hours will defer the beat. If the beat is urgent but
  `quiet_hours_urgent` is off, the warning says so.
- The `/cadence` slash command prints the same quiet-hours warning.

## [0.1.0] - 2026-11-07

- Persistent one-shot (`at`) and recurring (`every`) beats, bound to the chat
  that armed them.
- `scope=chat` survives `/new`; `scope=session` stays anchored to its session.
- Quiet hours, daily message/fire caps, spacing, minimum interval, catch-up
  window and retry delay.
- Atomic claims, deduplication and stale-claim recovery after a restart.
- Human-proof gate: a beat waits for a real message in the current session.
- No-op backoff and pause for recurring beats that finish silently.
- Unanswered policy: `smart`, `hard` or `off`.
- `/cadence` command and `cadence` tool with create/list/get/update/pause/
  resume/cancel/delete.
- Beat turns finish with `cadence respond {silent}`; `silent=true` suppresses the
  final message without relying on a text sentinel. An exact `[SILENT]` remains
  as a defensive fallback.
- Calmer defaults: 12 messages and 24 fires per chat per day, 15-minute
  spacing and recurring floor, 22:00-08:00 quiet hours, 3-hour catch-up,
  5-minute retry and 15-second poll.
