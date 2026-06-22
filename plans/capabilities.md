# mypeople — capabilities (testable spec)

12 user-facing capabilities. Each one defines:
- **Action** — what the user does (the command)
- **Pane truth** — what's observable after, in the actual pane / filesystem / HTTP
- **Test** — a deterministic script you can run to prove it works

The Test column is what every SEED's `## Verify` block will derive from. If a capability has no testable pane truth, it isn't a capability.

## Architectural constraint — global identity scheme

Every agent has a globally-unique id of the form **`<host>/<session>:<tab>`**.
- `<host>` is the Tailscale node name (or `hostname -s` if not on a Tailnet).
- `<session>` is the tmux session name (no `mc-` prefix needed — host already disambiguates).
- `<tab>` is the tmux window name.

This means two hosts can both run a session called `test` without collisions: `mac-pro/test:Boss` and `cloud-1/test:Boss` are distinct addresses. Every Action/Test below assumes ids in this form.

A short form `<session>:<tab>` is allowed in CLI invocations when the host is unambiguous (current host) — the server canonicalizes to the long form before routing.

---

## 1. spawn

| | |
|---|---|
| **Action** | `mp spawn <project> --name <tab> -s <session> --backend claude` |
| **Pane truth** | A new tmux tab `mc-<session>:<tab>` exists; its pane shows the claude welcome banner. |
| **Test** | `mp spawn x --name w1 -s test --backend claude`<br>`tmux list-windows -t mc-test \| grep -q 'w1'`<br>`tmux capture-pane -t mc-test:w1 -p \| grep -q 'Claude Code v'` |

## 2. send

| | |
|---|---|
| **Action** | `mp send <session> --tab <tab> "PROMPT"` |
| **Pane truth** | `PROMPT` literally appears in the agent's pane, intact, with no garbled chars. |
| **Test** | `MARK="ping-$RANDOM"`<br>`mp send test --tab w1 "echo back: $MARK"`<br>`sleep 2 && tmux capture-pane -t mc-test:w1 -p \| grep -q "$MARK"` |

## 3. peek

| | |
|---|---|
| **Action** | `mp peek <session> --tab <tab>` |
| **Pane truth** | CLI output equals what `tmux capture-pane` would print for that pane. |
| **Test** | `EXPECTED=$(tmux capture-pane -t mc-test:w1 -p)`<br>`ACTUAL=$(mp peek test --tab w1)`<br>`[ "$EXPECTED" = "$ACTUAL" ]` |

## 4. kill

| | |
|---|---|
| **Action** | `mp kill <session> --tab <tab>` (or no `--tab` to kill the whole session) |
| **Pane truth** | The tab/session disappears from `tmux list-windows`/`tmux list-sessions`. |
| **Test** | `mp kill test --tab w1`<br>`! tmux list-windows -t mc-test \| grep -q 'w1'` |

## 5. status

| | |
|---|---|
| **Action** | `mp status` |
| **Pane truth** | Output lists every alive agent with a state token (idle / working / dead). One line per agent, format stable enough to parse. |
| **Test** | `mp spawn x --name w1 -s test --backend claude`<br>`mp spawn x --name w2 -s test --backend claude`<br>`mp status \| grep -q 'mc-test:w1.*\(idle\|working\)'`<br>`mp status \| grep -q 'mc-test:w2.*\(idle\|working\)'` |

## 6. boss-tab (master role + notification routing)

| | |
|---|---|
| **Action** | `mp spawn x --name Boss -s test --master` then `mp spawn x --name w1 -s test --boss mc-test:Boss` |
| **Pane truth** | When w1 finishes a turn, Boss's pane gains a line like `[AGENT NOTIFICATION] w1 (mc-test) finished: <summary>`. |
| **Test** | `mp spawn x --name Boss -s test --master --backend claude`<br>`mp spawn x --name w1 -s test --backend claude --boss mc-test:Boss`<br>`mp send test --tab w1 "Reply with the word PONG only"`<br>`# wait until Boss pane contains the notification`<br>`tmux capture-pane -t mc-test:Boss -p -S -200 \| grep -q '\[AGENT NOTIFICATION\] w1.*PONG'` |

## 7. stop-hook (per-spawn Claude Code plugin fires on every turn end)

| | |
|---|---|
| **Action** | (implicit — fires automatically when any spawned claude agent emits Stop) |
| **Pane truth** | After every turn, `status/<session>/<agent>.json` is written/updated with `status:"idle"`, fresh `timestamp`, and `summary` of last assistant message. |
| **Test** | `mp spawn x --name w1 -s test --backend claude`<br>`BEFORE=$(date -u +%s)`<br>`mp send test --tab w1 "Reply PONG"`<br>`# wait`<br>`TS=$(jq -r .timestamp status/mc-test/w1.json \| date -j -f "%Y-%m-%dT%H:%M:%SZ" +%s)`<br>`[ "$TS" -ge "$BEFORE" ]`<br>`jq -r .status status/mc-test/w1.json \| grep -q idle` |

## 8. status-files (per-agent JSON summary)

| | |
|---|---|
| **Action** | (same trigger as stop-hook — the file IS the artifact) |
| **Pane truth** | `status/<session>/<agent>.json` is a valid JSON object with keys `agent`, `session`, `status`, `timestamp`, `session_id`, `summary`. `summary` reflects the agent's last assistant message (≤200 chars). |
| **Test** | `mp send test --tab w1 "Reply PONG"`<br>`# wait`<br>`jq -e '.agent and .session and .status and .timestamp and .session_id and .summary' status/mc-test/w1.json`<br>`jq -r .summary status/mc-test/w1.json \| grep -q PONG` |

## 9. hud (browser dashboard)

| | |
|---|---|
| **Action** | open `http://127.0.0.1:9900/dashboard` in a browser |
| **Pane truth** | HTTP 200 + the rendered page lists every alive agent with state, last summary, last activity. For headless verification: queue-server's agent API (e.g. `/agents`) returns the alive agent in JSON. |
| **Test** | `mp spawn x --name w1 -s test --backend claude`<br>`curl -sf http://127.0.0.1:9900/dashboard \| grep -q '<html'`<br>`curl -sf -H "X-Queue-Secret: $SECRET" http://127.0.0.1:9900/agents \| jq -e '.[] \| select(.agent_id=="mc-test:w1")'` |

## 10. browser-attach (per-tab ttyd deep-link from HUD)

| | |
|---|---|
| **Action** | from the HUD (cap #9), click a specific agent row → opens `http://<host>:7681/?arg=session:<sess>&arg=window:<tab>` |
| **Pane truth** | HTTP 200; ttyd attaches the browser **directly to that specific tmux window**, not to the whole session — `tmux send-keys MARK Enter` into that window via curl/script appears on the rendered terminal. |
| **Test** | `mp spawn w1 -s test --backend claude`<br>`URL="http://127.0.0.1:7681/?arg=-tcmc-test%3Aw1"`  # ttyd arg to tmux attach -t<br>`curl -sf -o /dev/null -w '%{http_code}' "$URL" \| grep -q 200`<br>`# server-side: HUD's per-agent links contain the right tmux target string`<br>`curl -sf http://127.0.0.1:9900/dashboard \| grep -q "tmux-target=mc-test:w1"` |

## 11. backend-claude

| | |
|---|---|
| **Action** | `mp spawn x --name w1 -s test --backend claude` |
| **Pane truth** | The pane runs `claude` (not codex / pi / shell) — banner reads "Claude Code v…". `--plugin-dir` is on the running process so hooks fire. |
| **Test** | `mp spawn x --name w1 -s test --backend claude`<br>`tmux capture-pane -t mc-test:w1 -p \| grep -q 'Claude Code v'`<br>`ps -ax -o command \| grep 'claude.*plugin-dir' \| grep -v grep` |

## 12. cross-host routing + self-registration (via Tailscale)

| | |
|---|---|
| **Action** | Fresh host B comes up; runs the seed; **auto-registers** with the upstream queue-server on host A via Tailscale. From A: `mp spawn cloud-1/test:w1 --backend claude` (note long-form id with explicit host); `mp send cloud-1/test:w1 "msg"`. |
| **Pane truth** | Host B's `mc-test:w1` tmux window exists on B (not A). Messages from A land in B's pane. Notifications from the remote agent's Stop hook route back to whoever spawned it on A. `mp status` on A lists B under "clients". |
| **Transport** | Tailscale gives both containers stable, mutually-routable names. `QUEUE_URL` points at A's Tailscale name (e.g. `http://mac-pro.tail-net.ts.net:9900`). B's queue-client heartbeats to that URL using its own Tailscale identity. |
| **Self-registration handshake** | On first boot, B runs `tailscale up --authkey=$TS_AUTH_KEY`, then POSTs `/clients/register` once with `{"hostname":"cloud-1", "tailscale_name":"cloud-1.tail-net.ts.net"}`. A's queue-server records B as a known client; subsequent `/heartbeat` updates the liveness. |
| **Test** | Two containers A and B joined to same Tailnet, queue-server on A:<br>`# on B (after seed runs):`<br>`tailscale status \| grep -q "$(hostname)"`<br>`curl -fsS "http://A.ts.net:9900/health"`<br>`# on A:`<br>`mp status \| grep -q "cloud-1"   # B is registered`<br>`mp spawn cloud-1/test:w1 --backend claude`<br>`! tmux list-windows -t mc-test 2>/dev/null \| grep -q w1   # NOT on A`<br>`tailscale ssh tester@cloud-1 tmux list-windows -t mc-test \| grep -q w1   # IS on B`<br>`MARK="ping-$RANDOM"; mp send cloud-1/test:w1 "echo $MARK"`<br>`# wait`<br>`tailscale ssh tester@cloud-1 tmux capture-pane -t mc-test:w1 -p \| grep -q "$MARK"` |
| **Prereqs** | SEED's `## Inputs` collects: Tailscale **auth key** (non-interactive node join), upstream **queue-server URL** (Tailnet name), upstream **queue secret**. Tailscale daemon installed in a Step. |

---

## What this gives us

- A row per capability ⇒ a SEED `## Verify` block writes itself.
- A failing test is a failing capability — no "agent said DONE" loophole.
- Every SEED N declares which subset of capabilities it must satisfy; we never claim implemented without the corresponding test running green in a fresh container.

## What's NOT here (intentionally)

- approve/deny gating
- handoff verb
- watchdog
- lazy-detect
- disaster-recovery
- resume
- backend-codex, backend-pi, backend-terminal
- wiki / persistent memory
- file attachments
- voice notifications
- gh-pr-watcher, linq-poller, self-improve

If any of those needs to come back into v1, add a row above.
