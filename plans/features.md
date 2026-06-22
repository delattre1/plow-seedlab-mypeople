# mypeople — features (v1)

The complete list of things mypeople does. **No spec here.** Spec (Action / Verify / failure modes) is written per-feature *after* it's implemented, against the real running thing — not invented up front.

## Core agent control

1. **spawn** — create a tmux tab and start a claude agent in it
2. **send** — deliver a message to a specific agent (durable, via queue)
3. **peek** — capture what's on an agent's pane right now
4. **kill** — graceful exit a tab or a whole session
5. **status** — list every alive agent + its state

## Topology

6. **boss-tab** — designate a master tab that owns the session; receives notifications from its workers

## Lifecycle visibility

7. **stop-hook** — agent emits a notification every time it finishes a turn (per-spawn plugin, no global config patching)
8. **status-files** — every Stop writes a JSON snapshot per agent (state, summary, timestamp, session_id)

## Observability

9. **hud** — live browser dashboard showing every alive agent and last summary
10. **browser-attach** — click an agent in the HUD → ttyd opens that exact tmux window in a browser tab

## Backends

11. **backend-claude** — agents run the Claude Code TUI

## Distribution

12. **cross-host (Tailscale)** — agents on different machines reachable by the same address scheme; fresh nodes self-register on first boot

## Architectural constraint

- **Global identity** — every agent is addressed as `<host>/<session>:<tab>`. Two hosts can run a session of the same name without collisions. Host comes first because the queue's first routing decision is which client to push to.

## Boss doctrine (behavior, not runtime — lives in `boss-claude.md`)

- **Plan-gate** — Boss refuses to launch engineering work until: brainstorm done, PLAN written, E2E Verify drafted, CEO approved.
- **Autonomous loop** — Boss reacts to every Stop notification within 30s. When team is idle with no work, asks the CEO for direction; doesn't go silent.

---

## What's NOT in v1

cut from tmux-boss: approve/deny gating · handoff verb · watchdog · lazy-detect · disaster-recovery · resume · backend-codex/pi/terminal · wiki / persistent memory · file attachments · voice notifications · gh-pr-watcher · linq-poller · self-improve.

Each can come back later as a separate, scoped addition. None blocks v1.
