# plow-seedlab-mypeople

**Single source of truth: the canonical `mypeople` seed (generative).**

`mypeople` is a small, opinionated runtime for orchestrating Claude Code agents across one or many
machines via an HTTP queue — per-host queue-client + central queue-server + per-spawn
`tmux-boss-hooks` plugin, a TODO board, a HUD, ttyd panes, an autonomous Boss, and a **CTO proxy
agent** (CEO-equivalent authority, phone-driven over WhatsApp via the existing Hermes/OpenClaw
bridge).

## What's in this repo

This is a **generative seed** repo, following the `plow-seedlab-*` convention (cf.
`plow-seedlab-video-editing`, `plow-seedlab-seedbed-substrate`). It carries the **artifact**, not the
generated runtime:

| Path | What it is |
|---|---|
| `mypeople.seed.md` | **The seed** — 100% generative prose. Paste it into a fresh Debian-12 container with `claude` authed + TUN/NET_ADMIN, and it generates + runs the whole runtime (queue server, HUD, TODO, ttyd, Boss, CTO) and self-Verifies. |
| `CLAUDE.md` | Engineer's handbook — the doctrine + development cycle for working on the seed. |
| `plans/boss-claude.md` | Boss doctrine (source of truth; inlined into the seed at install). |
| `plans/cto-claude.md` | CTO proxy-agent doctrine (source of truth for seed §8.5). |
| `plans/features.md`, `plans/capabilities.md` | User-facing feature list + capability spec. |

**No generated runtime code or live data lives here** — the seed *generates* `queue-server.py`,
`todo-server.py`, `todos.html`, the HUD, the Boss, and the CTO from the spec. A running deployment's
code + board data is an operational concern, kept out of the seed repo (that's what makes the seed
"100% instructions / 0% code").

## Using the seed

Paste `mypeople.seed.md` into `claude` inside a clean Debian-12 container (TUN + NET_ADMIN, Tailscale
auth key). It runs `## Steps`, generates every component, spawns the Boss + CTO, and runs `## Verify`
to exit 0. See the seed's own `## Verify` and `## 15. Verification journeys` for the acceptance gates.

## Doctrine

The seed is the artifact; the running system is the proof. A seed without a passing `## Verify` from
a **brand-new container** is a draft, not a release. See `CLAUDE.md` for the full development cycle.
