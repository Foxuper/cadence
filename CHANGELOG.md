# Changelog

All notable changes to cadence are documented here.

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
