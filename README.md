# cadence

[![CI](https://github.com/Foxuper/cadence/actions/workflows/ci.yml/badge.svg)](https://github.com/Foxuper/cadence/actions/workflows/ci.yml)

**Your agent can say "I'll check back in ten minutes" and actually do it — in the same chat, with the same context.**

Cadence is a small, persistent heartbeat scheduler for Hermes Agent. It arms
**beats**: scheduled turns that return to the chat that created them. There is
no side model, no cron session and no new conversation. When a beat has nothing
worth saying, the agent finishes with `respond silent=true` and the message never
reaches you.

## What it feels like

**A reminder that remembers the conversation.**

> **You:** Remind me at 4 to send the Acme invoice.
> **Agent:** Will do — I'll check in at 16:00.
>
> *16:00.* **Agent:** It's 4. Did the Acme invoice go out? I can draft it if you want.

Because the beat runs in the same chat, it sees what you already said. If you
wrote "sent it" at 15:10, it can stay quiet.

**A recurring check that can shut up.**

> **You:** Every 15 minutes, check whether the deploy finished. Stay silent if there is nothing.
> **Agent:** Armed. I'll only speak when something changes.

A recurring beat that keeps finishing silently slows itself down and can pause
on its own.

## Why cadence

- **Same conversation, same model.** A beat is a normal turn in the chat that
  armed it, with that chat's history, tools and permissions.
- **Durable.** Beats live in SQLite on wall-clock time. They survive gateway
  restarts, sleep and `/new`.
- **Deterministic gates.** Quiet hours, daily caps, spacing, catch-up, retries
  and the unanswered policy run before any model call.
- **Quiet by default.** `respond silent=true` suppresses the reply; the tool call
  cannot leak into the chat.
- **Your bounds.** The agent works inside the settings. It can create, pause or
  cancel its own beats, but it cannot widen the profile limits.

## How it works

```text
you (/cadence) or the agent (cadence tool)
        │
        ▼
SQLite schedule (one row per beat)
        │
        ▼  every poll_seconds
gates: enabled · expiry · catch-up · scope · quiet hours · human proof
       · unanswered · daily caps · spacing
        │
        ▼
one turn in the same chat, on its own model
        │
        ├── normal reply  → delivered
        └── respond silent=true → suppressed
```

One scheduler runs per profile, guarded by a cross-process file lock. Claims are
atomic, so two workers cannot fire the same beat at once. A crash mid-fire can
still produce a retry, so delivery is at-least-once.

## Install

```bash
hermes plugins install Foxuper/cadence --enable
hermes plugins list
```

Then complete the configuration below and restart or reload the gateway.

## Configuration

Cadence has three layers. The first two are Hermes core keys; only the third
belongs to the plugin.

### 1. Load the plugin

```yaml
plugins:
  enabled:
    - cadence
```

This is what makes the `/cadence` command and the `cadence` tool available.

### 2. Allow delivery (core key)

```yaml
plugins:
  entries:
    cadence:
      allow_gateway_injection: true
```

Message injection is a privileged capability in Hermes: it lets a plugin queue
a message in a chat and start an agent turn. It is **off by default** and is
separate from `plugins.enabled` because a plugin may be useful without it
(tools, commands, skills).

For cadence, injection is how every beat is delivered, so in practice this key
is required. Without it, beats are still scheduled and visible in
`/cadence list`, but delivery is refused (`inject_refused`), retried every
`retry_seconds`, and eventually marked failed.

It is a profile-level permission, not a chat selector: it does not broadcast and
does not let the model choose a destination. Each beat still returns only to the
chat that created it.

### 3. Behavior settings

```yaml
plugins:
  entries:
    cadence:
      settings:
        enabled: true
        timezone: ""
```

These live under `plugins.entries.cadence.settings` and also appear in
**Settings ▸ Plugins** in Hermes Desktop.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | true | Master switch |
| `timezone` | `""` | IANA zone; blank = profile zone |
| `quiet_hours` | `22:00-08:00` | Defer beats during this window |
| `urgent_bypass` | `[]` | Gate names: `quiet_hours`, `spacing`; hard gates always apply |
| `max_messages_per_day` | 12 | Delivered (non-silent) replies per chat, local day |
| `max_fires_per_day` | 24 | Beat fires per chat, local day |
| `spacing_minutes` | 15 | Minimum between beats in a chat |
| `min_interval_minutes` | 15 | Floor for `every` |
| `catch_up_minutes` | 180 | Late one-shots beyond this are missed |
| `retry_seconds` | 300 | Retry after a refused injection |
| `poll_seconds` | 15 | Scheduler poll interval |
| `noop_backoff` | `gentle` | `off` / `gentle` / `assertive` |
| `noop_after` | 3 | Silent fires before slowing |
| `noop_max_hours` | 24 | Cap for the backed-off interval |
| `noop_pause_after` | 6 | Silent fires before pausing; 0 disables |
| `unanswered_policy` | `smart` | `off` / `hard` / `smart` |
| `unanswered_after` | 2 | Delivered beats without human reply |

`urgent` is per beat. `urgent_bypass` is a list of gate names: `quiet_hours`,
`spacing`, both, or empty (`spacing` uses `spacing_minutes`). Hard gates — daily
caps, catch-up, expiry, human proof and unanswered mute — always apply.

## Commands

`/cadence` with no arguments, or `/cadence help`, prints the command list.

### Syntax

```text
/cadence list
/cadence show <id>
/cadence add [session|chat] [urgent] <when> [until <duration>] [max <n>] <note>
/cadence update <id> note <note>
/cadence update <id> <when> [until <duration>] [max <n>] [note]
/cadence update <id> until <duration>
/cadence update <id> max <n>
/cadence update <id> urgent true|false
/cadence pause <id>
/cadence resume <id>
/cadence cancel <id>
/cadence delete <id>
```

`<when>` accepts:

| Form | Meaning |
|---|---|
| `in 10m` | One-shot relative to creation |
| `every 15m` | Recurring interval |
| `at 16:30` | One-shot at a clock time; if already past, tomorrow |
| `16:30` | Same as `at 16:30` |
| `daily at 09:00,13:00` | Recurring at local wall-clock times; one beat, multiple times |
| ISO timestamp | One-shot at an exact time |

`<duration>` accepts `30m`, `2h`, `1d` or a bare number of minutes. `until`
accepts a duration, `in 2h` or an ISO timestamp.

`max <n>` is the lifetime fire limit for recurring beats. `urgent` is per
beat; `urgent_bypass` decides which gates it may skip. `daily` and `every` are
mutually exclusive; `daily` takes a comma-separated list of `HH:MM` local times.

`scope` defaults to `chat`; `session` pins the beat to the current session. On
`add`, `urgent` is a flag; on `update`, it requires `true` or `false`.

### All commands

| Command | What it does |
|---|---|
| `/cadence` or `/cadence help` | Show help |
| `/cadence list` | List beats in this chat |
| `/cadence show <id>` | Show one beat |
| `/cadence add ...` | Create a beat |
| `/cadence update ...` | Update a beat |
| `/cadence pause <id>` | Pause an active beat |
| `/cadence resume <id>` | Resume a paused beat |
| `/cadence cancel <id>` | Cancel an active beat |
| `/cadence delete <id>` | Delete a beat |

### Common examples

| Example | What it does |
|---|---|
| `/cadence add in 10m <note>` | One-shot reminder |
| `/cadence add urgent in 10m <note>` | One-shot; bypass depends on `urgent_bypass` |
| `/cadence add every 15m <note>` | Recurring beat |
| `/cadence add session in 10m <note>` | Anchored to this session |
| `/cadence add every 15m until 2h <note>` | Recurring with an expiry |
| `/cadence add every 15m max 12 <note>` | Recurring with a fire limit |
| `/cadence add at 16:30 <note>` | One-shot at a clock time |
| `/cadence add daily at 09:00,13:00 <note>` | Recurring at local clock times |
| `/cadence update <id> note <note>` | Edit the note |
| `/cadence update <id> in 20m <note>` | Reschedule |
| `/cadence update <id> daily at 09:00,13:00` | Switch to daily times |
| `/cadence update <id> urgent true` | Mark urgent |
| `/cadence update <id> urgent false` | Clear urgent |

### Tool actions

| Action | Fields |
|---|---|
| `create` | `note`, one of `at`, `every` or `daily`, optional `scope`, `until`, `max_fires`, `urgent` |
| `list` | — |
| `get` | `id` |
| `update` | `id`, plus any of `note`, `at`, `every`, `daily`, `until`, `max_fires`, `urgent` |
| `pause` | `id` |
| `resume` | `id` |
| `cancel` | `id` |
| `delete` | `id` |
| `respond` | `silent`; only valid on a beat turn |

The agent gets the same actions through the `cadence` tool. On a beat turn,
finish with `respond`: `silent=true` suppresses the final message,
`silent=false` delivers it. The tool schema is sent on every model call, so the
model knows this without an extra per-turn hint. If it forgets, an exact
`[SILENT]` is still suppressed as a defensive fallback.

### Receipts

Create and update results also include `quiet_hours_until`,
`spacing_until`, `cap_until`, `effective_next_at` and a `warning`. The warning
covers quiet hours, spacing, exhausted daily caps, expiry before the beat fires,
catch-up misses and missing human proof. If the beat is urgent but
`urgent_bypass` does not include `quiet_hours`, the warning says so. The
`/cadence` slash command prints the same warning. List output labels wait
reasons as `deferred: ...`, paused beats as `paused: ...`, and real failures as
`error: ...`.

## Chat vs session

| Level | What it is | How cadence uses it |
|---|---|---|
| **Chat** | One messaging destination: a Telegram DM, a Discord channel, a topic. Hermes identifies it with a `session_key`. | A beat stores the `session_key` that created it. There is no destination field and no broadcast. |
| **Session** | One conversation instance inside a chat. `/new` starts a new one. | `scope=chat` follows the chat across `/new`; `scope=session` stays pinned to the session that created it and waits if you switch away. |

## Scope

Cadence is intentionally small:

- No file, URL, process or command watchers.
- No cron jobs and no bundled skills.
- No new chat: a beat always returns to the chat that armed it.

For independent scheduled jobs, use Hermes cron. For watchers, use a cron
monitor.

## Compared with cron

| | cadence | Hermes cron |
|---|---|---|
| Runs in | the chat that armed it | a fresh session |
| Sees conversation history | yes | no |
| Best for | reminders, follow-ups, periodic checks | independent jobs |

## Platform support

- Linux/macOS: cross-process lock via `fcntl.flock`.
- Windows: cross-process lock via `msvcrt.locking`.
- Paths and SQLite use the standard library only; no shell calls.
- IANA zones come from `zoneinfo`. On Windows without `tzdata`, `UTC`/`GMT`
  still resolve; any other zone logs a warning and falls back to the local
  zone. Hermes already ships `tzdata` on Windows, so a normal install is fine.

## Security & privacy

- State lives in `<profile>/plugin-data/cadence/cadence.db` (SQLite, WAL).
- One scheduler thread per profile, protected by an OS file lock
  (`fcntl` on POSIX, `msvcrt` on Windows).
- Delivery follows the core permission described in
  [Configuration](#configuration); a beat never chooses another chat.
- No network calls, no credential reads, no shell commands.
- A process that dies mid-fire is recovered after the 300 s stale-claim window
  on the next poll. Delivery is at-least-once, not exactly-once.

## Layout

```text
cadence/                     plugin root
├── plugin.yaml
├── __init__.py              register(ctx) entry point
├── cadence/
│   ├── core/                domain model, store, scheduler, policy
│   ├── hermes/              Hermes boundary, runtime and registration
│   ├── protocols.py         shared Protocols (Clock, Injector, Hermes surfaces)
│   └── tool.py              model-facing tool and /cadence command
└── tests/
    ├── core/                engine, store, schedule, scope, policy
    ├── hermes/              hooks, registration, file lock
    └── tool/                tool and command
```

`pythonpath = tests` in `pytest.ini` keeps `tests/support.py` importable from
the subfolders without making `tests` a package (the plugin root is also named
`cadence`, so a `tests/__init__.py` would confuse the loader).

## Releasing

Bump `version:` in `plugin.yaml` and add the matching `## [x.y.z]` section to
`CHANGELOG.md` in the same commit. On `main`, after tests pass, CI creates the
`vX.Y.Z` tag and the GitHub release from that CHANGELOG section, and marks it
Latest. A commit that does not change the version does not create a release.

## Development

```powershell
# Create an isolated dev environment; Hermes never imports it.
uv venv .venv

# Install pytest, PyYAML and (on Windows) tzdata into that environment.
uv pip install --python .venv -r requirements-dev.txt

# Run the suite: core, hooks, tool, manifest defaults and the lock subprocess test.
uv run --python .venv --no-project python -m pytest -q

# Lint with the rules in ruff.toml, including ANN401 (no explicit Any).
uvx ruff check .

# Verify formatting without rewriting files; use `uvx ruff format .` to fix.
uvx ruff format --check .

# Strict type check; reads pyrightconfig.json (strict + Any as error).
uvx basedpyright
```

`uvx` downloads Ruff and basedpyright on demand, so they are not plugin
dependencies. The `.venv` exists so basedpyright can resolve `pytest` and
`PyYAML`; `--no-project` keeps `uv run` from looking for a `pyproject.toml`.
The test suite covers `tests/core`, `tests/hermes`, `tests/tool`, manifest
defaults and `urgent_bypass` parsing.

`.venv/` is dev-only: Hermes never imports it and `requirements-dev.txt` is
not a plugin dependency declaration. `pyrightconfig.json` points at it through
`venvPath`/`venv`, so the editor and the CLI resolve `pytest` fixtures without
per-file ignores.

`ruff.toml` and `pyrightconfig.json` live next to `plugin.yaml` on purpose: a
`pyproject.toml` there would be read by Hermes as a dependency declaration.

## License

MIT. See [LICENSE](LICENSE).
