# mypeople — engineer's handbook

You're reading this because you're about to work on mypeople. Read this first. It is short.

## The product

mypeople is a small, opinionated runtime for orchestrating Claude Code agents across one or many machines via an HTTP queue. Per-host queue-client + central queue-server + per-spawn `tmux-boss-hooks` plugin. Everything goes through the queue; agents never bypass to raw tmux.

The product is shipped as a single seed: `seeds/mypeople.seed.md`. Paste it into a fresh Debian-12 container with claude installed and TUN/NET_ADMIN exposed, and you get a working runtime — queue server, HUD, ttyd, Boss with doctrine, full agent loop — reachable via Tailscale.

## The doctrine you must internalize

The seed is the artifact; the running system is the proof. A seed without a passing `## Verify` from a brand-new container is a **draft**, not a release.

This project follows seedlab's *beloved engineer* doctrine: trust the pane (the actually-rendered terminal), not the agent's self-report. Hold writer + operator + verifier in one head so the iteration loop closes in five minutes.

## The development cycle (NON-NEGOTIABLE)

```
   ┌───── Idea (the change you're making) ──────────────┐
   │                                                    │
   ▼                                                    │
  Edit the seed                                         │
   │                                                    │
   ▼                                                    │
  Paste into a CLEAN container, run it                  │
   │                                                    │
   ▼                                                    │
  Watch what breaks ← it WILL break.                    │
   │   (the seed doesn't know about every host quirk;   │
   │    the implementation surfaces them)               │
   ▼                                                    │
  Fold the findings BACK into the seed text             │
   │   (heredocs, Steps, Verify, Failure modes —        │
   │    whatever the bug was, encode the fix)           │
   ▼                                                    │
  Cleanup → CLEAN container → re-paste → re-verify ─────┘
```

You exit the loop when a brand-new container pastes-and-Verifies the seed with zero ad-hoc fixes. Only then is the change shippable.

## What this means in practice

- **Do not skip the "paste into a clean container" step.** A seed that "works in my dev box" is a draft.
- **Do not trust the agent's `SEED_RESULT=DONE`.** Always run `## Verify` from an independent shell (`docker exec` works) — the agent's exit code from inside its own session is suspect; the pane is the truth.
- **Every bug the implementation surfaces is a spec bug.** If claude inside the container had to patch `~/.claude.json` to make trust work, that patch belongs in the seed's Steps. If Verify needed a longer wait because of an auto-update probe, that wait goes in the seed's queue-client. You don't get to leave bugs implicit "for the next person to figure out."
- **Never `git clone` mypeople from inside the container.** All custom code is inlined as heredocs in the seed. Paste-into-claude IS the install.

## Where things live

| Kind of thing | Path |
|---|---|
| The seed (the artifact) | `seeds/mypeople.seed.md` |
| Boss doctrine (source of truth; inlined into seed at install) | `plans/boss-claude.md` |
| CTO proxy-agent doctrine (source of truth for seed §8.5) | `plans/cto-claude.md` |
| User-facing feature list | `plans/features.md` |
| Capability spec | `plans/capabilities.md` |
| This handbook | `CLAUDE.md` |

## Hard rules

1. A seed is done only when a clean container one-shots it. No exceptions for "almost works."
2. The agent's transcript is not evidence. `docker exec` into a separate shell and prove via Verify.
3. Fold every finding back into the seed. Do not let the next engineer rediscover the same bug.
4. Read this doc before working on the seed. If you skipped it, go back.
