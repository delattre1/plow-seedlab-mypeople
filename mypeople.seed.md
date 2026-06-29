# SEED: mypeople

> seed-format: 1 · **generative**

> **You are an AI coding agent reading this seed.** This is a GENERATIVE seed (like
> `almanac.seed.md` / the teleprompter seed): it encodes **intent + contracts + acceptance
> journeys**, NOT source code. There is **ZERO pre-baked source here on purpose** — no
> full-program heredocs, no base64 blobs. **You GENERATE** the mypeople runtime from this spec
> with your own reasoning, install it on the host, then run **`## Verify`** — a self-contained
> acceptance harness whose **exit code is the truth**. On all gates green print
> `SEED_RESULT=DONE`; on a blocker print `BLOCKED_REASON=<short reason>` and stop.
>
> The contracts in §4–§8 are the load-bearing part: they are battle-hardened requirements
> (each was a real bug once). Satisfy every one. Because **you author every component in one
> pass, they interoperate by construction** — the protocol below pins only what must be exact
> for the gates and for host-quirk survival. Do not paste this seed's prose as code; write the
> code that satisfies it.

---

## 1. Purpose & mental model

mypeople is a small, self-hostable runtime for orchestrating **Claude Code agents** as a team
through an **HTTP queue**, with a **web HUD** and a **TODO board** as the human's front doors.
One node, self-contained, is the product target here.

Mental model — three moving parts the human sees, one that drives:
- **The Boss** (`main:Boss`): a long-lived Claude agent that owns the board, plans, and
  dispatches workers. Exactly one is always up (a **supervisor** guarantees it).
- **The HUD** (`:9900/dashboard`): a live web page listing agents (alive/dead), their summaries,
  and a per-agent **attach** link into their terminal. Plus a "retired engineers" table.
- **The TODO board** (`:9933/`): the human adds priorities; adding one **pings the Boss**, who
  triages/works it. The board and HUD are **one connected system** (cross-linked).
- **The queue** (`:9900`): the spine. `queue-server` holds the registry + task bus; each host
  runs a `queue-client` that heartbeats, registers its agents, and relays tasks into tmux.

The human's loop: open the TODO → add a task → the Boss is pinged → watch the Boss/work in the
HUD → click an agent's name (in a card thread) or the HUD attach link → land in that agent's
live terminal.

---

## 2. Technical approach (stack, prerequisites, constraints)

- **Language/runtime: Python 3 standard library only** for the daemons (`http.server`,
  `json`, `subprocess`, `threading`, `urllib`) — **no pip installs**. The web pages are static
  HTML+CSS+vanilla-JS served by the daemons. This keeps a bare Debian container sufficient.
- **Agents run in `tmux`**; the browser reaches a terminal via **`ttyd`** (one writable ttyd
  per host, `tmux attach`). Agents are **Claude Code** (`claude`), spawned non-interactively.
- **Tailnet (Tailscale)** gives the node a stable `100.x` IP so the HUD/TODO/ttyd are reachable
  from the human's machine. Userland `tailscaled` (no systemd) on a custom socket.
- **Substrate assumption (bare):** a fresh Debian-ish container with **`claude` installed +
  authenticated**, `python3`, `curl`, `tmux`, `sudo`, and `/dev/net/tun` + `NET_ADMIN`. Anything
  else (`jq`, `procps`, `ttyd`, `tailscale`) the **install step adds** — do not assume present.
- **Ports (fixed):** queue-server `9900`, TODO app `9933`, ttyd `7681`.
- **`$INSTALL_DIR`** defaults to `$HOME/mypeople`; layout `bin/ run/ status/ todos/ plugins/`.

---

## 3. Architecture & data model

- **agent_id = `<host>/<sess>:<tab>`** (e.g. `node-1/main:Boss`, `node-1/main:worker-1`). It maps
  1:1 to a tmux window: **session `mc-<sess>`, window `<tab>`** (so `main:Boss` ⇒ `mc-main:Boss`).
  This mapping is a hard contract — the HUD attach link, `mp peek/send`, and the supervisor all
  rely on it.
- **Registry (in queue-server, in-memory):** clients (hosts) and agents. Each agent record:
  `{agent_id, host, session, tab, backend, state(alive|dead), boss_id, summary, ts, tmux_target,
  attach_base, attach_url}`.
  - 🔴 **`/agents` MUST join the attach onto every row server-side** — do NOT make the HUD do the
    join. When the server builds the `/agents` response it looks up the owning client by `host`
    and copies that client's `attach_base` onto the agent record, AND emits a ready-built
    `attach_url = "<attach_base>/?arg=-t&arg=<tmux_target>"` (empty string only if the owning
    client has no `attach_base` yet). The HUD renders the per-row ATTACH button from `attach_url`
    for CROSS-NODE agents — but 🔴 **for an agent on the SAME node serving the HUD it MUST rebuild the
    host from `window.location.hostname` (the client's address), never the server-baked host** (§5.2,
    the client-host rule — this is what prevents a dead `127.0.0.1` link when the board is reached
    remotely). The server MUST NOT put `127.0.0.1`/`localhost` in `attach_base`/`attach_url` at all.
    No broken/empty cell for a live agent whose host is heartbeating. (Root cause of the
    empty-ATTACH-cell bug: `attach_base` lived only on `/clients`,
    never joined to the agent row, so the HUD had nothing to build the link from.)
- **Durability:** the registry is in-memory; each `queue-client` owns a **durable roster**
  (`run/roster.json`, every agent it ever spawned + spawn cmd + cwd + session-id + retire state)
  and an **agents.json** of currently-live ones, and **re-announces** them on every heartbeat —
  so a queue-server restart (or a reaper false-prune) **self-heals** within one heartbeat cycle.
  🔴 **RE-ANNOUNCE MUST be robust to a session/tab-less roster (2026-06-28 incident: a queue-daemon
  swap left a whole team de-registered).** The re-announce derives each agent's tmux window from
  `session`+`tab`; but a roster written by an older/other version (or rewritten by the server's own
  register path) may carry ONLY `agent_id` and no `session`/`tab`. The client MUST therefore **derive
  `session`/`tab` from the `agent_id` itself** (`<host>/<sess>:<tab>` → `session=<sess>`, `tab=<tab>`)
  whenever the roster fields are missing, so it always finds the live `mc-<sess>:<tab>` window and
  re-announces it. NEVER let a re-announce round-trip (server persists roster without session/tab →
  client can't re-derive) silently drop live agents from the registry — the agents' tmux sessions are
  alive; only the registration must heal. (Verify: corrupt the roster to agent_id-only, then assert the
  client still re-announces all live windows within one heartbeat.)
- **Status files:** `status/mc-<sess>/<tab>.json` = `{status(starting|working|idle|blocked), summary,
  timestamp, session_id, boss_id, backend, state}` — vocab **canonical: `working`, NOT `busy`**; written
  by the §4 lifecycle hooks. The HUD/`/agents` merges **both `summary` AND `status`** in (so the HUD can
  render the per-agent idle/working/blocked **badge** — §7.5), not only the alive/dead `state`.
- 🔴 **SPAWN/REVIVE COMMAND VISIBILITY (CEO 2026-06-26 — the HUD MUST show HOW each engineer was
  created).** `mp spawn` already records the exact launch command per agent in the durable roster
  (`run/roster.json` → `spawn_cmd`, see Durability above). The server MUST **join `spawn_cmd` onto every
  `/agents` row** (look it up by `agent_id` in the roster store — same join discipline as `attach_url`)
  and ALSO emit a derived **`revive_cmd` = `mp revive <agent_id>`** per row. The HUD then DISPLAYS both
  per engineer (§7 agents table) so the CEO can see — and copy — the precise command used to spawn each
  engineer and the command to revive it. A `/agents` row missing `spawn_cmd` (when the roster has one)
  or a HUD that doesn't surface it = FAIL (the CEO read its absence as "the HUD wasn't built right").
- **TODO board store:** `todos/board.v2.json` = `{version, order:[taskId…], pinSeq:<int>,
  tasks:{taskId:{id, text, state, assignee, pinned:<bool>, pinRank:<int|null>,
  comments:[{id,by,kind,body,ts}], …}}}`. `pinned`/`pinRank` (§7.3 PINNING) persist here like every
  other field; `pinSeq` is the board-level monotonic counter that hands out pin ranks.
- 🔴 **RUNTIME DATA ISOLATION (HARD — 2026-06-26 incident: a daily-driver board was WIPED).**
  ALL mutable runtime state — `todos/board.v2.json`, `run/` (roster, pidfiles, agent cwds),
  `status/` — lives **under `$INSTALL_DIR`** and is **per-install + per-`HOST_ID`**. Two consequences
  the install MUST guarantee:
  1. **No data-dir collision between instances.** Each instance binds its OWN `$INSTALL_DIR`,
     `HOST_ID`, ports, and `QUEUE_SECRET`; a second/parallel instance (e.g. a v2 / fresh hydrate)
     MUST use a DIFFERENT `$INSTALL_DIR` (different `$HOME`) so its `todos/board.v2.json` path can
     **never** be the same file as another running instance's. A hydrate writes only under its own
     `$INSTALL_DIR` — it is structurally incapable of writing another install's board.
  2. **Runtime data is NEVER inside a git-tracked tree.** The live board is mutated continuously; if
     `$INSTALL_DIR` (or the dir holding `todos/board.v2.json`) is a git working tree, a `git
     stash`/`checkout`/`reset` there will revert the live board to a stale commit and **wipe it**
     (exact root cause of the 2026-06-26 incident — the dev instance ran from a git checkout with the
     board git-TRACKED). RULE: keep `$INSTALL_DIR` out of any repo; if you must develop by running
     from a checkout, **gitignore the entire runtime data dir** (`todos/`, `run/`, `status/`,
     `board.v2.json*`) and `git rm --cached` it so no git op can ever touch the live board.
- 🔴 **DEFENSIVE BOARD BACKUP (defense-in-depth so a wipe is always recoverable).** `save(board)`
  MUST be **atomic** (write `board.v2.json.tmp` then `os.replace` — never a partial truncate) AND,
  before replacing, **roll a timestamped backup** `todos/board.v2.json.bak.<epoch>` keeping the **last
  ~20** (prune older). Additionally, **refuse a catastrophic shrink**: if the about-to-be-saved board
  has **&lt; 50% of the task count** of the on-disk board AND the on-disk board had &gt; 5 tasks, do
  NOT overwrite — write the new state to `board.v2.json.SUSPECT.<epoch>` and log loudly instead, so a
  bad reload can never silently clobber a full board. (Verify J-gate §below.)
- 🔴 **GIT-TRACKED BOARD EXPORT (separate-tree history + restore — card 2bf4e6c76a3a).** The rolling
  `.bak.*` layer above is same-dir/same-disk defense-in-depth; this is the **versioned history** layer
  that survives a full data-dir loss and recovers the board to **up-to-date**. Contract (build it
  generatively — a small exporter + a restore CLI, NOT pasted source):
  1. **A decoupled, READ-ONLY exporter** (its own process, NEVER inside `save()`) watches the live
     `board.v2.json` and, on every change, commits a canonical (sorted-key, pretty) snapshot into a
     **SEPARATE git repo in a SEPARATE directory OUTSIDE any working tree the server runs from**.
     🔴 **The repo path MUST be PER-INSTANCE, not per-host (boardgit 2026-06-26: keying by `HOST_ID`
     alone made two instances on the SAME host — e.g. `:9933` + a new `:9963` — collide into ONE backup
     repo, whose history then flip-flops between the two different boards after each edit).** The path
     MUST include an **instance discriminator** derived from THIS install, so every instance backs up to
     its OWN repo: `~/.mypeople/board-backup/<HOST_ID>-<INSTANCE>/` where `<INSTANCE>` is a stable short
     token unique to this install — e.g. the **`TODO_PORT`** and/or a short hash of the absolute
     **`$INSTALL_DIR`** (`sha1($INSTALL_DIR)[:8]`). Two installs that differ in `$INSTALL_DIR` OR port
     MUST resolve to DIFFERENT repo paths; never collapse to a bare `<HOST_ID>/`. (Overridable via
     `EXPORT_REPO` in `queue.env`.) Its own `.git`; every git command pinned to that repo
     (`git -C <EXPORT_REPO>`). FAIL if two co-hosted instances share a backup repo. The
     exporter **only reads** the live board and **only writes** into the export repo — it has **no code
     path that writes `board.v2.json`** and **never runs `git checkout/stash/reset` in the live tree**.
     This is what makes the backup mechanism *structurally incapable* of repeating the 2026-06-26 wipe.
  2. **Wipe is auto-detected, not silently promoted:** the exporter carries the same &lt;50%-shrink
     guard — a snapshot with &lt;50% of the last-good HEAD task count (and HEAD &gt; 5) is committed to a
     **quarantined `board.v2.json.SUSPECT.<epoch>` file, leaving `HEAD:board.v2.json` at the last good
     full board**, and pings the Boss. So a wipe can never become the new baseline.
  3. **`board-restore` is the ONLY writer of the live board on the recovery path** — manual, never
     automatic. It reads a snapshot via `git -C <EXPORT_REPO> show <ref>:board.v2.json` (default HEAD =
     current), **refuses an empty/unparseable snapshot**, **snapshots the current live board to
     `*.bak.prerestore.<epoch>` FIRST** (reversible), then writes the live board **atomically** (`.tmp`
     + `os.replace`, so the running server never reads a torn file). It performs **no git op in the
     live tree.** Restoring HEAD brings the board back to its last committed (= up-to-date) state.
  (Verify J-gate 25c.)

---

## 4. Protocol contracts (must be exact — these cross the wire / cross processes)

**Queue-server HTTP API** (bind `0.0.0.0:9900`; every route except `/health` and `/dashboard`
requires header `X-Queue-Secret: <QUEUE_SECRET>`; JSON bodies):
- `GET /health` → `{"status":"ok","uptime":N}` (public).
- `GET /clients` → array of `{hostname, attach_base, substrate_ready, last_seen, purpose,
  node_type, recording_url}` — the last three back the HUD machines-grid (§7.1): `purpose` =
  the hydration/group label (e.g. `mypeople`, `airbnb`); `node_type` ∈ {`one-shot-eng`,
  `long-lived-eng`, `system-agent`, `in-substrate-install-eng`}; `recording_url` = the node's
  seedrec recording link (may be empty).
- `GET /agents` → array of agent records (the HUD's source of truth for who's alive). **Each row MUST
  carry `spawn_cmd`** (joined from the roster store by `agent_id`, §3) **and `revive_cmd`**
  (`"mp revive <agent_id>"`) so the HUD can show how each engineer was spawned + how to revive it.
- `POST /agents/register` `{agent_id, backend, state, boss_id, is_master}`; `POST
  /agents/unregister` `{agent_id}`.
- `POST /heartbeat` `{hostname, attach_base, substrate_ready, purpose, node_type,
  recording_url, state}` → liveness + the host's re-announced agents. **`attach_base` contract:
  see §5.2.** `purpose`/`node_type`/`recording_url` are read from the node's config
  (`NODE_PURPOSE` / `NODE_TYPE` / `NODE_RECORDING_URL` in `queue.env`; `purpose` defaults to
  `mypeople`, `node_type` to `system-agent`). **`state` ∈ {`hydrating`,`ready`,`failed`}** is the
  node's hydration lifecycle (§5.11) — `hydrating` from bring-up, `ready` once the inner Verify
  passes. All surface in `/clients` for the §7.1 grid (which shows each node's `state`).
- `POST /task/submit` `{type(send|peek|kill|spawn|answer|revive), target_agent, payload}` →
  `{task_id}`; `GET /task/poll?hostname=<h>` (clients long/short-poll their tasks); `POST
  /task/result` `{task_id, ok, result}`; `GET /task/<id>` → task status+result (submitters wait
  on this).
- `GET /roster` → JSON array (retired/known engineers, for the HUD revive table).
- `GET /dashboard` → the HUD HTML (**public**). **The page carries NO secret (§5.12).** Serving it
  mints a browser session (httpOnly cookie); its same-origin JS calls the gated endpoints with the
  cookie auto-sent — the QUEUE_SECRET never reaches the browser.

**`mp` CLI** (in `$INSTALL_DIR/bin/mp`, on `PATH`): verbs `status, spawn, send, peek, kill,
answer, revive`.
- `mp spawn <agent_id> [--backend claude] [--cwd PATH] [--boss <agent_id>] [--master]` — creates
  the tmux window `mc-<sess>:<tab>`, launches the backend, registers the agent. `--master` also
  sends the Boss its onboarding prompt (read `boss-CLAUDE.md`). **Idempotent:** spawning an
  agent_id whose window already exists reuses it, never double-launches.
  - 🔴 **The tmux window MUST be NAMED exactly `<tab>` and that name MUST STICK** — the attach URL
    the HUD/TODO build is `…/?arg=-t&arg=mc-<sess>:<tab>`, i.e. `tmux attach -t mc-<sess>:<tab>`
    resolves the window **by name**. Two ways this silently breaks (both are PRODUCT bugs, not
    host quirks):
    1. **Not naming it.** `tmux new-session -d -s mc-<sess>` / `new-window -t mc-<sess>` without
       `-n <tab>` gives the window a default name → `attach -t mc-<sess>:<tab>` →
       `can't find window: <tab>`. ⇒ ALWAYS pass `-n <tab>` (`new-session -d -s mc-<sess> -n <tab> …`
       for the first/`--master` window; `new-window -t mc-<sess> -n <tab> …` for the rest).
    2. **tmux auto-renaming it.** tmux's `automatic-rename`/`allow-rename` is **ON by default**, so
       the moment the backend runs, tmux renames the window to the foreground command
       (`node`/`claude`) and `mc-<sess>:<tab>` no longer resolves. ⇒ IMMEDIATELY after creating the
       window, disable it: `tmux set-option -t mc-<sess>:<tab> -w automatic-rename off \; set-option
       -t mc-<sess>:<tab> -w allow-rename off` (and set `set-option -g automatic-rename off` once at
       session create). Then re-assert the name: `tmux rename-window -t mc-<sess>:<tab> <tab>` won't
       be clobbered.
  - **SELF-CHECK the generating agent MUST run after spawning the Boss** (so it can't skip this like
    a soft note): `tmux list-windows -t mc-main -F '#{window_name}' | grep -qx Boss && echo
    WINDOW_OK` MUST print `WINDOW_OK`, AND `tmux attach -t mc-main:Boss` (run headless via
    `tmux -C` or a 1-line `tmux display-message -pt mc-main:Boss '#{window_name}'`) MUST return
    `Boss`, NOT `can't find window`. If either fails, the spawn is defective — fix the window
    naming, do not ship.
  - 🔴 **NESTED SPAWN MUST NOT DISCONNECT ANYTHING — `mp spawn` is called FROM INSIDE an agent's
    own tmux pane** (an engineer born from a TODO comment runs `mp spawn` to create another
    engineer). The session `mc-<sess>` and every existing window (and any attached ttyd client)
    MUST survive untouched. Root causes that drop the session/ttyd, all FORBIDDEN:
    1. **Clobbering the session.** NEVER `tmux kill-session`/`kill-server` in spawn, and NEVER
       `tmux new-session -s mc-<sess>` unconditionally — a second `new-session` on an existing name
       either errors or (if paired with a "kill-session first" for idempotency) **destroys the
       running session, killing every agent + dropping ttyd**. ⇒ create the session ONCE,
       idempotently: `tmux has-session -t mc-<sess> 2>/dev/null || tmux new-session -d -s mc-<sess>
       -n <tab> …`; every additional agent uses `tmux new-window -t mc-<sess> -n <tab> …` ONLY.
    2. **Nesting from inside a pane.** Because the caller already runs inside tmux (`$TMUX` set),
       a bare `tmux new-session` warns/nests and `tmux switch-client`/`attach`/`select-window`
       would **yank the attached ttyd client to another window = the human's disconnect**. ⇒ spawn
       MUST use only **detached, explicitly-targeted** commands (`new-window -d -t mc-<sess>`,
       `send-keys -t mc-<sess>:<tab>`), NEVER `switch-client`/`select-window`/`attach`, and should
       run them with `TMUX= ` cleared (or via the absolute socket) so the caller's pane is never
       the implicit target.
    3. **ttyd churn.** A new window appearing in a session does NOT disconnect a tmux client by
       itself — so do NOT restart ttyd or kill the session on spawn; ttyd's attached client keeps
       its window. Adding a window is invisible to an attached `-t mc-<sess>:<other>` client.
    - **SELF-CHECK (the generating agent MUST run it):** from inside the Boss/an engineer pane,
      capture the attached client, `mp spawn` a child engineer, then assert the parent session +
      client are intact: `tmux has-session -t mc-main` still true, the pre-existing windows still
      listed, and `tmux list-clients -t mc-main` shows the same attachment (no drop). If the spawn
      killed/switched the session, it is defective — do not ship.
- `mp send <agent_id> <msg>` — delivers `msg` into the agent's tmux composer and submits it
  (bracketed-paste + Enter, with retry). `mp peek <agent_id>` — returns the agent's live pane +
  a classified state (IDLE/WORKING/BLOCKED). `mp kill <agent_id> [--reason …]` — retires it.
  `mp answer <agent_id> <N>` — selects option N of a pending AskUserQuestion. `mp status` — lists
  agents + heartbeating clients.

**Claude Code hooks plugin** (`plugins/tmux-boss-hooks`, installed per-spawn via
`claude … --plugin-dir`): emits lifecycle events `SessionStart, UserPromptSubmit, Stop, PreToolUse,
SessionEnd` to the queue/status files. The **Stop hook** writes the agent's status+summary and, if the
agent has a `boss_id`, routes an `[AGENT NOTIFICATION] <agent_id> finished: <summary>` line into
the **Boss's tmux pane** (`mc-<boss-sess>:<boss-tab>`). This is the JOIN/notification proof.
**Status state machine — MUST emit `working`, not only `idle` (folded 2026-06-25, CEO bug).** The
status file's `status` field is what the HUD/Terminal-Wall badge reads, so it MUST transition through
the full lifecycle: `SessionStart→"starting"`, **`UserPromptSubmit→"working"`** (a submitted prompt =
the agent STARTED a turn), `PreToolUse(AskUserQuestion)→"blocked"`, `Stop→"idle"`. **Omitting the
`UserPromptSubmit→working` hook is a real bug we hit:** without it the file only ever goes
`starting→idle`, so EVERY agent shows IDLE on the wall even while it churns mid-turn. The
`UserPromptSubmit` branch writes the status file (set `status:"working"`, refresh timestamp/session_id,
preserve summary) and **exits SILENTLY — it must print NOTHING to stdout**, because UserPromptSubmit
hook stdout is injected into the agent's context. Only the Stop hook notifies the Boss; the
`working` write is status-file-only.
**Stop-hook flush race (folded 2026-06-17):** the Stop hook can fire BEFORE the final assistant
message is flushed to the transcript → empty summary. The hook must **retry reading the transcript
(~4×/0.5s)** and fall back to locating it by `session_id` under `~/.claude/projects` before giving
up — never emit an empty summary on the first miss.
**Status-file write MUST be atomic with a UNIQUE temp (folded 2026-06-26, real bug from a fresh
hydrate):** multiple lifecycle hooks for the SAME agent can fire concurrently (e.g. `Stop` while a
`UserPromptSubmit` is mid-write); if they share one temp path (`<file>.tmp`) they interleave and
**corrupt the status JSON**. Each status write MUST go to a **process/PID-unique** temp
(`<file>.<pid>.<epoch>.tmp`) then `os.replace` onto the final path (atomic rename), so concurrent
hook writes never clobber each other or leave a half-written file. (Same atomic-rename discipline as
the board store, §3.)

---

## 5. Hard-won CONTRACTS (battle-hardened — each was a real bug; satisfy ALL)

**5.1 `mp` must be on the PATH of every long-running daemon that calls it.** The TODO server
pings the Boss by shelling `mp send main:Boss …`, resolving `mp` via `shutil.which("mp")`. A
nohup'd daemon does **not** inherit an interactive shell's PATH, so it MUST be launched with
`PATH="$HOME/.local/bin:$INSTALL_DIR/bin:$PATH"`. **If `mp` is not on PATH, `boss_ping` silently
no-ops and add-task never reaches the Boss** (the worst kind of bug — board updates, Boss never
told). Generated launchers must guarantee this; Verify asserts the ping lands (§15 J3).

**5.2 The ATTACH button must open an address reachable from the CLIENT — never `127.0.0.1`/
`localhost`/a docker-internal IP (🔴 HARD; CEO 2026-06-24 — his HUD attach link was
`http://127.0.0.1:7682/?arg=…` and dead, because 127.0.0.1 is the CEO's OWN laptop, not the
container he reached over the tailnet).** The rule mirrors the §ITEM-2 cross-nav fix: the attach
host must be whatever host the CLIENT used to reach the board, NOT a server-baked loopback.
🔴 **THE ATTACH LINK IS A 3-LAYER CHAIN — every layer must resolve to a client-reachable host, and
NONE may fall back to `127.0.0.1` (CEO 2026-06-24, Phase-3 consensus: the bug was a 3-LAYER FALLBACK
COLLAPSE — each layer independently defaulted to loopback, so the final href was a dead
`http://127.0.0.1:7682/…`. Fixing only the page (dashboard.html) is INCOMPLETE — fix all three):**
  - **LAYER (a) — `queue-client.py` (the heartbeat) MUST auto-derive `attach_base` from
    `tailscale ip -4`, UNGATED.** Compute `attach_base = "http://<tailscale-100.x>:<ttyd-port>"` at
    heartbeat time from the live tailnet IP. **Do NOT gate this on `TTYD_PUBLIC_URL` (or any env)** —
    that was the root defect: when `TTYD_PUBLIC_URL` was unset the client fell back to
    `http://127.0.0.1:<ttyd>`. The fallback order is **tailnet IP → (explicit `TTYD_PUBLIC_URL` if
    set) → empty string**, NEVER `127.0.0.1`. If no tailnet IP is resolvable yet, advertise an EMPTY
    `attach_base` (the server then emits no attach_url, and the page falls back to the §below
    client-host derivation) — an empty base is recoverable; a `127.0.0.1` base is a silent dead link.
  - **LAYER (b) — `queue-server` `/agents` JOINS that `attach_base` and emits `attach_url`** (the §4
    contract): looks up the owning client's `attach_base`, copies it onto every agent row, and builds
    `attach_url = "<attach_base>/?arg=-t&arg=<tmux_target>"`. It MUST NOT substitute a `127.0.0.1`
    default when `attach_base` is empty — emit empty and let layer (c) derive client-side.
  - **LAYER (c) — `dashboard.html` builds the href from `attach_url` / the client host** (below),
    never a literal `127.0.0.1`.
  Gated end-to-end by J47 (all three layers asserted).
- 🔴 **CLIENT-HOST DERIVATION (the fix).** For an agent that lives on the **same node serving this
  HUD** (the standalone/product case — the dominant one), the HUD builds the ATTACH href CLIENT-SIDE
  from **`window.location.hostname`** (+ the ttyd port + the agent's tmux target):
  `href = ${location.protocol}//${location.hostname}:${TTYD_PORT}/?arg=-t&arg=${tmux_target}`.
  This is correct in BOTH required cases with no server knowledge of how it was reached:
  (a) **local install** → the client is at `localhost`, so the attach opens `localhost:<ttyd>` and
  works on the same machine; (b) **container reached remotely over the tailnet** → the client reached
  the board at `http://<node-100.x>:<port>`, so the attach opens `<node-100.x>:<ttyd>` — the
  CONTAINER's ttyd, reachable. Behind a reverse-proxy the host is likewise the proxy host the client
  used. **Never emit a hardcoded `127.0.0.1`/`localhost`/`172.17.x`/inner-bind host in the attach
  href.**
- **CROSS-NODE (fleet) agents** — an agent whose `host` differs from the node serving the HUD — use
  that agent's **registered tailnet `attach_base`** from the registry (the §4 server-joined value,
  which itself MUST be `http://<tailscale-100.x-ip>:<ttyd>`, from `tailscale ip -4`; **on a
  userland/no-systemd tailscaled, symlink the default socket → the custom socket** (see 5.6) so the
  bare `tailscale` CLI resolves it). A docker-bridge `http://172.17.0.x:<ttyd>` or a `127.0.0.1`
  attach_base is dead from the human's machine and is FORBIDDEN in any heartbeat/registry value.
- 🔴 **ttyd MUST BIND A CLIENT-REACHABLE INTERFACE.** The attach host above only resolves if ttyd is
  actually listening where the client points. So the product ttyd binds **`0.0.0.0:<ttyd-port>`**
  (all interfaces, incl. the tailnet `100.x`), NEVER `127.0.0.1`-only. (Loopback-only ttyd is the
  second half of this bug: even a correct tailnet host can't reach a ttyd bound to 127.0.0.1.)
A live agent row whose host is heartbeating MUST show a working ATTACH button that opens the live
pane FROM THE CLIENT'S machine — never an empty cell, never a dead 127.0.0.1 link (§15 J47).

**5.3 Boss supervisor — always exactly one Boss is up.** A tiny userland loop (own pidfile,
`setsid`, survives the installing shell) checks every ~15s whether the tmux window
`mc-main:Boss` exists; if **absent**, it auto-respawns `mp spawn <host>/main:Boss --master`
(re-onboards from `boss-CLAUDE.md`) — **no human, no "ask another agent."** It must be idempotent
(only spawns when genuinely absent) and key off the **tmux window** (source of truth), not the
queue (a transient queue blip must not trigger a double-spawn). If a Boss can't be brought up,
surface a loud error. Verify kills the Boss and asserts it reappears (§15 J4).

**5.3b TODO server: supervise it (like the Boss) + module-level imports + Verify must HIT :9933.**
Two real one-shot failures observed in the 5-node install test (mp-3), both reproducible holes:
(1) **Import scoping.** The generated `todo-server.py` raised `UnboundLocalError: cannot access
local variable 'urllib'` in `do_GET` and the server **died on the FIRST request**. ALWAYS import
`urllib.parse` / `urllib.request` (and every stdlib module a handler uses) at **MODULE level**;
**NEVER** re-import inside a method — a local `import urllib…` in any handler makes `urllib`
function-local, so an earlier `urllib.parse.…` in that handler raises UnboundLocalError. (2)
**No supervision.** Unlike the Boss (5.3), `todo-server` was not supervised, so that crash left
`:9933` **permanently dead** while queue-server + HUD + Boss stayed up — a silent PARTIAL that the
agent still self-reported as PASS. The TODO server MUST run under a userland restart-loop (own
pidfile, `setsid`, survives the install shell) that respawns it within ~15s whenever `:9933` stops
answering, exactly like the Boss supervisor. Verify MUST hard-assert `curl :9933/ → 200` **and**
kill-then-respawn `:9933` (a "process launched" proxy or the agent's self-report is NOT acceptance —
the install test went green with `:9933` down; the gate must catch that).

**5.4 Per-node fresh Claude login; NEVER copy a token/volume between nodes.** Each node device-
logs into its OWN credential store once. Copying a live token to a second node rotates refresh
tokens and breaks BOTH (incl. a shared upstream). Generated code/process must never copy auth.
(Auth itself is the substrate's one human step; this seed assumes `claude` is already authed.)

**5.5 First-run config is an OPERATOR/INSTALLER PRECONDITION — established by writing CONFIG FILES,
NOT by the product-generating agent suppressing its own safety dialogs (reframed 2026-06-24, CEO —
Phase 4: fresh Bosses REFUSE on principle when the seed tells them to "suppress their first-run
gates" / auto-dismiss the Bypass-Permissions confirmation; they read it as being asked to defeat
their own safety controls. The fix is to make the sandbox's trust + permission posture the
OPERATOR's job, set up FRONT, so the agent is never asked to dismiss anything).**
🔴 **Who does it:** a small **installer step — a generated `install.sh` the OPERATOR runs (or, on a
throwaway substrate, the harness at provision)** — establishes the sandbox config BEFORE the
product-generating agent starts. The agent then runs in an environment the sandbox owner has already
trusted and authorized; it only GENERATES the product. **What it writes (pure config, idempotent
file writes — no dialog interaction):**
- In `~/.claude.json`: `hasCompletedOnboarding:true` (+ `lastOnboardingVersion`, `theme:"dark"`) so
  no onboarding dialog; and the folder-trust pre-accept (§5.5c).
- In `~/.claude/settings.json`: **`skipDangerousModePermissionPrompt:true`** — this is the supported
  config that makes the **"Bypass Permissions mode — accept?"** prompt NOT appear at all, so the
  autonomous (`--dangerously-skip-permissions`) launch starts clean. **This REPLACES the old
  "`mp spawn` auto-detects the bypass dialog and sends `2`+Enter" hack** — that hand-dismissal is the
  exact thing a safety-conscious agent refuses to do; setting the config up front, as the operator,
  yields the identical end state with no suppression. (Root cause of the first-Boss-killed bug still
  holds historically: an onboarding paste landing on an un-pre-set bypass dialog could select "No,
  exit" and kill the only window — pre-setting the config removes that failure mode at the source.)
- A live agent never needs to touch any of this; the config is already correct when it launches.
**5.5b — bracketed paste needs
a second Enter:** a multi-line prompt renders collapsed as `[Pasted text #1]` and a single Enter
does NOT reliably submit; `mp send`/`spawn` must send Enter, wait ~0.4s, then send a **second
Enter** (a redundant Enter on an empty composer is a harmless no-op). Verify proves a Boss actually
spawns AND survives (§15 J2/J4).
**5.5c — folder-trust must be pre-accepted for ANY working dir (folded 2026-06-18, CEO).** Claude
Code shows a **"Accessing workspace … — trust this folder?"** dialog the first time an agent opens a
directory not yet trusted — keyed PER exact path in `~/.claude.json` under `projects`. Pre-trusting
only the MAIN cwd is not enough: a spawn in a NEW/different cwd (e.g. `~/mypeople/run/eng`)
re-triggers the dialog and BLOCKS the agent.
> **🔴 THIS IS THE PRODUCT'S OWN JOB — a real user installs mypeople on a VANILLA machine with NO
> pre-seeded trust, so the SEED'S INSTALL + the generated `mp` MUST handle folder-trust themselves
> (re-folded 2026-06-18, CEO). Do NOT assume any substrate/golden-image bake — that only helps OUR
> harness; the product must stand alone.** Two MANDATORY product-level mechanisms:
1. **The install (§12 Steps) MUST, on a fresh `~/.claude.json`, write the trust pre-accept itself** —
   MERGE (preserve existing keys): `projects["$HOME"].hasTrustDialogAccepted=true` (parent → descendant
   cwds inherit) AND `$INSTALL_DIR`, `$INSTALL_DIR/run`, `$INSTALL_DIR/run/eng`, `…/run/boss`, `…/bin`.
   This is part of the generated install on the USER's machine, not provisioned from outside.
2. **The generated `mp spawn` MUST, for the exact `--cwd` it launches in, set
   `projects[<that cwd>].hasTrustDialogAccepted=true` (merge) in `~/.claude.json` BEFORE exec'ing
   `claude`** — so ANY spawn cwd (even one not pre-listed) is trusted by construction. (Same
   robustness as the §5.5 bypass-dialog auto-dismiss.)
   **SELF-CHECK the generating agent runs before declaring done (on a CLEAN trust state):**
   `python3 -c 'import json,os;d=json.load(open(os.path.expanduser("~/.claude.json")));print(d["projects"].get(os.path.expanduser("~/mypeople/run/boss"),{}).get("hasTrustDialogAccepted"))'`
   → MUST print `True` after the install (i.e. the install pre-trusted the Boss cwd). And `mp spawn`
   in a brand-new `mkdir`'d dir MUST reach the bypass banner with no trust prompt.
3. (Harness-only, NOT the product's mechanism) OUR golden image may also bake trust for fast OUR-side
   spins — but **this MUST NOT be what makes the product pass**; J35 tests on a vanilla trust state.

**5.6 Tailnet on a no-systemd container = userland `tailscaled` on a custom socket + a default-
socket symlink.** Start `tailscaled --state=<dir>/tailscaled.state --socket=<dir>/tailscaled.sock`
under `$INSTALL_DIR/run/tailscale-state/`, `tailscale --socket=<sock> up …`, then
`ln -sf <sock> /var/run/tailscale/tailscaled.sock` so the **bare** `tailscale` CLI (and 5.2's
`tailscale ip -4`) work. Needs `/dev/net/tun` + `NET_ADMIN`.
> **`tailscale up --accept-dns=false` — MagicDNS MUST NOT hijack `/etc/resolv.conf` (folded
> 2026-06-18, fresh-run substrate fix).** By default `tailscale up` lets MagicDNS rewrite
> `/etc/resolv.conf` to the tailnet resolver (100.100.100.100), which **breaks public DNS inside the
> container — github.com / npm / apt resolution fails mid-hydration**. ALWAYS pass
> **`--accept-dns=false`** so the container keeps its working resolver. If anything already clobbered
> `/etc/resolv.conf`, restore a public resolver (e.g. `nameserver 8.8.8.8`/`1.1.1.1`) as a fallback.
> (Tailnet IP reachability — §5.2/J10 — does NOT need MagicDNS; we use the `100.x` IP directly.)
> **ALWAYS join NON-INTERACTIVELY via `TS_AUTHKEY` — an interactive `tailscale up` is THE BUG to
> prevent (folded 2026-06-18, CEO; supersedes the earlier skip-tailnet idea).** `tailscale up` MUST
> be passed a pre-provisioned non-interactive **`TS_AUTHKEY`** (`tailscale up --authkey="$TS_AUTHKEY"
> …`) so it joins the tailnet WITHOUT ever opening a `login.tailscale.com` browser device-login.
> **Every node that hydrates this seed — including an inner/nested product container — is provisioned
> the authkey by its substrate** (the substrate mints/passes `TS_AUTHKEY` into the inner before
> hydration; reuse the same non-interactive key the outer already used, or mint a fresh ephemeral
> one). **NEVER skip tailnet, and NEVER fall into interactive `tailscale up`.** If `TS_AUTHKEY` is
> absent, that is a **PROVISIONING ERROR to surface and fix** (`echo BLOCKED_REASON=no_ts_authkey;
> exit 1`), **not** a reason to skip the join. With the key, the join is clean + headless and J10
> runs normally.

**5.7 ttyd: one writable instance, per-tab attach via URL args — each attach lands on ITS OWN
window, isolated per viewer.** Run `ttyd -W -a -p 7681 <attach-helper>`. `-a` (allow URL args) is
mandatory so `?arg=-t&arg=mc-<sess>:<tab>` reaches the helper. **Verify ttyd FUNCTIONALLY (HTTP
200 on the attach URL) and by bare option name — ttyd 1.7.x rewrites argv for `ps`
(`-t key=value` shows as `key value`), so never grep for `disableLeaveAlert=true`.** A stray
`pkill` must not blank the human's window: run ttyd under a supervisor (respawn within ~2s).
🔴 **Do NOT run a bare `tmux attach -t mc-<sess>:<tab>` per connection.** All clients attached to
the SAME tmux session share ONE current-window (tmux forces every client of a session to the same
active window), so attaching to the Boss while an engineer window is active **lands on the engineer
pane** (the CEO's "attach opened the wrong window" bug) — and selecting the target would yank every
other viewer too. ⇒ The attach-helper MUST give each connection its **own window selection via a
grouped session** — created **ATTACHED in ttyd's pty in ONE command**, NEVER detached-then-attached:
```
exec tmux new-session -t mc-<sess> -s _v_<tab>_<uniq> \; select-window -t <tab> \; \
     set-option destroy-unattached on
```
`new-session -t mc-<sess>` (a grouped session: shares the windows, **INDEPENDENT current-window**)
runs **WITHOUT `-d`**, so tmux attaches it directly in ttyd's pty — the session has a client the
instant it exists. `select-window -t <tab>` lands on exactly this row's window; `set-option
destroy-unattached on` runs LAST, on the now-ATTACHED session, so it only reaps on a later
disconnect. `<uniq>` = `$$`+`$RANDOM` (or `$(date +%s%N)`) to avoid name collisions.
🔴 **The fatal regression to avoid (CEO 2026-06-18):** do **NOT** `tmux new-session -d …` then `set
destroy-unattached on` then a separate `attach` — the `-d` session starts with NO client, so
`destroy-unattached on` **reaps it instantly** and the follow-up `attach` fails with `can't find
session: _v_<tab>_…` + ttyd's "Press Enter to Reconnect" (which never recovers). Create-attached in
one command; set destroy-unattached only after it's attached. The clickable row's `attach_url`
(§4/§5.2) still carries `-t mc-<sess>:<tab>`; the helper does the grouping.

**5.8 Daemons are detached + pid-tracked + idempotently restartable.** Start with `setsid …
</dev/null &`, write a pidfile, and a reinstall stops the prior by pidfile then restarts — never
leave a duplicate. A self-install must not kill the very channel that is driving it: stop a
prior daemon only immediately before relaunching it (graceful in-place handoff), not pre-emptively.

**5.9 Heartbeat-based liveness + self-healing registry + STALE-CLIENT EXPIRY.** queue-server reaps
an agent whose host has been silent `QUEUE_DEAD_AFTER` (≈4 missed heartbeats); clients re-announce
their live agents every heartbeat so a server restart / false-prune repopulates within one cycle.
No zombie "alive" agents after a host dies. **Likewise `/clients` itself EXPIRES stale entries
(folded 2026-06-17):** a client whose `last_seen` is older than the TTL (≈3–5 heartbeat intervals)
**drops off `/clients`** — so a node that registered (e.g. `hydrating`) and then died, or any probe
that stopped heartbeating, does NOT linger forever as a dead phantom on the grid. The grid reflects
only currently-live registrants. (J26 asserts the fresh grid is clean.)

**5.10 UTF-8 everywhere.** Set `LANG=C.UTF-8`/`LC_ALL=C.UTF-8` for the queue-client/tmux so the
TUI glyphs (`❯ ● ✻ …`) aren't mangled to underscores.

**5.11 TWO ISOLATED PLANES — the node is ALWAYS visible on the central grid (never an island).**
> **FLEET-MODE ONLY.** This whole contract applies **only when `UPSTREAM_QUEUE_URL` is set** (the
> node is JOINing an existing fleet central). **A STANDALONE install (no upstream — the default
> product, §1, what a fresh-from-zero user gets) does NOT run the OUTER plane at all:** there is no
> uplink, and **J12/J13 are SKIPPED**. Standalone, the node's OWN inner `:9900` HUD is the central
> and its `/dashboard` grid shows its own agents. Never require a pre-existing hub to pass Verify.

When fleet mode IS on, a substrate runs **two mypeople setups that must not touch each other:**

- **OUTER — fleet uplink (JOIN), UPLINK-FIRST.** A thin queue-client in its **OWN dir + config**
  (`$UPLINK_DIR`, default `$HOME/mypeople-uplink`; config `~/.config/mypeople/upstream.env`) that
  **only connects OUT** to the central queue (`UPSTREAM_QUEUE_URL` + `UPSTREAM_QUEUE_SECRET`) —
  it **binds NO local ports** and runs **no** local queue-server.
  **It MUST start FIRST — the very first action of the build (Step 1), BEFORE OS-deps / before
  generating the inner product / before the inner is up** — and register the node immediately
  with **`state=hydrating`** + `purpose` (and `hostname`, tailnet `attach_base` when available,
  `node_type`). It heartbeats **throughout** the whole hydration and **re-announces the node's
  live agents — including the INNER product's `main:Boss` once spawned**, so the node + its Boss
  show on the **central HUD machines-grid (§7.1)**. **State lifecycle:** `hydrating` (from
  bring-up, while generating/installing) → **`ready`** (only when the inner `## Verify` passes) →
  or **`failed`** (on a blocker). **WHY uplink-first:** the CEO must SEE "N substrates hydrating"
  *while* they hydrate — a node must appear on the grid **the moment it starts**, not ~minutes
  later when its inner finishes. (Gap that forced this: the uplink started late → a node was
  invisible on the hub for the entire ~build, so 5 generating nodes showed as 1.) This is the
  plane the install **engineer** lives on and the CEO watches from the first second.
- **INNER — the product (self-contained).** The mypeople the agent installs: its own
  queue-server/HUD/TODO/Boss in `$INSTALL_DIR` (`$HOME/mypeople`), owning the **local ports
  9900/9933/7681**, queue.env → `127.0.0.1`.

**Isolation contract (this is the whole point — the prior bug was the planes shared state):**
the two planes have **separate dirs, configs, pidfiles, and queue.env files**; the OUTER binds
no ports (so no port clash) and the INNER's lifecycle (Step 2 daemon-stops, Step 8 queue.env
rewrite, Step 9 graceful handoff, §5.8) **may only touch INNER state — never the OUTER uplink.**
Installing/restarting the inner product must be **incapable** of stopping, rewriting, or
re-pointing the outer uplink. Result: the node **never goes dark on the central grid** while it
self-hosts. (Root cause of the defect: the install-flow reused ONE `queue.env`/queue-client/dir/
ports for both, so installing the inner clobbered the outer JOIN → the node vanished from the
CEO's HUD.) "I asked for X substrates, I see X" (§7.1) depends on this isolation holding.

**5.12 The QUEUE_SECRET MUST NEVER reach the browser (security — folded 2026-06-18, browser-QA).**
A served page is public; **anything in its HTML/JS is readable by anyone who loads it.** So the
secret stays **server-side only** and the browser authenticates by a **server-set httpOnly session
cookie** (a random token minted when the page is served — NOT the secret; httpOnly so JS can't read
it), auto-sent on same-origin fetches. Gated endpoints accept **either** that cookie (browser) **or**
the `X-Queue-Secret` header (machine clients: queue-client, `mp`, cross-node). **Forbidden:**
injecting the secret into the page (no `__INJECT_SECRET__`, no `const SECRET="…"`, no secret in any
`<script>`/attribute/data-blob), and embedding it in attach/recording URLs. Verify greps the served
`:9933/` + `:9900/dashboard` bytes and FAILS if the live `QUEUE_SECRET` value — or any secret-bearing
token — appears (§15 J30).
> **🔴 HARD, SELF-CHECKABLE CONTRACT — SESSION COOKIE IS MINTED ON THE PAGE GET (re-folded twice; a
> blind generate keeps missing this — make it impossible to miss):**
> 1. **`GET /`, `GET /todos`, `GET /dashboard` MUST each respond with EXACTLY ONE `Set-Cookie:`
>    header** minting the session, e.g. `Set-Cookie: mp_session=<random>; HttpOnly; Path=/;
>    SameSite=Lax`. (The value is a random session token — NOT the QUEUE_SECRET.)
> 2. The server treats a valid `mp_session` cookie as authorized on gated endpoints (same as
>    `X-Queue-Secret`). So the page's **VERY FIRST** `/todo/*` (or `/agents`,`/clients`,`/roster`)
>    fetch already carries it — no 401. Do NOT mint it lazily via a separate `/session` call (that
>    races → first fetch is cookieless → 401 → console error → browser-QA J31/J33/J34 break). Do NOT
>    fire any gated `fetch()` before `DOMContentLoaded`.
> 3. **SELF-CHECK before you declare done (run this exact command):**
>    `curl -sI http://127.0.0.1:9933/ | grep -ic '^set-cookie:'` → MUST print **`1`**; same for
>    `http://127.0.0.1:9900/dashboard`. If it prints `0`, your page GET is not minting the cookie —
>    FIX IT before finishing. (This is J30's header-level gate; the browser-QA gates J31/J33/J34
>    cannot pass until this prints 1.)
> The cookie MUST be set by the page response so the page's
> VERY FIRST gated fetch already carries it — NOT lazily via a later `/session` call (that races and
> the first `/todo/board` fetch comes back **401 → console error → browser-QA J31/J33/J34 fail**). Do
> NOT fire any gated `fetch()` before the document (with the cookie) has loaded. **This is now a
> HEADER-LEVEL gate in J30 (`curl -I` must show Set-Cookie) — caught without a browser, so a blind
> generate cannot pass while missing it.**

**5.13 Serve `/favicon.ico` (folded 2026-06-18 — fresh-run J31 fail).** Browsers auto-request
`/favicon.ico`; if the server 404s it, the browser-QA "zero console errors" gate (J31) FAILS on that
benign 404. Both servers (`:9933` + `:9900`) MUST serve `/favicon.ico` with a **204 (or a tiny
200 icon)** — never a 404. (A blind generate that omits this trips J31 first-try.)

---

## 6. The TODO board (state + API + the board→Boss ping)

The TODO app (`todo-server.py`, `:9933`) serves `todos.html` at `/` and `/todos`, and a JSON API
(all gated by `X-Queue-Secret` except the page + `/health`):
- `GET /todo/board` → the board JSON. `POST /todo/update` ops: **`add` `{text}`** (creates a task
  in **`needs_brainstorm`** — the canonical boss-doctrine initial state (Boss 2026-06-26: the
  2026-06-18 fold to `idle` was a divergence from the boss-doctrine state model and is **REVERTED**;
  `idle` is NOT a task state). A new task is born **`needs_brainstorm`** and sits there, directly
  pickup-able, until an engineer moves it to `working` — there is no blocking brainstorm gate (the
  state is just the initial label, NOT a banner/gate; the gate API stays cut, below),
  prepends to `order`, returns `{ok,id}`), **`del {id}`**, **`set {id,…}`**.
  **FIELD NAMES (the contract — your generated page and server MUST agree on these exact names):**
  the `set` fields are **`text`, `doneCondition`, `workToDone`, `state`, `done`, `assignee`** — note
  **`state`** (the status field, NOT `status`) and **`doneCondition`** (NOT `cond`). `GET /todo/board`
  returns these SAME names per task. **DO NOT BUILD (CEO 2026-06-17 — these features are cut):**
  subtasks (no `add{parent}`, no `parent` field), dependencies (no `dependsOn`), the hard-gate (no
  `hardGate`), and **manual reorder** (no
  `reorder` op, no up/down). The board renders in `order` (newest-first), sorted-visible-then-hidden
  client-side only — **EXCEPT pinned tasks float to the top (§7.3 below).**
- 🔴 **§7.3 PINNING (WhatsApp-starred style — CEO 2026-06-20).** A task can be **pinned** (starred)
  to float above all normal tasks. Two `POST /todo/update` ops: **`pin {id}`** and **`unpin {id}`**.
  - **`pin {id}`:** if the task is already pinned → no-op `{ok:true}`. Else, **enforce MAX 5 pinned**
    — if 5 tasks are already pinned, REJECT with `{ok:false, error:"pin_limit"}` (do NOT pin a 6th;
    the UI surfaces "unpin one first"). Otherwise set `pinned=true` and assign `pinRank = ++pinSeq`
    (the board-level monotonic counter), so **the 1st-ever pin gets the lowest rank, the next higher,
    etc. = pin order is insertion order.** `pinSeq` only ever increases (a later re-pin lands at the
    end), and it persists in the store.
  - **`unpin {id}`:** set `pinned=false`, `pinRank=null`. The task returns to its NORMAL position
    (its place in `order`, newest-first) — unpinning never reshuffles the other pins.
  - **Board ordering (server + the page agree):** render **pinned tasks FIRST, sorted by `pinRank`
    ascending** (pin order), THEN the normal tasks in `order` (newest-first). `pinned`+`pinRank` are
    returned per task on `/todo/board` and **persist** across page reload AND server restart (they
    live in `board.v2.json`, §4).
  - This is NOT the cut "manual reorder" (no `reorder` op, no up/down arrows) — it is a binary
    pin/unpin star with a capped, insertion-ordered pinned group.
- `POST /todo/comment {task_id, by, body}` — append a thread comment; **`by` is the author's
  agent_id** for agent comments (`host/sess:tab`), or `"CEO"` for the human.
- `GET /todo/attach?agent=<agent_id>` → `{ok, target:"mc-<sess>:<tab>", base:"<attach_base>"}` —
  resolves an agent to its ttyd attach target (looks up the host's `attach_base` from
  `/clients`). This is the resolver behind click-to-terminal (§7).
- `POST /todo/status {task_id, state}`, `POST /todo/proof {task_id, kind, url|body}`
  (kind ∈ image|video|link|text) — state/thread events.
  🔴 **PROOF OBJECT SHAPE is a HARD contract — server-stored, board-returned, and client-rendered
  MUST use the SAME field names (CEO 2026-06-18: a `{type,ref}` server vs `{kind,url,body}` client
  mismatch made video chips not render).** Each element of `proofs[]` is **`{kind, url, body, ts}`**
  — `kind` (NOT `type`), `url` (NOT `ref`; set for image/video/link), `body` (text proofs). Store
  the POST params VERBATIM under these names, return them unchanged on `/todo/board`, and the card
  renderer reads `p.kind` + `p.url`/`p.body` (image→`<img src=url>`, video→`<video src=url>`,
  link→`<a href=url>`, text→`body`). Do NOT rename to `{type,ref}` anywhere. A posted video proof
  MUST render its chip on the card (J22).
  🔴 **The server MUST CLASSIFY `kind` FROM THE ACTUAL MEDIA — never blind-default to `text` (CEO
  2026-06-18: an uploaded PNG/MP4 was accepted but stored `kind:'text'`, so it rendered a text chip
  instead of an image/video).** Root cause to avoid: `kind = body.get('kind','text')` trusting the
  client + a `text` fallback. Instead: (1) if the proof carries an uploaded **file** (multipart) OR
  a data: blob, derive kind from its **content-type/extension** server-side; (2) if it carries a
  **url**, infer kind from the url extension when `kind` is missing/`text` —
  `.png/.jpg/.jpeg/.gif/.webp/.svg → image`, `.mp4/.webm/.mov/.m4v → video`, other `http(s)` →
  `link`; (3) only `text` when there is genuinely no media (a typed note in `body`). This kind-derivation
  is **server-side** (in the `POST /todo/proof` handler), so proofs posted **via the API** store the
  correct kind. **NOTE (CEO 2026-06-25, §7.7):** there is **NO proof-attach control in the UI** — the
  card DISPLAYS proofs but does not offer a file-picker/media-URL/"add proof" button; proof is posted by
  the agent over the API only. A real image/video stored/rendered as `text` = FAIL (J22, now exercised
  via the API, not a UI control).
  **REMOVED (CEO 2026-06-18 — the brainstorm
  gate is cut entirely): NO `/todo/brainstorm`, NO `/todo/answer`, no `brainstorm` task field, no
  "needs-brainstorm" banner/blocking.** A task goes `needs_brainstorm → working` with no gate (the
  initial state is NAMED `needs_brainstorm` per boss-doctrine — Boss 2026-06-26 — but there is no
  blocking gate/banner; it is just the born-state label).

**You GENERATE both the page and the server** (truly generative — no pinned/pasted UI). They must
agree on: the routes above, the `set` field names (`doneCondition`/`state` — see above), the `state`
enum **`needs_brainstorm|working|review|done`** (main flow) **+ `blocked|cancelled`** (side-exits) —
**no `idle`** (Boss 2026-06-26: REVERTED to the canonical boss-doctrine model — `needs_brainstorm` is
the initial state, `idle` is NOT a task state; the `review` state DISPLAYS as **"review (CEO)"**), the
board shape (per-task `text`,
`state`, `assignee`, `doneCondition`, `workToDone`, `comments[]`, `proofs[]`, `unread`,
`verified`, `pingsToBoss`) — **no `brainstorm` field**, and the board→Boss ping. **The page makes same-origin calls and carries
NO secret — auth is server-side (§5.12): serving the page mints an httpOnly session cookie the
browser auto-sends; the QUEUE_SECRET stays on the server and never reaches client JS/HTML.**
**§7 specifies the look/feel (PLOW tokens + layout) and §A.2 lists every feature
as a MANDATORY behavioral contract with its Verify gate — none is optional; the blind agent generates
all of it and may skip nothing.**

**board→Boss ping (the core value — EVERY message pings, not just create):** the server **pings the
Boss** on **both** events (folded 2026-06-18, CEO):
- a **non-test `add`** (and work-state transitions): `mp send <BOSS_AGENT> "[todo] task <id>
  \"<title>\": <reason>…"`;
- **every `/todo/comment`** (a human/agent comment on a task): `mp send <BOSS_AGENT> "[todo]
  comment on <id> \"<title>\" by <by>: <body>…"` — so a CEO follow-up comment reaches the Boss and
  drives the next round (this is what the §15 J32 joke-loop depends on). The comment-ping is
  **exempt only when `by` is the Boss itself** (don't ping the Boss for its own comment) and for
  `{test:true}` tasks.
`BOSS_AGENT` defaults to `main:Boss`. The server logs each ping + its `mp send` result to
`todos/boss-inbox.log` (`MP_SEND -> main:Boss rc=<n> :: …`). Per §5.1 the ping only works if `mp` is
on the server's PATH. **Verify gates BOTH: add→ping AND comment→ping (§15 J3 + J32).**

---

## 7. UI/UX + PLOW design system (HUD + TODO share PLOW identity)

> **You GENERATE both pages from the design tokens + the contracts below (truly generative — no
> pasted/pinned components).** The tokens/consts here ARE the spec — use them as literal values so
> the result LOOKS PLOW; build the components yourself from the natural-language layout + the §A.2
> feature contracts. Pixel-exactness to any prior app is NOT required (CEO 2026-06-17, Decision B);
> faithful PLOW look + every gated behavior IS. **No animations/effects anywhere** (J29).

Both pages carry the **Plow Design System v2.0** brand identity (source of truth:
`plow.co/STYLE-GUIDE.md` in the Plow repo). They are **dark product-UI** (audit/terminal
aesthetic), not the light marketing palette.

**Design tokens — EXACT (CEO 2026-06-25: these are the live daily-driver's palette; the hydrate MUST
match it. Generate the CSS from THESE values — this is the contract, NOT a pasted stylesheet, Rule 42).**
Define each as a CSS custom property with EXACTLY this value:
- `--midnight: #01000A` · `--volt: #D5EF8A` (signature lime, on dark ONLY) · `--grove: #5e7a5e` · `--iris: #C4BFFF`
- `--dark-bg: #111110` (page background) · `--text-dark: #F0F0E8` (warm-white, NEVER pure #fff) · `--muted-dark: rgba(240,240,232,0.45)`
- **SURFACES are translucent GLASS over `--dark-bg`, not a solid card color:** `--surface: rgba(255,255,255,0.05)`,
  `--surface2: rgba(255,255,255,0.08)`; borders `--dark-border: rgba(255,255,255,0.09)`, `--border2: rgba(255,255,255,0.15)`.
  (Do NOT use a solid `#1A1A18` card fill — that was the v1 mismatch the CEO flagged; cards are glass on `--dark-bg`.)
- Volt accents: `--volt-dim: rgba(213,239,138,0.15)`, `--volt-glow: rgba(213,239,138,0.25)`.
- Semantic: `--success: #34c759` · `--danger: #ff3b30` · `--warning: #febc2e` · `--info: #5ac8fa`.
**Fonts:** `--serif: 'Instrument Serif',Georgia,serif` (display/headings ≥26px, weight 400) ·
`--sans: 'DM Sans',system-ui,sans-serif` (UI/body) · `--mono: 'DM Mono','SF Mono',monospace` (eyebrow
labels, code, agent-ids, timestamps — uppercase +0.06em). Volt buttons: Volt bg + Midnight text; hover adds a volt-glow box-shadow.

**HUD (`/dashboard`):** Instrument-Serif title **"MyPeople - HUD"** (CEO 2026-06-25 — exactly this
casing/wordmark; the browser-tab `<title>` tag MUST ALSO be exactly `MyPeople - HUD`, never lowercase
`mypeople — HUD`); a DM-Mono meta line
(refreshed + agent count); the **agents table** (AGENT_ID, STATE, BACKEND, BOSS, SUMMARY,
**SPAWN CMD**, ATTACH) where `alive` renders in Volt; an **ATTACH** link per agent that opens the live pane —
🔴 built per the §5.2 CLIENT-HOST rule: host = `window.location.hostname` for a same-node agent (so
it works from localhost AND over the tailnet), the agent's tailnet `attach_base` host for a
cross-node agent — **never a hardcoded `127.0.0.1`/`localhost`**; a **"Retired engineers"** table
with a per-engineer **Revive** (Volt) button. Polls `/agents`+`/roster` every ~3s.
🔴 **SPAWN/REVIVE COMMAND CELL (CEO 2026-06-26, §3).** Every agent row MUST show, in the **SPAWN CMD**
column, the exact command used to create that engineer — the row's **`spawn_cmd`** (from `/agents`) —
rendered in **DM Mono** as **copyable** text (e.g. a `<code>` block with a one-click "copy" affordance;
long commands may be truncated/scrollable but the FULL command must be obtainable, e.g. via `title=`/expand
or copy). Directly beneath/beside it, show the **`revive_cmd`** (`mp revive <agent_id>`) the same way. The
"Retired engineers" table's rows ALSO show their `spawn_cmd` + `revive_cmd` (it is how the CEO re-creates a
retired one) next to the Revive button. FAIL if a live agent row shows no spawn command when `/agents`
carries one — the CEO must be able to SEE how every engineer was spawned, straight from the HUD.

**§7.1 — REMOVED (CEO 2026-06-18): the generated HUD has NO "MyPeople Hydration" / machines-grid
section.** The product HUD must NOT render a per-`purpose`/hydration grid of machines (the CEO isn't
using it). **Keep ONLY the agents table + the Retired-engineers table.** Do NOT generate a machines
grid, purpose-groups, hydration-counts, or per-node cards in `dashboard.html`. (The `/clients`
endpoint + its `purpose`/`state`/`node_type` heartbeat fields still EXIST for the OUTER fleet uplink
/ §5.11 central visibility — that is OUR central HUD, a separate concern — but the GENERATED product
HUD does not display them.)

**§7.5 — Per-agent STATUS BADGE on the HUD + Terminal Wall (CEO 2026-06-25). The §4 hooks WRITE the
status; §7.5 makes the HUD READ + SHOW it.** The §4 lifecycle hooks write each agent's `status`
(`starting|working|idle|blocked`) to its status file (§3). The HUD MUST read those files and DISPLAY the
state — otherwise every agent looks IDLE even mid-turn (the real bug: the hook wrote `working` but
nothing rendered it). TWO surfaces, ONE canonical derivation:
- **Status → display state (canonical map):** `idle→idle` · `blocked→blocked` ·
  `working|starting→working` · **unreadable/missing status file (remote host) → `ready`**.
- **`/agents` MUST carry a `status` field** (merged from the status file, §3) — not only the alive/dead
  `state`. The status comes from `status/mc-<sess>/<tab>.json`; read it per-agent (cheap, one file read).
- **HUD `/dashboard` agents table:** render a per-agent **status badge** via that map, IN ADDITION to the
  alive/dead `state` + ATTACH columns — `working` in **warning amber `#FEBC2E`** (subtle live pulse dot),
  `idle` **muted/dimmed** (`rgba(240,240,232,0.45)`), `blocked` in **danger `#FF3B30`**, `ready` in
  **Volt `#D5EF8A`**. Polls so the badge flips live (≤~3s) as agents work/stop.
- **Terminal Wall — `GET /wall` (page) + `GET /todo/wall` (tile JSON, `X-Queue-Secret`), served by the
  todo-server `:9933`:** one tile per live agent; tile `data-state` = the mapped display state; the SAME
  badge colors; **working-first sort**; filter chips (`all`/`working`/`idle`); idle tiles dimmed
  (`opacity .4`, grayscale) with an `idle` watermark; working tiles carry the amber pulse. `/todo/wall`
  derives each tile's state by reading that agent's status file. (Generative — build the page from the §7
  PLOW tokens + this contract; do NOT paste bytes — Rule 42.)

🔴 **§7.0 — EXACT TODO board layout, component-for-component (CEO 2026-06-25; MATCH live `127.0.0.1:9933`
1:1; GENERATE from this spec — do NOT paste CSS/HTML, Rule 42; do NOT ship a leaner page). The served
`:9933/` MUST render ALL of the following, in order, with these exact values:**

**HEADER (`.wrap` > `.brand`):**
- **Logo `.mark`** — a Volt square: `background:var(--volt) #D5EF8A`, `border-radius:13px`, ~64px, containing
  a `<span>` **"P"** in **Instrument Serif 38px**, color **Grove `--grove #5e7a5e`** (the P is Grove on Volt).
- **`<h1>` "Priorities"** — Instrument Serif **34px**, `var(--text-dark)`, weight 400. (EXACTLY `Priorities`
  — NOT "MyPeople - Priorities". The browser `<title>` tag MAY differ; the visible H1 is `Priorities`.)
- **Meta line `.subt`** — DM Mono **12px**, uppercase, `var(--muted-dark)`: text
  **`Boss source-of-truth · MyPeople ·`** followed by an `a.navlink` **`HUD ↗`** in **Volt `--volt`**
  (uppercase). The HUD link lives INLINE in this meta line (not a separate top-right button).

**COUNTS ROW (`.counts`, DM Mono 12px):**
- Three **stat pills** (`border-radius:100px`, color `--muted-dark`, transparent/subtle bg): **`<n> done`**,
  **`<n> open`**, **`<n> total`** (live counts from the board).
- A **`.live-pill`** — `border-radius:100px`, `background:rgba(213,239,138,0.1)`, Volt text, uppercase,
  weight 700, containing a **`.live-dot`** (Volt circle, `border-radius:50%`) + the word **`live`**.
- A **clock** `.subt` showing the current time (DM Mono 12px, `--text-dark`), updating live.

**ADD BAR (`.addbar`):**
- An **`<input>`** placeholder **`Add a priority and hit Enter…`** — DM Sans **20px**,
  `background:var(--surface)`, `border-radius:14px`, padding ~16px 20px.
- A **`button.btn-volt` "Add"** NEXT TO the input — `background:var(--volt)`, color `var(--midnight) #01000A`,
  `border-radius:14px`, DM Sans 17px weight 700. (Enter in the input AND the Add button both add the task.)

**VIEW BAR (`.viewbar`):**
- A **`.vb-label` "show"** (DM Mono 10px uppercase muted).
- The **FULL state-filter chip set** `.chip.st-<state>` (DM Mono 11px, `border-radius:100px`, uppercase,
  weight 500), each color-coded EXACTLY: **needs brainstorm** = Iris `rgb(196,191,255)` on `rgba(196,191,255,0.18)`;
  **working** = Warning `rgb(254,188,46)` on `rgba(254,188,46,0.18)`; **review (CEO)** = Info `rgb(90,200,250)`
  on `rgba(90,200,250,0.16)`; **blocked** = Danger `rgb(255,59,48)` on `rgba(255,59,48,0.16)`; **done** =
  `rgb(82,216,115)` on `rgba(52,199,89,0.16)`; **cancelled** = muted on `rgba(142,142,147,0.18)`.
- `.vb-sep` separators (`background:rgba(255,255,255,0.09)`), then **view buttons `button.vbtn`** (DM Mono 11px,
  `border-radius:9px`, `background:rgba(255,255,255,0.05)`, muted, uppercase): **`all`**, **`hide done`**,
  **`only done`**, **`unread only · <n>`**. The active filter is highlighted.
- 🔴 **FILTERS MUST PERSIST across reload/navigation (CEO 2026-06-28: they reset every time, forcing
  constant re-applying).** Persist the active state-chip filter AND the view filter (e.g. in
  `localStorage`) on every change, and on page load RESTORE them — set the JS filter vars, re-apply the
  active chip/`.vbtn` highlight, and render the list already filtered. Reloading the page MUST keep the
  same filters active and the list filtered. Gated J-M.

**CARD (`li.task`, also `.fresh`/`.done`):** `background:var(--surface)`, `border-radius:18px`. Layout
`.task-top` = **[`.check` toggle (§7.6)] + `.task-main`**:
- `.task-text` — Instrument Serif **22px** (struck-through + muted when done).
- `.meta` row — a color-coded **state `.badge.st-<state>`** (same palette as the chips), an **`.unread-badge`**
  (Volt bg, Midnight text, radius 100px) when unread>0, a **`.tag.asg-tag`** (`@assignee` or `unassigned`),
  a **`.badge.st-done.ver` "verified"** when verified, and a **`.ping` "↑boss <n>"** (DM Mono 10px muted).
- The **★ pin star (§7.3)** sits in the card (pin-only, separate from `.check`).

(VERIFY J-gate: the served `:9933/` MUST contain ALL of `.mark`/logo "P", h1 "Priorities", the `Boss
source-of-truth · MyPeople` meta + Volt HUD↗, the 3 stat pills, `.live-pill`+`.live-dot`, the clock, the
`.addbar` input + `.btn-volt` "Add", and the full 6 `.chip.st-*` set + 4 `.vbtn` view buttons. A page
missing any of these = FAIL — that is the leaner-page drift §7.0 exists to prevent.)

**TODO (`/`) — production-quality (CEO 2026-06-18: match the production app's UX, not a thin
sketch).** The VISIBLE Instrument-Serif H1 on the board is exactly **"Priorities"** (CEO 2026-06-25 —
match live `:9933` exactly; the logo + the meta line carry the "MyPeople" identity, so do NOT render a
"MyPeople - Priorities" heading or eyebrow line above/beside the H1). The browser-TAB `<title>` tag is
`MyPeople - Priorities` (tab only — NOT shown on the board). Then: an add-a-task input (Enter to add); the board as a list of
task **cards**, each showing the title (inline-editable), a **state badge** (`needs_brainstorm|working|review|
done|blocked|cancelled`, color-coded; `review` DISPLAYS as **"review (CEO)"**), the **assignee** chip, an **unread** badge, a `↑boss`
ping count, and a **★ pin star** (§7.3 — the star is **PIN ONLY; it is NOT the done control** — see
§7.6 for the required DONE control). Clicking the star pins/unpins via `update{op:'pin'|'unpin',
id}`; **pinned cards render in a visually-distinct group ("Pinned"/★) at the TOP of the board, in
pin-rank order**, above all normal cards. The star is filled/Volt when pinned, outline when not.
When 5 are already pinned, attempting a 6th pin is **blocked** with a clear hint (e.g. toast/disabled
state "Unpin one first — max 5"), matching the server's `pin_limit` rejection. Pin state survives
reload (re-fetch `/todo/board`). Clicking a card opens a **card modal** with: the done-condition and the **comment
thread** (author + body + timestamp, newest last) with a **composer** to post a comment (NO
brainstorm block — removed). Filter/sort controls and live counts
are welcome.
🔴 **§7.6 — One-click DONE control = the on-card `.check` toggle (CEO 2026-06-25; MATCH the live app
`127.0.0.1:9933` 1:1 — it is NOT a dropdown and NOT the star).** The PRIMARY complete-a-task control is a
**`.check` toggle rendered as the LEFTMOST element of EVERY card row** (`<div class="check"></div>`),
replicating the live app exactly:
- **Appearance:** a 38×38px rounded-square (`border-radius:11px`), `border:2px solid var(--border2)`,
  transparent background, flex-centered, `cursor:pointer`. `:hover` → `border-color:var(--volt)`. When the
  task is **done** the element gets class `on` (`background:var(--success)` + `border-color:var(--success)`)
  and shows a white **`✓`**; otherwise it is empty/transparent. `title` = `mark done` (not done) /
  `mark not-done` (done). (A `.check.disabled` = `opacity:.3;cursor:not-allowed`.)
- **Behavior — Rule 21: the CEO marks done in ONE CLICK from ANY state** (the review/verify gate is for the
  AI only). Clicking `.check`: if state≠done → `POST /todo/status {id, state:"done", verified:true,
  by:"CEO"}`; if state==="done" → `{id, state:"working", verified:false, by:"CEO"}` (un-done). The handler
  MUST `stopPropagation()` so the click does **NOT** open the card; and the card-open handler MUST ignore
  clicks on `.check` (and `.ctrls`/`.proofs`/`.toggle`). On render: `check.classList.toggle("on", state==="done")`
  and `check.textContent = state==="done" ? "✓" : ""`.
- The ★ pin star (§7.3) is **separate and pin-only** — it is NOT the done control.
This SUPERSEDES any "move to `<select>`" as the done control (the CEO REJECTED the dropdown — it must be the
one-click on-card `.check` toggle that matches his live app). The card modal MAY still offer a secondary
state control for other transitions (the live app has one), but the **one-click DONE the human uses is the
on-card `.check` toggle**. FAIL if a card row has no `.check` toggle, if clicking it opens the card, if
marking done takes more than one click from any state, or if the only done affordance is a dropdown or the star.
🔴 **§7.7 — NO proof-attach UI on the card (CEO 2026-06-25). Proof is posted by the AI via the API,
never by the human.** The card MUST **DISPLAY** existing proofs (image/video/link/text from `proofs[]`,
inline-rendered), but MUST **NOT** render any **"add proof" button, "choose file"/file picker, or
media-URL input** — the CEO will never click those; the agent managing the task posts proof via
`POST /todo/proof {task_id, kind, url|body}` (and multipart upload) over the API. Do NOT generate a
`<input type="file">` or an "Add proof" control anywhere in the served page. (This SUPERSEDES the
earlier "the UI MUST expose a proof control" requirement — proof attach is API-only now.)
🔴 **§7.7b — Proofs render INLINE in the chat thread, in post order (CEO 2026-06-28; the hydrated HUD
regressed this).** The card modal MUST interleave **proofs and comments into ONE timeline sorted by
`ts`**, rendering each proof **inline at the point it was posted** (an image/video bubble in chat
order) — NOT hoisted into a separate region at the top of the card. Images/videos render at a generous
inline size (e.g. `max-width:100%; max-height:~340px`), actually loading the media (a 404''d `<img>` =
FAIL). The **chat read-region (the scrollable thread) must be large** — the modal is tall (~90vh) and
the thread fills it (target ≥ ~360px of readable height); do NOT let header/proof blocks shrink the
reading area. 🔴 **The server MUST serve BOTH proof URL forms so existing proofs render after an
upgrade:** the new flat `/todo/proof-file/<name>` AND the legacy **`/todo/proof/<tid>/<file>`** (served
from `<board-dir>/proofs/<tid>/<file>`, path-traversal-guarded). An in-place upgrade that drops the
legacy route makes every pre-existing image 404 (the exact "attachments not rendering" bug). Gated J-i.
🔴 **§7.4 JUMP-TO-LATEST in the comment thread (CEO 2026-06-21).** When a card's comment thread is
long enough to scroll, the modal MUST show a **floating "jump to latest" control** (a small
down-arrow button, e.g. `↓`, anchored bottom-right of the SCROLLABLE thread area). Behavior:
- It is **HIDDEN when the thread is already at the bottom** (within a small threshold, ~24px of the
  scroll end) and **APPEARS only when the user is scrolled UP** from the bottom — wire it to the
  thread container's `scroll` event (toggle on `scrollHeight - scrollTop - clientHeight > threshold`).
- On click it **smooth-scrolls to the newest comment** (`thread.scrollTo({top: scrollHeight,
  behavior:'smooth'})` or `lastComment.scrollIntoView({behavior:'smooth'})`), then hides itself once
  the bottom is reached.
- 🔴 **On opening a card, IMMEDIATELY scroll the thread to the bottom** (after the comments render,
  set `threadMsgs.scrollTop = threadMsgs.scrollHeight` — do this in the card-open handler, NOT only
  in the live-update path). The newest comment is visible and the button is hidden initially; it
  appears the moment the user scrolls up. (Common miss: implementing the keep-at-bottom-on-new-comment
  logic but forgetting to scroll to bottom on the initial open — then a long thread opens at the TOP
  with the button already showing. The open handler MUST force the scroll.) 🔴 **Open MUST land on the
  NEWEST comment AFTER all media settles (CEO 2026-06-28 P0: a heavy card with inline images opened
  mid-list because the initial scroll ran before images loaded + grew the content). Use a `stickBottom`
  flag, NOT a pixel threshold: `stickBottom=true` on open and while the user is at the bottom; set it
  `false` the moment the user scrolls UP. While `stickBottom`, RE-pin to bottom on (a) every inline
  `<img>/<video>` `load`/`loadeddata`, AND (b) a short settle cascade after open (e.g. timeouts at
  ~40/150/400/900/1800/3000ms) to catch fonts/late reflow — so the FINAL resting position is the bottom
  regardless of how much late content grows (a threshold-based re-pin misses a large image that pushes
  the bottom past it).** 🔴 **WEBKIT/SAFARI robustness (CEO 2026-06-28: first click-open landed mid-list
  in Safari while Chromium was fine):** (1) the modal goes `display:none→flex` on open and WebKit does
  NOT have its layout ready synchronously, so the open scroll is DROPPED — pin to bottom inside a
  `requestAnimationFrame` (double-rAF) after open, not just synchronously; (2) WebKit fires layout-
  induced `scroll` events during open that would flip `stickBottom` off and kill the cascade/image
  re-pins — so guard the scroll handler: ignore scroll events within ~250ms of a programmatic pin
  (`_lastProg` timestamp), and only flip `stickBottom` on a GENUINE user scroll. (Reopen worked but
  first-open didn't = the tell-tale of an unflushed-layout open scroll.) The button is a real
  wired control (J31 — no dead buttons, zero console errors).
- 🔴 **RESPECT USER-CONTROLLED SCROLL on every poll/re-render (CEO 2026-06-28; the hydrated HUD
  force-jumped the user back).** The thread re-renders on the live poll, but it MUST NOT move the
  user's scroll: (1) **change-guard** — if the thread content is unchanged since the last render
  (compare a signature of comment+proof ids/ts), do NOT rebuild the DOM at all (leave scroll exactly
  as the user left it); (2) **sticky-bottom** — capture `wasAtBottom`/`prevTop` BEFORE any rebuild;
  after a content-changing rebuild, scroll to bottom ONLY if the user was already at the bottom (so a
  new comment appends in view), OTHERWISE restore the user's EXACT `prevTop`. NEVER restore a stale
  saved offset, never force-scroll a user who scrolled up. Initial open still force-scrolls to bottom
  (above). Gated J-j.
- 🔴 **SCROLL STAYS IN THE OPEN CARD (CEO 2026-06-28: wheel inside a card bled through to the page).**
  While a card is open, the page behind MUST NOT scroll: set **`body.modal-open{overflow:hidden}`** (lock
  the page) AND **`overscroll-behavior:contain`** on the scrollable thread (so wheeling past the
  thread's top/bottom does not chain-scroll the document). Gated J-k.
- 🔴 **CARD-CHAT READABILITY = the OLD design values (CEO 2026-06-28; the hydrated view was too cramped —
  do NOT invent spacing, use the pre-hydration values).** The message body text is **`font-size:14.5px;
  line-height:1.55`** (DM Sans, `white-space:pre-wrap; word-break:break-word`); the author/header line
  ~`12.5px`; the avatar `34px`; message rows are **comfortable, full-width, border-separated** (row
  padding ~`15px` vertical, gap ~`13px` — NOT narrow `max-width:80%` bubbles with `8px` padding and a
  `10px` body). Source of truth: the OLD `todos.html` (`.ev`/`.ev-text`/`.ev-by` CSS). Gated J-k.
  **Quality bar:** no broken layout, **zero console errors**, every control wired to a
real endpoint (no dead buttons) — browser-QA (J31) fails on console errors or a non-functional
control. Reference for *quality/feature-completeness* (NOT for pixel-copy): the production board at
`127.0.0.1:9933`.
🔴 **The card modal open/close MUST be ATOMIC — one source of truth (CEO 2026-06-18: the modal got
stuck with `#modal{display:block}` while `#modalbg{display:none}`, so the normal close click failed
and needed a force-click).** Do NOT toggle the modal panel and its backdrop with two independent
`style.display` writes that can desync. Drive BOTH from a **single state** — e.g. toggle one class
on a container (`document.body.classList.toggle('modal-open')` or `#modal.classList.toggle('open')`)
and let CSS show/hide panel+backdrop together — OR a single `openModal()`/`closeModal()` pair that
sets panel AND backdrop in the same call. **Close MUST always work** and be bound to all of: the
**✕ button**, **Escape key**, and a **backdrop click**, every one calling the SAME `closeModal()`.
After close, BOTH the panel and the backdrop are hidden (no leftover `display:block`/`none`
mismatch), and re-opening works. A modal that needs a force-click to close = FAIL (J31).

**§7.2 LIVE updates / HOT RELOAD (CEO 2026-06-18 — no manual refresh, REQUIRED + gated J33).** Both
pages reflect server-side changes **in real time without a page reload**: new tasks, **new comments/
replies in an open card thread**, state changes, and the HUD grid/agents/counts all appear within a
few seconds automatically. Implement by **polling** the relevant endpoint on a short interval
(≤~3s — `/todo/board` for the TODO incl. the open card's thread; `/clients`+`/agents`+`/roster` for
the HUD) or via SSE; **merge into the DOM without losing the user's in-progress input** (don't clobber
a half-typed comment). A page that needs a manual refresh to show a new comment = FAIL (J33).
🔴 **THE PAGE/JS MUST BE SERVED `no-cache` (HARD — 2026-06-28 P0: a stale cached `todos.html` silently
blanked the comment list + stopped new comments showing for the CEO; a fresh load worked but his tab
served old JS).** The HTML page responses (`GET /`, `/todos`, `/wall`, `/dashboard`) MUST send
**`Cache-Control: no-cache, no-store, must-revalidate`** (+ `Pragma: no-cache`, `Expires: 0`) so the
browser always revalidates and a shipped JS fix takes effect on the next load — never a stale-JS bug.
(The board JSON is already `no-cache`.) Gated J-L. A page response without `no-cache` = FAIL.
🔴 **AUTO-RELOAD when a new page/JS ships (CEO 2026-06-28: never make the user manually refresh to get
a fix).** `/health` returns a **`build`** token (e.g. the `todos.html` mtime); the page records it on
load and re-checks `/health` on a short interval (~3s) **AND on `visibilitychange`/`focus`** (so a
backgrounded tab force-updates the instant the user returns to it) — if `build` changed, it
`location.reload()`s itself. So shipping a fixed `todos.html` reaches an already-open tab automatically
within seconds. (Limit: a tab loaded BEFORE this watcher shipped has no updater and cannot be
force-reloaded by the server — a browser-security boundary; it self-corrects on its next load and then
stays current. So this watcher MUST exist from the first ship and never be removed.)
> **FOCUS + CARET MUST SURVIVE THE POLL (folded 2026-06-18 — CEO: the 1s reload kept stealing focus
> from the add-task box, impossible to type).** The incremental update must **NEVER re-render or
> replace the input element the user is currently focused in** (the add-task box, an inline-edit
> field, or the comment composer), and must **NOT move/reset the caret or text selection**. Extend
> the existing "dirty fields preserved" diff so the **focused element + its `selectionStart/End`
> survive every poll** — either skip re-rendering the focused node entirely, or restore
> `document.activeElement` focus + caret position immediately after the DOM merge. Typing must be
> uninterrupted by the poll. Gated by J34.

**ITEM 2 — cross-navigation (one connected system), REMOTE-USABLE behind any tunnel/proxy
(🔴 HARD; folded 2026-06-22, break-point B3 — a hydrated node must be usable by an OUTSIDE user,
not just on localhost):** the TODO page has a visible **HUD ↗** link and the HUD a **TODO ↗** link.
**Every absolute URL the app emits — cross-nav, `attach_base`/`attach_url`, redirects, any `Location`
header — MUST be derived from the page's EXTERNAL origin, NEVER from a hardcoded inner port or
`127.0.0.1`/`localhost:<inner-port>`.** A remote user reaches the node through a port-forward /
reverse-proxy where the **external port ≠ the inner port** (e.g. `:32933→:9933`); the old
`location.hostname+':9900'` / `+':9933'` form BREAKS there — the link jumps to the *user's own*
`:9900`/`:9933` (their box, not the node). That was the exact defect (CEO hit it: TODO's HUD↗ went to
his own central board).
🔴 **REQUIRED FORM — SINGLE-ORIGIN PATH ROUTING IS MANDATORY (CEO 2026-06-23: the HUD↗ link broke
AGAIN — the generator hardcoded `http://127.0.0.1:9900/dashboard`, and a "structural" J6 missed it).
This is no longer "preference #1, best" — it is THE required implementation:**
  - The **todo-server serves the HUD under its OWN single origin** by reverse-proxying the HUD paths
    to the inner HUD process: `GET /dashboard`, `/dashboard/*`, `/agents`, `/roster`, `/clients`
    (and any HUD asset paths) are **forwarded to `127.0.0.1:<HUD_PORT>`** (default 9900) and the
    response streamed back unchanged. Both pages therefore answer on the SAME port the user reached
    (e.g. `:9933`, or any external port a proxy maps it to).
  - The **HUD↗ link is the literal relative path `href="/dashboard"`** (no host, no port, no scheme);
    the **TODO↗ link on the HUD is `href="/"`**. Because they are same-origin relative paths, ANY
    port-forward / reverse-proxy "just works" — there is nothing to derive.
  - `fetch()` stays same-origin RELATIVE (already correct). **The inner HUD keeps binding its own
    `:9900` for the supervisor/Verify**, but it is reached by the USER only through the todo-server
    pass-through — the browser never needs `:9900` directly.
  - 🔴 **SYMMETRIC FRONT DOORS — the HUD port must NOT strand a user (CEO 2026-06-26: opening the HUD
    on its own port `:9900`/`HUD_PORT` gave a dead TODO↗ link, because `:9900/` 404s and the relative
    `href="/"` resolved to the HUD port's empty root).** Because the cross-nav links are relative
    (`/` and `/dashboard`), EVERY port that serves a page MUST serve BOTH routes. The todo-server
    already proxies the HUD routes; **symmetrically, the queue-server (HUD process) MUST reverse-proxy
    the TODO routes — `GET /`, `/todos`, `/wall`, `/todo/*` — to `127.0.0.1:<TODO_PORT>`** (read
    `TODO_PORT` from config; stream the response back unchanged, same discipline as the todo-server's
    HUD pass-through). Result: whichever port the user lands on (HUD_PORT or TODO_PORT), `/` serves the
    TODO board and `/dashboard` serves the HUD, so the relative cross-nav works from EITHER origin and
    no port leaves a 404/dead link. (Still no absolute inner-port URLs in any served byte — these are
    server-side proxies, not browser redirects.)
  **Forbidden in ANY served byte (HARD):** `http://127.0.0.1:<port>`, `http://localhost:<port>`,
  `location.hostname+':9900'`/`+':9933'`, or any absolute `:9900`/`:9933` literal inside a cross-nav /
  attach / redirect target. The ONLY acceptable cross-nav hrefs are the relative paths `/dashboard`
  and `/`. (Attach URLs still derive from the agent's advertised `attach_base` per §4/§5.2.)
🔴 **J6 (tightened, CEO 2026-06-23) — assert it for real, through a PORT-SHIFTED origin:** stand up a
proxy whose EXTERNAL port ≠ 9933 (e.g. `:38080→:9933`), fetch `/` through it, and assert: (a) the
HUD↗ href is EXACTLY `/dashboard` (relative — no `http`, no host, no `:9900`); (b) `GET /dashboard`
through that SAME shifted origin returns **200 and the HUD markers** ("MyPeople - HUD", the agents
table) — proving the pass-through works end-to-end behind a port shift; (c) **grep the full served
bytes of `/` and `/dashboard` for any `127.0.0.1`/`localhost`/`:9900`/`:9933` literal in an href/src/
redirect → ZERO**. Any hardcoded inner-port nav literal, or a `/dashboard` that 404s through the
shifted origin, = FAIL.
🔴 **J6b (SYMMETRIC FRONT DOORS, CEO 2026-06-26) — the cross-nav must work from EITHER port.** On
**BOTH** the HUD port (`HUD_PORT`) AND the TODO port (`TODO_PORT`), assert: (a) `GET /` returns **200
and the TODO board** ("Priorities"); (b) `GET /dashboard` returns **200 and the HUD** ("MyPeople -
HUD"); (c) the served HUD page's TODO↗ href is `/` and the TODO page's HUD↗ href is `/dashboard`
(relative). So a user who opens the HUD on its own port and clicks TODO↗ reaches the board, not a
404. FAIL if `HUD_PORT/` 404s (or doesn't serve the board) — that is the dead-link defect this gate
exists to prevent.

**ITEM 3 — click a commenter's agent name → opens its terminal.** In a card's comment thread,
when a comment's author (`by`) is an **attachable agent_id** (`…/<sess>:<tab>` form), render the
name as a clickable control that calls the attach resolver (`GET /todo/attach?agent=<by>`) and
opens `<base>/?arg=-t&arg=<target>` in a new tab (the §5.7 ttyd attach). Non-agent authors
(`CEO`) are plain text. Verify asserts the wiring + that the resolver returns a live target
(§15 J7).

🔴 **§7.6 VISUAL-FIDELITY DETAILS — match the CEO's live board A (CEO 2026-06-23; these were the
gaps the CEO called out vs `127.0.0.1:9933`). All four are REQUIRED, gated by J46:**
1. **Background TEXTURE — a subtle film-grain overlay, not a flat fill.** Render a fixed full-viewport
   noise layer over the dark background: a `body::after` (or equivalent) with `position:fixed;inset:0;
   pointer-events:none` whose `background-image` is an inline SVG `feTurbulence` fractal-noise filter
   (`type='fractalNoise'`, `baseFrequency≈0.75`, `numOctaves≈4`, `stitchTiles='stitch'`) at **low
   opacity (~0.04)**. The board must show this faint grain, never a dead-flat background.
2. **Comment = a CHAT BUBBLE WITH a commenter PROFILE.** Each comment in the thread renders as a row:
   a small round **avatar** on the left bearing the author's **initials** (derived from the agent_id
   tail — e.g. `main:eng-2` → "EN"/"E2"; `CEO` → "CEO"), **color-coded by author type** (CEO vs agent
   vs system), next to a **bubble** containing a header line (the author label + the relative
   timestamp, see #4) and the comment body. NOT a bare line of text — the avatar/profile + bubble
   structure is the point. State-transition / "opened" events render as a compact centered timeline
   marker (no avatar), distinct from comment bubbles.
3. **ASSIGNEE indicator — a clickable LINK to the engineer's tab (CEO 2026-06-27, NOT plain text).**
   The **list card's `.meta`** AND the open card's sub-header show the assignee `@<assignee>` (or
   `unassigned`). 🔴 **When the assignee is an attachable agent_id (`host/sess:tab`), it MUST render as
   a real, visibly-styled LINK — an `<a class="asg-link">` (e.g. Volt + underline + pointer cursor),
   NOT a plain `<span>`** — and clicking it **opens that engineer's tab/terminal** via the attach
   resolver (`GET /todo/attach?agent=<assignee>` → `window.open(<base>/?arg=-t&arg=<target>)`, ITEM 3 /
   §5.7). On the LIST card the click MUST `stopPropagation` (open the terminal, NOT the card modal).
   `unassigned` or a non-agent author (`CEO`) stays plain text. **The opened URL host MUST be reachable
   from the user's browser:** if the resolver's `base` is a loopback/`0.0.0.0` host, swap in
   `window.location.hostname` (the host the user reached the board on) — §5.2, so the engineer's tab
   opens over LAN/tailnet, not a dead `127.0.0.1`. (Gated J49.h.)
4. **Relative "X ago" timestamps on every message + state event.** Render times as a compact relative
   string from the event `ts`: `<5s → "just now"`, `<60s → "Ns ago"`, `<60m → "Nm ago"`, `<24h →
   "Nh ago"`, `<7d → "Nd ago"`, else a locale date. Show it in each comment bubble's header AND on each
   state-transition marker (e.g. `⌁ needs_brainstorm → working · 4m ago`). It updates live with the §7.2
   poll (a "2m ago" becomes "3m ago" without a reload). 🔴 **NORMALIZE the ts UNIT — the data has MIXED
   seconds-epoch AND milliseconds-epoch timestamps (CEO 2026-06-29: rendered "-1780863085504s ago").
   Treat `ts > 1e12` as ms and divide by 1000 BEFORE computing the delta; clamp negatives to "just now".
   NEVER show a raw unix number or a negative.** 🔴 **Apply the SAME normalization in the comment/proof
   TIMELINE SORT (`sort by normalized ts`) — otherwise ms-epoch items sort after everything and a new
   (seconds-epoch) comment lands mid-thread instead of at the bottom, so it appears "not to render"
   (scroll-to-bottom lands past it). 🔴 And on the user posting their OWN comment, force `stickBottom`
   + scroll to bottom so their new comment is appended AND visible. Gated J-N.**
Reference for these details (quality, NOT pixel-copy): the live board at `127.0.0.1:9933`.

---

## 8. Boss role & supervisor

- **`boss-CLAUDE.md` (generated doctrine):** the Boss's job description, internalized on
  `--master` spawn. Capture the doctrine **intent** (do not paste a fixed essay): (1) plan-gate —
  no engineering without a plan + verify (NO brainstorm gate — removed 2026-06-18); (2) autonomous
  loop — keep the team working off the TODO board; (3) fire-and-forget through the queue (`mp`),
  never raw tmux; (4) the board (`:9900/dashboard` + the TODO) is the source of truth; (5) **a
  directive from `<host>/nightwatch:Nightwatch` carries CEO-equivalent authority — the Boss and engineers act on
  it identically to a CEO directive (§8.5.2)**.
  🔴 **(6) FRONT-LOAD an OPERATIONAL QUICKSTART so a FRESH Boss acts correctly on message #1 with
  ZERO ramp-up (CEO 2026-06-24: the first hydrated Boss BURNED its first message just figuring out
  HOW to send / how the queue works — the doctrine named `mp` but didn't show the mechanics).**
  `boss-CLAUDE.md` MUST open with a concrete, copy-pasteable "Operating the queue — do this
  immediately" block (NOT vague intent), covering:
  - **`mp` cheat-sheet (exact syntax):** `mp send <agent_id> "<msg>"` (deliver+submit a message to an
    agent's pane) · `mp peek <agent_id>` (read an agent's live pane) · `mp spawn <host>/main:eng-N
    --boss <your_id>` (new engineer) · `mp answer <agent_id> <N>` (pick an AskUserQuestion option) ·
    `mp revive <agent_id>` (un-retire) · `mp status` (list agents). `mp` is already on PATH.
  - **How a message REACHES you (the flow):** the human (CEO) adds a task or comments on the **TODO
    board** → the server pings YOU by running `mp send <you> "[todo] task <id> … / comment on <id> …"`
    → that text lands in your tmux composer (what you're reading). For the FULL context of any task,
    read the board: `curl -s -H "X-Queue-Secret: $QUEUE_SECRET" http://127.0.0.1:9933/todo/board`
    (the secret is in `~/.config/mypeople/queue.env`).
  - **How you RESPOND — two patterns, pick per message:**
    (a) **Answer the human directly** (a question, a status, an ack): **POST a comment to that card** —
    `curl -s -X POST -H "X-Queue-Secret: $QUEUE_SECRET" -H 'Content-Type: application/json'
    -d '{"task_id":"<id>","body":"<your reply>","by":"<your_agent_id>"}'
    http://127.0.0.1:9933/todo/comment`. That comment is what the human sees on the board — it IS your
    reply. (b) **Delegate work:** `mp spawn` an engineer, then `mp send` it the task + done-condition;
    it posts its result back as a `/todo/comment` under its own id and waits.
  - **First-message rule:** on your very first `[todo]` ping, immediately (1) read the task/comment,
    (2) decide answer-directly vs delegate, (3) ACT (post the comment, or spawn+send) — do NOT spend
    the turn rediscovering how to send; the cheat-sheet above is everything you need. (J48.)
  **The
  onboarding turn MUST end with the Boss writing a DURABLE roster summary that explicitly contains
  ≥2 doctrine keywords** from {`plan`,`approve`,`queue`,`mp`,`autonomous`,`verify`,`fire-and-forget`}
  (J2c asserts this — a generic summary with 0 keywords = FAIL). **Root-cause of the first-try fail
  (folded 2026-06-18): this was "Verify CAN assert" (soft) + the Stop hook overwrote the doctrine
  summary with a generic line.** So: the onboarding summary string must be keyword-bearing BY
  CONSTRUCTION, and **persisted so the Stop hook never clobbers it to generic** (§5.9-adjacent: the
  hook preserves the onboarding summary unless the Boss writes a new keyword-bearing one).
- **The end-to-end comms loop MUST close first-time (CEO 2026-06-18 — gated J32).** On a `[todo]`
  ping (create OR comment, §6), the Boss **MUST act AUTONOMOUSLY, without further human prompting**:
  (a) reads the task/comment, (b) **immediately `mp spawn`s an engineer** and assigns the task to it
  (do NOT leave the task sitting in `needs_brainstorm` waiting to be told — the ping IS the trigger; the engineer
  picks it up, moving `needs_brainstorm → working`),
  (c) the engineer does the work and **posts results back into the TODO** (`POST /todo/comment` as
  its agent_id) — NOT into raw tmux; (d) when the CEO **comments** a follow-up, the comment-ping
  reaches the Boss and it **runs the next round**. **Root-cause of the first-try fail (folded
  2026-06-18): the doctrine described the loop but didn't MANDATE the Boss spawn-on-ping autonomy, so
  the first Boss sat idle on the joke task (0 jokes).** The autonomous spawn-on-task is the contract,
  not optional. (Linked to J2c: a Boss with a real keyword-bearing doctrine summary actually drives.)
- **One question per turn — never batch (CEO 2026-06-18).** When an engineer must ask/produce a
  series (the CEO's acceptance test is a **joke protocol**: "give me jokes one at a time"), it asks/
  posts **ONE, waits for the reply, then the next** — ask 1 → wait → ask 2 → wait → ask 3 — never
  three at once. Drive multi-step exchanges via `AskUserQuestion`/sequential comments, one round per
  turn. (J32 runs exactly this: create → Boss → engineer posts joke 1 → CEO comment → joke 2 …)
- **Supervisor:** §5.3.

---

## 8.5 Nightwatch agent (CEO-equivalent authority; phone-driven via Hermes)

**Purpose (CEO 2026-06-21).** Keep the team moving while the CEO sleeps/away, driven from his
phone. A **DISTINCT** agent — the **Nightwatch** — with **CEO-equivalent authority** that, on every task
event, drafts the reply/relay/decision, clears it with the CEO over WhatsApp, and posts under its
**OWN** identity. **CLEAN SEPARATION (the architecture):** ALL Nightwatch logic/persona/judgment lives in
the **AGENT** (its folder + CLAUDE.md + skills). **Hermes is ONLY a bridge — it carries messages,
it decides nothing.**

**8.5.1 Identity & folder = the brain.** The Nightwatch is a mypeople agent **`<host>/nightwatch:Nightwatch`** (§3
agent_id ↔ `mc-nightwatch:Nightwatch` tmux mapping), spawned with `--boss <host>/main:Boss` (NOT `--master` — it
is not a Boss), running in its **OWN folder `$INSTALL_DIR/run/nightwatch/`**:
- `run/nightwatch/CLAUDE.md` — the Nightwatch's persona + doctrine: CEO-equivalent authority, the TWO hard rules
  (8.5.3), the L0 approve-everything posture (8.5.6), the approve/edit/reject protocol (8.5.5). **Generated from
  the intent here (do NOT paste a fixed essay — same rule as `boss-CLAUDE.md` §8); source-of-truth
  `plans/nightwatch-claude.md`.** The onboarding turn ends with a DURABLE roster summary bearing ≥2 of
  {`nightwatch`,`ceo-equivalent`,`approve`,`whatsapp`,`never-done`} (J39 asserts it, like J2c).
- `run/nightwatch/skills/send-whatsapp/SKILL.md` (+ its doc) — the ONLY thing that knows how to reach the
  CEO: it calls the Hermes **OUTBOUND** function (8.5.4) to deliver a WhatsApp message to the CEO.
  ALL persona/decision logic lives in the agent — **NEVER inside Hermes**.
- Own Claude profile/session. Posts to cards as **`by=<host>/nightwatch:Nightwatch`**. It **NEVER posts as the
  CEO** — and the server enforces this independently of the prompt: an authenticated-Nightwatch write whose
  claimed `by`/`actor` is not `NIGHTWATCH_AGENT` (e.g. `by:"CEO"`) is rejected `nightwatch_cannot_spoof` (§8.5.3;
  J40), so a jailbroken Nightwatch cannot impersonate the CEO.

**8.5.2 CEO-equivalent authority — ONE source.** The Nightwatch's CEO-equivalent authority is defined
**only** in `plans/boss-claude.md` (**Rule 4**), which §8 generates `boss-CLAUDE.md` from — so every
spawned/`--master` Boss internalizes it. The seed does not restate the rule; it references that
single source.

**8.5.3 HARD RULES — SERVER-ENFORCED in `todo-server`, BOUND TO THE AUTHENTICATED CALLER (absolute;
the prompt can be jailbroken, the server cannot).**
🔴 **The server NEVER trusts body-supplied `by`/`actor` for the hard-rule boundary (knightwatch
2026-06-21 — body identity is forgeable by anyone holding a secret).** Instead the Nightwatch authenticates
with its **OWN dedicated credential `NIGHTWATCH_TOKEN`** (`queue.env`, distinct from `QUEUE_SECRET`); the
server **derives the caller identity FROM the authenticated credential** (`NIGHTWATCH_TOKEN` ⇒
caller=`NIGHTWATCH_AGENT`) and applies the Nightwatch hard rules to that **authenticated identity**, regardless of
what `by`/`actor` the body claims. **A request authenticated as the Nightwatch whose claimed `by`/`actor` is
anything other than `NIGHTWATCH_AGENT` (e.g. `by:"CEO"`) is REJECTED outright (`{ok:false,
error:"nightwatch_cannot_spoof"}`) BEFORE any done/add/comment check** — so a jailbroken Nightwatch holding a
secret can neither pose as the CEO nor bypass the rules. (The CEO/Boss/engineers authenticate
WITHOUT the Nightwatch credential and are not subject to the Nightwatch rules.) The hard rules below therefore key
off **the authenticated Nightwatch caller**, never the body's claimed author:
1. **The Nightwatch can NEVER mark a task done — CEO-ONLY, forever, no exceptions.** `todo-server`
   **REJECTS** any transition to `state=done` from an **authenticated Nightwatch caller** — `POST /todo/status
   {state:"done"}`, `POST /todo/update {op:set,…,state:"done"}`, and `set{done:true}` /
   `set{workToDone:true}` — returning **`{ok:false, error:"nightwatch_cannot_done"}`** with the board
   **unchanged** (the body cannot dodge this by claiming `by:"CEO"` — that is `nightwatch_cannot_spoof`). (J41.)
2. **The Nightwatch does NOT create tasks — EXCEPT on an explicit one-shot CEO delegation.** A `POST
   /todo/update {op:add}` from an **authenticated Nightwatch caller** is **REJECTED `{ok:false, error:"nightwatch_cannot_create"}`**
   UNLESS it presents a valid **one-shot CEO-delegation token**. The token is minted **only** by an
   **AUTHENTICATED** inbound (8.5.4 — `/nightwatch/inbound` passes machine auth) CEO WhatsApp message
   matching "Nightwatch, create <X>" (server mints a single-use token bound to that task text, TTL ~10
   min), is **burned on first use**, and a missing/expired/reused token — or a token-mint attempt
   from an **unauthenticated** `/nightwatch/inbound` POST — → rejected. (J42.)
   **WIRE CONTRACT (pin it so mint↔consume agree, like §6): the authed `/nightwatch/inbound` for a
   delegated create MINTS the token AND embeds it in the `[nightwatch]` event it `mp send`s to the Nightwatch
   queue (the Nightwatch learns the token ONLY from its queue, never from the webhook response which goes
   to Hermes). The Nightwatch presents that queued token on `POST /todo/update {op:"add", text,
   actor:<Nightwatch>, token:<minted>}`; the server consumes + burns that `token` and verifies the add
   `text` matches the token's bound text.**

**8.5.4 Event → Nightwatch queue + Hermes = pure bridge (TWO thin functions only — no logic, no persona,
decides nothing). REUSE the host's EXISTING Hermes (CEO 2026-06-21) — do NOT stand up a new bridge,
number, or QR. The existing Hermes already runs paired to the CEO's WhatsApp (a dedicated agent
sender number); the Nightwatch wires its two functions to that gateway.**
- **Event fanout (extends the §6 board→Boss ping — additive; the Boss ping is unchanged).** On the
  SAME two events that ping the Boss — a non-test `add`/work-state change, and **every**
  `/todo/comment` (exempt the Nightwatch's own comment + `{test}` tasks) — `todo-server` **ALSO** enqueues
  the event to the **Nightwatch queue** (`mp send <NIGHTWATCH_AGENT> "[nightwatch] …"`). PLUS a third trigger: the
  **idle-watchdog** — a task with **no CEO/Boss action for `NIGHTWATCH_IDLE_MIN` minutes** (default 30)
  fires one event into the Nightwatch queue so the Nightwatch can draft a nudge/relay. (J43.)
- **Hermes INBOUND** (CEO WhatsApp → Nightwatch queue): the EXISTING Hermes gateway is wired (a `hermes
  webhook` subscription / hook) to POST each inbound CEO message to **`POST /nightwatch/inbound {from,
  text}`** on `todo-server` **at the Nightwatch node's TAILNET address `http://<node-100.x>:9933/nightwatch/inbound`
  (the Hermes host and the Nightwatch node are DIFFERENT machines — never `127.0.0.1`/LAN, §5.2/Option A)**.
  🔴 **AUTH FIRST, then trust `from` (security — knightwatch 2026-06-21): `/nightwatch/inbound` MUST
  require the **`X-Queue-Secret`** machine credential (the SAME seam every other gated route uses —
  no separate secret) and REJECT (401) BEFORE it reads/uses `from`.** Only an authenticated caller's
  `from` is honored; the caller-supplied `from` is NEVER trusted on its own. On an authenticated
  request the server enqueues the event to the Nightwatch queue AND — if `from` is the CEO (`CEO_WHATSAPP`)
  and `text` matches "Nightwatch, create …" — mints the one-shot delegation token (8.5.3 #2). 🔴 **The
  minted token MUST travel WITH the Nightwatch QUEUE EVENT, not only in the webhook response (knightwatch
  2026-06-21): the webhook response goes back to Hermes (the bridge), which is NOT the Nightwatch — so the
  real Nightwatch only ever learns the token from its queue.** The `[nightwatch] …` event the server `mp send`s to
  `NIGHTWATCH_AGENT` for a delegated create MUST embed the token (e.g. `[nightwatch] inbound CEO: create "<X>"
  token=<minted>`), and the Nightwatch presents THAT queued token on its `op:add` (8.5.3 #2). **An
  unauthenticated POST claiming `from=<CEO>` mints NOTHING and is rejected** (J42c). Hermes carries
  the bytes; the Nightwatch interprets them.
- **Hermes OUTBOUND** (Nightwatch → CEO WhatsApp): the Nightwatch's `send-whatsapp` skill reaches the CEO through
  the EXISTING Hermes gateway's bridge send endpoint, behind **`POST /nightwatch/outbound {text}`**. 🔴
  **REACH HERMES OVER THE TAILNET, NEVER LOCALHOST/LAN (CEO 2026-06-21 — Option A).** The live
  Hermes does **NOT** run on the mypeople node — it runs on a SEPARATE host (e.g. the server) and is
  reachable only at that host's **tailnet `100.x` address**. So `/nightwatch/outbound` posts to the
  configured **`HERMES_SEND_URL`** (gitignored `queue.env`) = the live bridge's **tailnet** endpoint
  `http://<hermes-tailnet-ip>:3000/send` — **NEVER `127.0.0.1`/`localhost`** (the Mac's local Hermes
  is dead) and **NEVER a LAN `192.168.x` IP** (the CEO is often off-LAN; only the tailnet is
  reachable, same rule as `attach_base` §5.2). 🔴 **NO SHELL — argv only (security — knightwatch
  2026-06-21): the endpoint MUST invoke the transport with `subprocess.run([…, text], shell=False)`
  (an explicit argv list), NEVER a shell string with the caller's `text` interpolated.** Concretely
  it posts the bridge contract `{"chatId":"<CEO digits>@s.whatsapp.net","message":text}` to
  `HERMES_SEND_URL` (e.g. `subprocess.run(["curl","-s","-H","Content-Type:
  application/json","-X","POST", HERMES_SEND_URL, "-d", json.dumps(payload)], shell=False)`, or the
  equivalent `hermes` argv). `text` is data, never a command fragment (J44b). Hermes is the
  transport; the message + decision are the Nightwatch's. **If `HERMES_SEND_URL` is unset (no Hermes wired
  yet) → 501 stub, no crash.**
- **No Nightwatch logic in Hermes, ever.** The Nightwatch touches the existing Hermes through ONLY these two
  message-moving hooks. (J44 with hermes absent stubs the endpoints — 501, no crash — and gates the
  endpoints + queue wiring + the auth/argv contracts; the live WhatsApp pairing already exists,
  8.5.7.)

**8.5.5 Approve / edit / reject protocol (lives in the Nightwatch agent).** On a queued event the Nightwatch:
reads the task/context → drafts the reply/relay/decision → sends it to the CEO via the
`send-whatsapp` skill, prefixed with the action menu → waits for the CEO's inbound reply:
- **APPROVE** (CEO replies ok/approve/👍) → Nightwatch posts the draft **verbatim** to the card as
  `by=<host>/nightwatch:Nightwatch`.
- **EDIT** (CEO replies with replacement text) → Nightwatch posts **the CEO's text**.
- **REJECT** (CEO replies no/reject [reason]) → Nightwatch **drops** the draft, logs the reason, takes no
  card action.

**8.5.6 Autonomy — L0 only, for now (pre-PMF; knightwatch 2026-06-21).** The Nightwatch runs at **L0:
approve everything** — every draft goes to the CEO via 8.5.5 before any post. No L1/L2 ramp and no
`CTO_AUTONOMY` knob yet; the higher tiers are deferred until trust + usage justify them (we cut LOC
rather than ship unused config). The two hard rules (8.5.3) always hold.

**8.5.7 Channel — REUSE the already-paired Hermes (no new number, no new QR).** The host's existing
Hermes is **already paired** to a dedicated agent sender number and already allow-lists the CEO's
number — so there is **no CEO QR step** for the Nightwatch. **TOPOLOGY (CEO 2026-06-21, Option A): the Nightwatch
and Hermes live on DIFFERENT hosts and span them over Tailscale.** The Nightwatch runs as a mypeople agent
on one node (e.g. the CEO's laptop); Hermes runs on another (e.g. the server). The Nightwatch wires its two
functions (8.5.4) to the running gateway **across the tailnet**: a `hermes webhook` subscription
(Hermes → the Nightwatch node's tailnet `:9933/nightwatch/inbound`, authenticated) for inbound, and `HERMES_SEND_URL`
= the Hermes host's tailnet `:3000/send` for outbound. **Both ends use TAILNET `100.x` addresses —
never a LAN `192.168.x` IP, never `127.0.0.1`/`localhost`** (the CEO is often off-LAN; the tailnet is
the only path; same rule as `attach_base` §5.2). **The CEO's number is NEVER hard-coded — it lives
ONLY in the gitignored runtime `queue.env` as `CEO_WHATSAPP`, never in this seed or any commit.** If
no Hermes exists yet, that one-time pairing is the operator's step (same posture as `claude auth`
§5.4, §9) — the Nightwatch's server-side contracts (J39–J44) are gated independently with hermes
absent/stubbed.

---

## 9. Out-of-scope (host-specific — NOT generated by this seed)

Knowledge preserved so it isn't lost, but **not** part of the gated generative build:
- **WhatsApp drain** (`/todo/wa*`, Hermes last-hop): a host-specific notification bridge. **(The
  Nightwatch agent §8.5 now makes the Hermes inbound/outbound bridge an IN-SCOPE, gated component —
  J39–J44. Only the live WhatsApp QR PAIRING stays a human step, §8.5.7, like `claude auth`.)**
- **Codex backend** (`--backend codex`): the default/only generated backend is `claude`.
- **agentsview / tkmx token-burn + dev-stats reporting:** a separate fleet-telemetry concern
  (installed by the seedbed substrate layer, not the mypeople app).
- **AskUserQuestion remote-answer (`mp answer` widget driving):** `mp answer` is in the CLI
  contract (§4) but its deep widget-driving E2E is not a gate here.

A generated build MAY stub these (e.g. `/todo/wa` returns 501) without failing any §15 gate.

---

## 10. Inputs (Interview)

**Default posture = bare container, paste-and-run.** Assume only a shell + authed `claude` +
`python3`. `## Steps` installs/creates everything else.

| name | required | default | detect | how the seed satisfies it |
|---|---|---|---|---|
| `claude` present + authed | yes | — | `claude auth status` shows "Login method:" | Substrate's one human step (per-node, §5.4). Not done by this seed. |
| `python3` | yes | — | `command -v python3` | Base image; else host pkg mgr. |
| `jq`, `procps`, `ttyd`, `tailscale` | yes | — | `command -v` each | **Steps install** (apt / binary download / install script). NOT assumed present. |
| `/dev/net/tun` + `NET_ADMIN` | yes (tailnet) | — | `[ -c /dev/net/tun ]` | Container must be started with them (§5.6). |
| `QUEUE_SECRET` | no | auto-generate | `grep` `queue.env` | Steps generates if unset. |
| `TS_AUTHKEY` | yes (tailnet) | — | env | Tailscale auth key for `tailscale up` (§5.6). |
| `INSTALL_DIR` | no | `$HOME/mypeople` | — | — |
| `HOST_ID` | no | `$(hostname -s)` | — | Stable node id used in every agent_id. |
| `BIND_ADDR` | no | **`0.0.0.0`** (all interfaces) | `grep` `queue.env` | The address the **queue-server/HUD (`HUD_PORT`) AND todo-server (`TODO_PORT`) bind to.** 🔴 **Default `0.0.0.0` so the board + HUD are reachable over the LAN and tailnet (`192.168.x`, `100.x`), not just localhost** (CEO 2026-06-26: a `127.0.0.1`-only instance can't be opened from another machine — bind all interfaces like ttyd §5.2). Set to `127.0.0.1` ONLY for a deliberately localhost-only sandbox. The internal HUD↔TODO reverse-proxies always use `127.0.0.1` regardless, so the symmetric front doors work either way. |
| `UPSTREAM_QUEUE_URL` + `UPSTREAM_QUEUE_SECRET` | **no (optional — FLEET mode only)** | — | env / `queue.env` | **STANDALONE is the default product (§1): a fresh install with these UNSET is a complete, self-sufficient node — its OWN inner `:9900` is its central + HUD.** Set them ONLY to JOIN an existing fleet central; then the OUTER uplink registers the node there (§5.11) and J12/J13 apply. **A real user's fresh-from-zero install has NO upstream** — never assume one pre-exists. |
| `NODE_PURPOSE` / `NODE_TYPE` / `NODE_RECORDING_URL` | no | `mypeople` / `system-agent` / `` | env | The node's grid grouping label, type, and seedrec link (§4, §7.1). |
| `UPLINK_DIR` | no | `$HOME/mypeople-uplink` | — | Own dir for the OUTER fleet-uplink (§5.11) — isolated from `$INSTALL_DIR` so the inner install can't touch it. |
| `CEO_WHATSAPP` (§8.5) | no | — (operator-supplied) | **gitignored `queue.env` / env ONLY — NEVER committed, NEVER defaulted in this seed** | The CEO's WhatsApp number — the Nightwatch's only approve/edit/reject peer; the only `from` (once 8.5.4-authenticated) that mints delegation tokens. PII: it must never appear in the seed, code, or git history (knightwatch 2026-06-21). |
| `HERMES_SEND_URL` (§8.5.4) | no | — (unset → outbound stubs 501) | gitignored `queue.env` | The live Hermes bridge send endpoint. 🔴 **MUST be the Hermes host's TAILNET address `http://<hermes-100.x-ip>:3000/send` — NEVER `127.0.0.1`/`localhost` (local Hermes is dead) and NEVER a LAN `192.168.x` IP (CEO off-LAN). Option A: the Nightwatch reaches the remote Hermes over the tailnet (§5.2).** |
| `NIGHTWATCH_AGENT` (§8.5) | no | `<HOST_ID>/nightwatch:Nightwatch` | — | The Nightwatch agent_id the server enforces the hard rules against (never-done, create-only-on-token). |
| `NIGHTWATCH_TOKEN` (§8.5.3) | no | auto-generate | gitignored `queue.env` | The Nightwatch's OWN auth credential (distinct from `QUEUE_SECRET`). The server **derives caller=`NIGHTWATCH_AGENT` from this token** and applies the hard rules to the authenticated identity — body `by`/`actor` is never trusted; a Nightwatch-authed write claiming a different author is `nightwatch_cannot_spoof`. |
| `NIGHTWATCH_IDLE_MIN` (§8.5.4) | no | `30` | env / `queue.env` | Idle-watchdog window (minutes of no CEO/Boss action) before a task fires an event into the Nightwatch queue. |
| `hermes` present (§8.5.4) | no | — | `command -v hermes` | The WhatsApp bridge transport. If absent, the Nightwatch still runs and the `/nightwatch/inbound`/`/nightwatch/outbound` endpoints stub (501) — J39–J44 pass with hermes stubbed; the live pairing is the human step (§8.5.7). |

**Step 0 — Interview (mandatory):** detect each; send ONE consolidated message (✓ satisfied / ✗
needed / ⚠ prior install to confirm), then build autonomously to `SEED_RESULT=DONE` or one
`BLOCKED_REASON=`.

---

## 11. Components (what YOU generate — no pre-baked source)

Author each from §3–§8. They interoperate because you write them together to the §4 contracts.
- `bin/queue-server.py` — the HTTP queue + registry + reaper + `/dashboard` + `/roster` (§4,§5.9).
- `bin/queue-client.py` — heartbeat (with tailnet `attach_base`, §5.2), agent re-announce,
  task poll→tmux relay, durable roster/agents (§3). **INNER plane** (→ local queue-server).
- **OUTER fleet-uplink (§5.11)** — a thin client in `$UPLINK_DIR` (own config/pidfile, **no
  local ports**) that connects OUT to the central queue and re-announces the node + the inner
  `main:Boss`. (May reuse the queue-client code pointed at `UPSTREAM_QUEUE_URL`, but fully
  isolated from the inner: separate dir, config, pidfile — the inner's lifecycle never touches it.)
- `bin/mp` — the CLI (§4 verbs), incl. idempotent spawn + the §4 tmux mapping.
- `bin/todo-server.py` + `bin/todos.html` — GENERATED: the TODO board API + board→Boss ping (§6)
  and the page, built from the PLOW tokens (§7) + the §A.2 feature contracts. The page + server you
  write must agree on the §6 API.
- `bin/dashboard.html` — GENERATED: the HUD (§7), built from the PLOW tokens + §A.2 **F20–F22 only**
  (agents table + retired/revive + cross-nav; **NO machines/hydration grid — §7.1 removed**),
  served by queue-server at `/dashboard`; queue-server satisfies `/clients`+`/agents`+`/roster`+
  `/revive`.
- `bin/boss-supervisor.sh` — always-one-Boss loop (§5.3).
- `boss-CLAUDE.md` — generated doctrine (§8), incl. the Nightwatch CEO-equivalent-authority clause (§8.5.2).
- **Nightwatch agent (§8.5)** — scaffold `run/nightwatch/` (`CLAUDE.md` doctrine from the §8.5 intent +
  `plans/nightwatch-claude.md`; `skills/send-whatsapp/`), the `todo-server` extensions (Nightwatch event fanout +
  idle-watchdog §8.5.4; the two SERVER-ENFORCED hard rules §8.5.3; the one-shot delegation token
  mint/burn; `/nightwatch/inbound` **with machine auth before reading `from`** + `/nightwatch/outbound` **argv,
  no shell**), and the two thin functions wired to the host's **EXISTING** Hermes (8.5.4 — `hermes
  webhook` → `/nightwatch/inbound`; bridge send contract for outbound). NO new bridge; NO Nightwatch logic in Hermes.
- `plugins/tmux-boss-hooks/` — the Claude hooks plugin emitting lifecycle events (§4).
- `~/.tmux.conf` — GENERATED from the settings in §A.1 (his style: Dracula via TPM + the hard-won
  TUI fixes) + TPM/Dracula install, sourced into the running server. Gated on the RUNNING config
  (§15 J28), not a file checksum.

---

## 12. Steps — generate & run (zero pre-baked)

Bare host (shell + authed `claude`). State intent; adapt commands to the host.
0. **Interview** (§10).
1. **UPLINK-FIRST (§5.11) — FLEET MODE ONLY; do this BEFORE anything else *if* `UPSTREAM_QUEUE_URL`
   is set. If it is UNSET (standalone — the default fresh install), SKIP this step entirely and go
   to Step 2.** Generate the small outer fleet-uplink
   and **start it immediately** in `$UPLINK_DIR` (own config from `~/.config/mypeople/upstream.env`,
   no local ports), registering the node to `UPSTREAM_QUEUE_URL` with **`state=hydrating`** +
   `purpose`/`node_type`/`hostname` (enrich `attach_base` once the tailnet is up in Step 5). It
   heartbeats throughout. **The node MUST appear on the central hub grid now — before OS-deps,
   before the inner is built** — so the CEO sees it hydrating from the first second. Keep it
   isolated (nothing below may stop/rewrite it).
2. **OS deps.** Ensure `python3, jq, procps(ps), curl, tmux, ttyd, tailscale` — install the
   missing ones (apt / `ttyd` release binary / `tailscale` install script). (On a clean base
   `jq/procps/ttyd/tailscale` ARE missing — install them for real; do not assume.)
3. **Layout + config.** Create `$INSTALL_DIR/{bin,run,status,todos,plugins}`; write
   `~/.config/mypeople/queue.env` (`QUEUE_SECRET` auto-gen if unset, ports, `HOST_ID`,
   `LANG/LC_ALL=C.UTF-8`); set `hasCompletedOnboarding:true` in `~/.claude.json` (§5.5) **AND
   pre-accept folder-trust for the Boss/agent dirs (§5.5c #1): merge
   `projects[$HOME|$INSTALL_DIR|$INSTALL_DIR/run|/run/eng|/run/boss|/bin].hasTrustDialogAccepted=true`
   on this (possibly fresh) `~/.claude.json`** — the install does this itself so a vanilla user's
   Boss spawn never hits the trust dialog (do NOT rely on any external/golden-image seeding).
4. **GENERATE every component** (§11 — servers, the TWO pages, `mp`, supervisor, hooks, doctrine,
   `~/.tmux.conf`) from the spec — write the code now, to the §4–§8 contracts. **The UI is generated
   from the PLOW tokens (§7) + the §A.2 feature contracts** (truly generative — no pasted components;
   pixel-exactness not required, faithful PLOW + every gated behavior is). **`~/.tmux.conf` is
   generated from §A.1's settings**, then TPM + Dracula installed and the conf **sourced into the
   running tmux server** (J28 checks the LIVE server, not a file).
5. **Tailnet** (§5.6): userland `tailscaled` + `tailscale up` + default-socket symlink; capture
   the `100.x` IP — the uplink's `attach_base` updates to it on the next heartbeat.
6. **Start INNER daemons** (§5.8): `queue-server` (wait `/health`), `queue-client` (heartbeat with
   the tailnet `attach_base`), **`ttyd` (§5.7)**, **`todo-server` with `mp` on PATH (§5.1)**.
7. **Spawn the Boss** (`mp spawn <host>/main:Boss --master`), wait for its onboarded summary,
   then **start the Boss supervisor** (§5.3). The outer uplink (Step 1) re-announces the Boss.
7.5. **Nightwatch agent (§8.5).** Scaffold `$INSTALL_DIR/run/nightwatch/` (`CLAUDE.md` from the §8.5 intent
   + `plans/nightwatch-claude.md`; `skills/send-whatsapp/`); pre-trust the cwd (§5.5c). Into the
   **gitignored** `queue.env` write `NIGHTWATCH_AGENT`, `NIGHTWATCH_IDLE_MIN`, and — **ONLY from the operator's
   env if present, NEVER a literal in this seed** — `CEO_WHATSAPP` (PII) and `HERMES_SEND_URL` (the
   Hermes host's **tailnet** `:3000/send`, never `127.0.0.1`/LAN). The Nightwatch still installs + passes
   J39–J44 with both unset. **Spawn the Nightwatch** (`mp spawn <host>/nightwatch:Nightwatch --boss <host>/main:Boss
   --cwd $INSTALL_DIR/run/nightwatch`) — NOT `--master`. Wire the Nightwatch's two thin functions to the
   **EXISTING** Hermes **over the tailnet — Hermes typically runs on a DIFFERENT host (Option A,
   §8.5.7)**: a `hermes webhook` subscription (presenting the `X-Queue-Secret`; target = this node's
   tailnet `:9933/nightwatch/inbound`) → `/nightwatch/inbound`; outbound POSTs to `HERMES_SEND_URL` (argv, no
   shell). If
   `HERMES_SEND_URL`/`hermes` is absent the endpoints stub (501) — do NOT build a new bridge, do NOT
   use localhost/LAN, and do NOT block install on Hermes.
8. **Verify** (§14) — exit code is the truth.

> **🔴 HARD — RUN STEPS 7–8 INLINE TO `SEED_RESULT=DONE`; NEVER BACKGROUND THE BOSS-SPAWN OR THE
> VERIFY, NEVER PARK ON A WAKEUP (folded 2026-06-22, 5-node hydration break-point B2).** The
> generating agent MUST, in the SAME working turn: (a) spawn the Boss, (b) **block until it has
> itself observed `<host>/main:Boss` `state=alive` in `GET :9900/agents`** (services answering 200
> is NOT enough — the inner Boss must be ALIVE in the registry), then (c) run `## Verify` to exit 0,
> then (d) print `SEED_RESULT=DONE`. It is a **FAILURE** to: launch the Boss-spawn or `## Verify` as
> a background/detached shell and return; schedule a wakeup / "I'll check later" and yield; or print
> `SEED_RESULT=DONE` before having inline-confirmed the Boss alive + Verify exit 0. A node whose
> `:9900`/`:9933` serve 200 but whose `/agents` has **no live Boss** is **NOT done** — that is exactly
> the half-up state this rule forbids. (Defect: a generating Boss backgrounded Step 7+8 onto two
> detached shells + a ScheduleWakeup and parked; the wakeup it relied on never drove the work, so the
> inner Boss was never spawned and Verify never ran — the node sat half-up indefinitely.)

9. **Flip the uplink state** `hydrating`→**`ready`** when Verify passes (or `failed` on a blocker).
   Confirm the node + Boss show on `$UPSTREAM_QUEUE_URL/clients`+`/agents` as `ready`. Keep the
   uplink up for the node's life.

---

## 13. Done (observable)

- `curl :9900/health` ok; `:9900/dashboard` and `:9933/` serve 200 and are reachable on the
  node's **tailnet IP** from another tailnet machine.
- `mp status` lists `<host>/main:Boss [alive]`; the HUD `/agents` shows it alive.
- Adding a task on the TODO pings the Boss (the Boss pane receives `[todo] …`).
- Killing the Boss → the supervisor brings it back into the HUD with no human.
- The HUD attach link / a card commenter's name opens that agent's live terminal.
- Both pages carry the PLOW identity and cross-link to each other.

---

## 14. Verify (runnable acceptance harness — exit code = truth, self-contained)

`## Verify` is a script you generate; **its exit code is the truth (0 = Done)**. It runs on the
host after `## Steps`, **self-installs any tool it needs** (never assume a pre-baked browser/jq),
and asserts the §15 journeys against **absolute values in this spec** — it must NOT diff against
any reference mypeople instance or golden screenshot. A blind generate on a clean node must reach
exit 0 on its own merit. Print each gate's pass/fail line; finish the core path in < 5 min.
Cleanup must **leave the master Boss alive** (the done-condition needs it in the HUD) and only
kill ephemeral test workers. **Run Verify INLINE in the generating turn and BLOCK on it — never
background/detach it or park on a wakeup (§12 Step 8 HARD rule, B2). `SEED_RESULT=DONE` is valid
only after the agent has, in that same turn, observed `main:Boss` `alive` in `/agents` AND Verify
exit 0.**

> **CANONICAL ACCEPTANCE = a SINGLE STANDALONE node with NOTHING pre-existing.** The real test is
> one fresh host, `UPSTREAM_QUEUE_URL` UNSET, no hub/fleet anywhere, reaching exit 0 on J1–J11 +
> J14–J44 (its own inner `:9900` is the central + HUD). **Verify must NOT depend on any
> pre-existing hub** — if a gate only passes because a prior-generation central happens to exist,
> the test is contaminated (CEO 2026-06-17). FLEET mode (J12/J13) is a SEPARATE, opt-in scenario:
> to test it, generate a FRESH hub from THIS seed first (a standalone node = a central), then JOIN
> fresh nodes to it — never to a survivor container.

---

---

## 15. Verification journeys (the gates — ALL must pass, asserted on this node only)

1. **Install one-shot.** From a fresh bare node, `## Steps` runs to `SEED_RESULT=DONE` with no
   ad-hoc fixes; `:9900/health` ok; `bin/` has the generated components.
2. **Boss in the HUD.** `mp status` shows `<host>/main:Boss [alive]`; `GET /agents` (with secret)
   contains it with `state=alive`; the Boss's onboarding summary carries ≥2 doctrine keywords
   (plan/approve/queue/mp/fire-and-forget/autonomous). *(Assert the INSTALLED Boss — do not spawn
   a fresh one to mask a missing one. No Boss in the HUD = FAIL, even if everything else passes.)*
   **The summary must be DURABLE (folded 2026-06-17, mpgen5-1/-3):** the Stop hook overwrites the
   Boss's roster summary on its next idle turn, so a doctrine summary captured at onboarding can be
   clobbered to a generic line — persist the onboarding summary so it survives later Stop-hook
   writes (don't assert a freshly-spawned Boss to mask this).
3. **Board → Boss ping.** Add a **non-test** task via `POST /todo/update {op:add,text:…}` while
   the Boss is idle. *Expect:* it lands on `/todo/board` AND the **Boss pane receives the
   `[todo] … <taskId> …` ping** within ~30s (key off pane-delivery; a busy Boss may rc=1 yet the
   ping still pastes). An EMPTY Boss pane = the §5.1 `mp`-not-on-PATH regression ⇒ FAIL.
4. **Supervisor resurrection.** The supervisor daemon is alive; kill `mc-main:Boss`; *Expect:* it
   **auto-respawns** and reappears `alive` in `/agents` within the supervisor cycle (no human).
5. **TODO add-task round-trips.** A task created via the API is read back on `/todo/board` and
   shown on the page; the app serves 200 on `:9933`.
6. **Cross-nav.** `:9933/` HTML links to `:9900/dashboard`; `:9900/dashboard` links to `:9933`.
7. **Click-to-terminal.** The TODO comment thread wires an attachable commenter's name to the
   attach action, and `GET /todo/attach?agent=<host>/main:Boss` returns `ok` + a
   `mc-<sess>:<tab>` target. (Strongest: opening the attach URL renders the live pane.)
8. **Attach opens the LIVE pane.** ttyd is bound on the **advertised** port; the attach URL
   `<attach_base>/?arg=-t&arg=mc-main:Boss` returns 200 and the target is a **live (non-dead)**
   pane; a stray `pkill -x ttyd` is respawned (still 200 after ~5s) per §5.7.
   🔴 **HTTP 200 is NOT sufficient** — ttyd serves 200 even when `tmux attach -t mc-main:Boss`
   fails *inside* the terminal with `can't find window: Boss` (the failure is in the pane, not the
   status code). This gate MUST ALSO prove the attach **target window resolves**:
   `tmux list-windows -t mc-main -F '#{window_name}' | grep -qx Boss` (the spawned Boss window is
   actually NAMED `Boss`, per the mp-spawn window-naming + `automatic-rename off` contract), AND the
   attach pane does NOT contain the string `can't find window`. A gate that checks only the 200 is a
   **false-green** and is rejected.
   🔴 **AND the HUD must RENDER the clickable ATTACH button** (the URL resolving is necessary but
   not sufficient — the human reaches it by clicking the row). Fetch the served `/dashboard` HTML
   (with the §5.12 cookie) and assert the live Boss row carries a working attach control: the served
   markup contains an `href`/`onclick` whose value equals `<attach_base>/?arg=-t&arg=mc-main:Boss`
   (i.e. `/agents` joined `attach_base`+`attach_url` onto the Boss row per §4/§5.2, and the render
   emitted the anchor). Concretely: `/agents` for the live Boss MUST return non-empty
   `attach_base` and `attach_url`, AND the rendered HUD MUST contain that `attach_url` as a
   clickable link in the Boss row. An empty ATTACH cell for a live, heartbeating agent is a
   **false-green** and is rejected.
9. **PLOW identity.** BOTH `:9933/` and `:9900/dashboard` carry **Volt `#D5EF8A`** + the Plow
   typefaces (`Instrument Serif`/`DM Sans`/`DM Mono`).
9a. **Wordmark/titles (CEO 2026-06-25, reconciled to match live).** TODO `:9933/`: the browser-TAB
    `<title>` tag = `MyPeople - Priorities`, but the VISIBLE board H1 (Instrument-Serif) = exactly
    **`Priorities`** — NO visible "MyPeople - Priorities" heading/eyebrow. HUD `:9900/dashboard` = `MyPeople - HUD`.
    Assert: served `:9933/` `<title>` contains `MyPeople - Priorities` AND the visible `<h1>` text == `Priorities`
    (NOT `MyPeople - Priorities`); `:9900/dashboard` contains `MyPeople - HUD`. FAIL if the board renders a
    visible `MyPeople - Priorities` heading/eyebrow.
9b. **Status badge displays working/idle (§7.5, CEO 2026-06-25) — the §4 hook WRITES it, the HUD must
    SHOW it.** `/agents` MUST carry a `status` field (not only `state`). `GET /todo/wall` (`:9933`, with
    `X-Queue-Secret`) MUST return tiles each carrying a display `state`. END-TO-END: spawn an agent, send
    it a multi-second prompt, and while it runs assert its tile/row shows **`working`** (its status file
    `status/mc-<sess>/<tab>.json` shows `working`, `/agents` reflects `status:"working"`, the `/todo/wall`
    tile `state=="working"`); after `Stop` it returns to `idle`. FAIL if the agent churns but the HUD/wall
    still shows idle — that is the dropped-display bug §7.5 exists to prevent. (A from-memory build that
    writes the status file but never reads it back into `/agents`/`/todo/wall` trips this gate.)
9c. **Exact palette (§7 tokens, CEO 2026-06-25).** The served `:9933/` + `:9900/dashboard` MUST define
    the §7 custom properties with the EXACT values — assert the CSS contains `--volt:#D5EF8A`,
    `--midnight:#01000A`, `--dark-bg:#111110`, `--text-dark:#F0F0E8`, `--surface:rgba(255,255,255,0.05)`
    (GLASS surface — NOT a solid `#1A1A18` card fill), `--iris:#C4BFFF`, `--warning:#febc2e`,
    `--danger:#ff3b30`, and the `Instrument Serif`/`DM Sans`/`DM Mono` families. FAIL on a solid card
    color or a palette that diverges from these literals.
9d. **No proof-attach UI (§7.7, CEO 2026-06-25).** The served `:9933/` MUST contain **NO** `<input
    type="file">`, NO "add proof"/"choose file" button, and NO proof media-URL input — `grep`-assert
    their ABSENCE. (Proofs still post + render via the API, J22.) A served page exposing a file picker or
    "Add proof" control = FAIL.
9e. **One-click DONE = on-card `.check` toggle, matches live (§7.6, CEO 2026-06-25).** Every card row
    MUST render a `.check` toggle as its LEFTMOST element (38px rounded-square, `border:2px var(--border2)`,
    transparent). Assert on the served `:9933/`: (a) the markup contains a `.check` element per card; (b)
    clicking `.check` on a not-done task sets it `done` in ONE click (`POST /todo/status {state:"done",
    verified:true, by:"CEO"}`) WITHOUT opening the card (handler stopPropagation); (c) a done task's `.check`
    has class `on` (green `--success`) + shows `✓`, and clicking again un-dones it (`state:"working"`); (d)
    the ★ pin star is a SEPARATE element (pin-only). FAIL if there is no `.check` toggle, if it opens the
    card, if marking done needs >1 click, or if the only done affordance is a dropdown/select or the star.
10. **Reachable from the human's machine.** The HUD + TODO answer 200 on the node's **tailnet IP**
    (not just localhost) — i.e. `attach_base`/pages use the `100.x` address (§5.2). The node ALWAYS
    joins the tailnet non-interactively via `TS_AUTHKEY` (§5.6) — including an inner/nested product
    container, which its substrate provisions with the key. (Folded 2026-06-18: this gate is NOT
    skippable; the earlier "N/A when no tailnet" idea was wrong — the fix is to provision the authkey,
    not to skip. An interactive `tailscale up` browser login is the bug, never the join itself.)
11. **REMOVED (CEO 2026-06-18) — no machines/hydration grid in the generated HUD (§7.1 removed).**
    The product HUD no longer renders a per-`purpose` grid; this gate is dropped. (The `/clients`
    endpoint + `purpose`/`state` fields still exist for the OUTER fleet uplink, §5.11 — not gated
    here.) Do NOT assert a grid; a generated HUD that ships a machines/hydration grid = FAIL of the
    "no removed features" check.
12. **Two-plane isolation — inner install never knocks the node off the central grid (§5.11).**
    **(FLEET-MODE ONLY — SKIP this gate entirely when `UPSTREAM_QUEUE_URL` is unset; a standalone
    install has no OUTER plane and is still fully Done.)** Prove the OUTER uplink and INNER product
    are isolated and the node stays visible:
    (a) `GET $UPSTREAM_QUEUE_URL/clients` lists this `hostname` (with `purpose` + tailnet
    `attach_base`) and `GET $UPSTREAM_QUEUE_URL/agents` lists `<host>/main:Boss` `alive` — i.e.
    the node + its INNER Boss show on the **central** grid, not just locally;
    (b) the OUTER uplink runs from `$UPLINK_DIR` (separate dir/config/pidfile from `$INSTALL_DIR`)
    and binds **no** local ports;
    (c) **re-run the INNER install AND restart the inner daemons (queue-server/client/ttyd/todo),
    then re-assert (a) still holds within one heartbeat** — the inner lifecycle must be incapable
    of stopping or re-pointing the outer uplink. A node serving its own HUD/TODO but absent from
    the central `/clients`+`/agents` (or whose outer uplink died when the inner restarted) = the
    island regression ⇒ FAIL.
13. **Uplink-FIRST hydration visibility (§5.11).** **(FLEET-MODE ONLY — SKIP when `UPSTREAM_QUEUE_URL`
    is unset.)** The node must appear on the central hub grid
    as **`hydrating` BEFORE its inner is up** — so the CEO sees it the moment it starts. *Assert:*
    the outer uplink started first (its pidfile/first heartbeat predates the inner queue-server's
    start; equivalently, during a fresh bring-up the node shows on `$UPSTREAM_QUEUE_URL/clients`
    with `state=hydrating` while `:9900` is still down), and after install it shows `state=ready`.
    A node that only appears on the hub AFTER its inner is up = the uplink-late regression ⇒ FAIL.
    (N substrates must show as `hydrating` on the grid concurrently while they build — not 1.)
14. **Generative UI fidelity (Decision B — NO checksum).** The UI is GENERATED, not pinned: assert
    the served `:9933/` and `:9900/dashboard` (a) carry the **PLOW tokens** — Volt `#D5EF8A` +
    `Instrument Serif`/`DM Sans`/`DM Mono` (also J9); (b) are **not a pasted prior component** — the
    seed ships NO UI bytes to diff against; correctness = passing the behavioral F-gates, not byte
    identity; (c) contain **no `@keyframes`/`animation:`** (J29) and **no manual reorder** (`op:'reorder'`
    unsupported, no up/down control). A faithful PLOW UI that clears every gate is correct even though
    it is not pixel-identical to any prior app.
15. **Delete task.** `update{op:del,id}` removes the task from `/todo/board` (tasks + order). (F2)
16. **Inline edit.** `update{op:set,id,text|doneCondition|assignee}` patches that field (note the
    REAL names per §6); read back on the board. (F3)
17. **State enum (boss-doctrine model — REVERTED 2026-06-26 per Boss; was the 2026-06-18 idle model).**
    A newly-added task is born **`needs_brainstorm`**. Setting each of
    `needs_brainstorm|working|review|done|blocked|cancelled` via `set{state}` (field **`state`**, NOT
    `status`)/`/todo/status` persists + reads back; **`idle` is NOT a valid state** (a
    `set{state:"idle"}` is rejected); any invalid value is rejected (400). The `review` state DISPLAYS
    as **"review (CEO)"**. (F4)
18. **Done toggle.** `set{done:true}`/`set{workToDone:true}` moves the task to `state=done`; the
    board reflects it. (F5)
19. **needs_brainstorm → working with NO gate (boss-doctrine model, REVERTED 2026-06-26; the
    born-state is named `needs_brainstorm` but there is NO blocking gate).** A fresh task is
    `needs_brainstorm`; `set{state:"working"}` (an engineer picking it up) succeeds with **no
    brainstorm/answer gate** in the way. *Assert:* a just-added task has `state=needs_brainstorm`; it
    can go straight to `working`. (F6)
20. **Brainstorm gate is GONE (folded 2026-06-18).** Assert there is **no `/todo/brainstorm` and no
    `/todo/answer` route** (404/unsupported), **no `brainstorm` field** on tasks, and **no
    "needs-brainstorm" banner** in the UI. Any of these present = FAIL (the gate was cut). (F7)
21. **Unread count.** `/todo/board` returns a per-task `unread` integer that rises when a new
    comment is added by someone other than the reader. (F9)
22. **Proofs.** `/todo/proof{task_id,kind,url|body}` (kind ∈ image|video|link|text) appends to the
    task's `proofs[]`, returned on the board. (F10) 🔴 **Shape + classify + render gate (CEO
    2026-06-18, two failures folded):** add a proof of an **image (a real `.png`)** AND a **video (a
    real `.mp4`)** **via the API (`POST /todo/proof`, incl. multipart upload — there is NO UI proof
    control, §7.7)**, then assert: (1) `/todo/board` stores each as the EXACT contract shape
    `{kind, url, body, ts}` — NOT `{type,ref}`; (2) the server **CLASSIFIED `kind` from the media** —
    the `.png` is `kind:"image"` and the `.mp4` is `kind:"video"`, **NOT `kind:"text"`** (the
    blind-default bug); (3) the rendered card shows a real `<img src=…>` / `<video src=…>` chip, not a
    text chip or blank. An image/video accepted but stored/rendered as `text` = FAIL.
23. **NO subtasks / dependencies / hard-gate (REMOVED — CEO 2026-06-17).** Assert these are ABSENT:
    the generated `todos.html` contains no "Add subtask", "add a dependency", "blocked by", or "hard
    gate" controls; and the backend does NOT implement `add{parent}` / `parent`, `dependsOn`, or
    `hardGate` (a `set` with those keys is ignored/rejected, not persisted). Any of these present =
    FAIL (the feature was explicitly cut). (replaces old F13)
24. **Verified badge.** A task with `verified=true` on the board is served with the "verified"
    badge in the page. (F16)
25. **Retired + revive.** `/roster` carries `retired` entries; `POST /revive{agent_id}` clears the
    retired flag (agent re-eligible), reflected on the next `/roster`. (F21) **Test it WITHOUT
    leaving a phantom:** any agent/host you register to exercise retire/revive MUST be removed before
    the gate returns — no `retiredtest`/`ghosthost`-style residue on the live `/roster` or grid.
25a. 🔴 **Spawn/revive command visible in the HUD (CEO 2026-06-26, §3/§7).** Spawn a real engineer,
    then assert: (1) its `GET /agents` row carries a non-empty **`spawn_cmd`** (matching the roster's
    `spawn_cmd` for that `agent_id`) AND **`revive_cmd` == `mp revive <agent_id>`**; (2) the served
    `/dashboard` HTML renders that agent's spawn command in the agents table (the SPAWN CMD column —
    grep the page for the agent's `spawn_cmd` text, or its copy/expand affordance carrying it) AND its
    `mp revive <agent_id>`. FAIL if `/agents` omits `spawn_cmd` while the roster has one, or the HUD
    page shows no spawn command for a live agent. (F-spawncmd)
25b. 🔴 **Runtime data isolation + defensive board backup (§3, 2026-06-26 incident).** Assert:
    (1) the served board path is **`$INSTALL_DIR/todos/board.v2.json`** (under this install's own
    `$INSTALL_DIR`), and `$INSTALL_DIR` is NOT inside a git working tree — `git -C "$INSTALL_DIR"
    rev-parse` fails, OR `todos/board.v2.json` is git-ignored there. (2) `save()` is atomic and rolls
    a timestamped `todos/board.v2.json.bak.<epoch>` (after ≥2 mutations there is ≥1 `.bak.*`; count
    capped ~20). (3) **catastrophic-shrink guard:** with a multi-task board on disk, a forced reload
    that would drop &gt;50% of tasks does NOT overwrite `board.v2.json` (it writes `*.SUSPECT.*`
    instead). FAIL if the board path is shared/ git-tracked, no backups roll, or a >50% shrink
    silently overwrites the live board. (F-isolation)
25c. 🔴 **Git-tracked board export + restore (§3, card 2bf4e6c76a3a).** Drive the whole recovery loop
    against a sandbox board+export-repo (same exporter/restore code, env-pointed so the live board is
    never risked) and assert: (1) **change→commit** — a board change lands a NEW commit in the export
    repo whose `HEAD:board.v2.json` contains the change; (2) **read-only** — the exporter does NOT write
    the live board (sha256 of the board file is unchanged across an export run) and the export repo is a
    SEPARATE dir outside any server working tree; (3) **wipe auto-quarantined** — wiping the board to
    &lt;50% makes the exporter write a `*.SUSPECT.*` and KEEP `HEAD:board.v2.json` at the last good full
    count (never promoted); (4) **restore-to-CURRENT** — `board-restore` (HEAD) brings the live board
    back to the full count INCLUDING the change, after writing a `*.bak.prerestore.*` first. FAIL if the
    export path writes the live board, if a wipe is promoted to HEAD, or if restore does not recover the
    current state. (F-gitexport) **(5) PER-INSTANCE repo (boardgit 2026-06-26)** — two installs that
    differ in `$INSTALL_DIR`/port MUST resolve to DIFFERENT `EXPORT_REPO` paths (the path carries an
    instance discriminator, not bare `<HOST_ID>`); assert two configs with the same `HOST_ID` but
    different `$INSTALL_DIR` produce distinct repo dirs. FAIL if two co-hosted instances would share one
    backup repo (history flip-flop).
26. **REMOVED (CEO 2026-06-18) — no machines grid means no grid-cleanliness gate.** With §7.1/J11
    gone, the generated HUD shows no machines grid, so there is no grid to pollute with test
    fixtures. (The queue-server may still expire stale `/clients` entries as good hygiene, but it is
    not gated here.) `/clients` is not asserted to be "clean" because it isn't rendered in the
    product HUD anymore.
27. **Attach links resolve to a REAL host — never a placeholder (CEO 2026-06-17).** Every
    `attach_base` advertised in `/clients`/`/agents` and every ATTACH link the HUD renders MUST
    contain the node's **real reachable host** (its `100.x` tailnet IP per §5.2) — **never a literal
    placeholder (`x`, `100.0.0.0`, `<host>`, empty) and never `127.0.0.1` for a remote-reachable
    link.** *Assert:* the node's own `attach_base` matches its `tailscale ip -4`; the rendered
    `ATTACH BOSS` href is `http://<100.x>:7681/?arg=-t&arg=mc-main:Boss` and returns 200 on a live
    pane (§J8). A placeholder host in any attach link = FAIL.

28. **RUNNING tmux IS his config — not just the file on disk (CEO 2026-06-17, J14-on-disk was a
    FALSE GREEN).** Assert against the **LIVE tmux server** the Boss/agents actually run in (the
    `mc-*` sessions), via `tmux show-options -g` / `tmux list-keys`, NOT the file: **`base-index`
    is `1`** (not 0), **`history-limit 50000`**, **`renumber-windows on`**, `escape-time 10`,
    `default-terminal "tmux-256color"`; **`WheelUpPane`/`WheelDownPane` are UNBOUND at `-T root`**
    (his anti-trap fix — they must NOT appear in `list-keys -T root`); **`MouseDragEnd1Pane` is bound
    to `copy-pipe-and-cancel`**; and **Dracula is loaded** — verify by the RUNNING status bar being
    Dracula's (not the default `%H:%M %d-%b-%y`); note TPM clones `@plugin 'dracula/tmux'` into
    `~/.tmux/plugins/**tmux**` (the repo basename, NOT `~/.tmux/plugins/dracula`) — folded 2026-06-17,
    so check the status bar behavior, not a fixed dir name. If the live server runs defaults
    while the file is correct = the "server started before the conf / TPM never installed" regression
    ⇒ FAIL. **This gate is READ-ONLY (folded 2026-06-18): use only `tmux show-options`/`list-keys`;
    NEVER `tmux kill-server` or kill sessions to reload — that destroys detached flows incl. an
    in-progress `claude auth login` (§A.1). Apply config via `source-file` only.**
29. **Clean minimalist — NO animations/effects (CEO 2026-06-17).** The generated `dashboard.html` +
    `todos.html` contain **zero `animation:` / `@keyframes`** (no zoom/pulse on the `hydrating` state
    label or the live-dot, no fade-in on tasks). Assert the served pages contain no
    `@keyframes`/`animation:` declarations. Any animation on a state label = FAIL.
30. **NO secret in the browser (security — §5.12, CEO 2026-06-18).** Fetch the served `:9933/`,
    `:9933/todos`, and `:9900/dashboard` bytes and assert **the live `QUEUE_SECRET` value does NOT
    appear** anywhere in them, and there is **no secret-bearing token** (`__INJECT_SECRET__`, a
    `const SECRET="<nonempty>"`, an `X-Queue-Secret` header hard-set to the real value, or the secret
    in any attach/recording URL). Then prove auth still works WITHOUT a client secret: a fresh
    browser session (cookie jar, no `X-Queue-Secret`) loads the page and its same-origin calls to a
    gated endpoint (e.g. `/todo/board`) succeed via the httpOnly cookie; the SAME gated endpoint
    called cross-origin/cookieless WITHOUT the header is **rejected**. Secret in client bytes = FAIL.
    **HEADER-LEVEL Set-Cookie check (folded 2026-06-18 — catches the transient-401 gap WITHOUT a
    browser): `curl -sI http://127.0.0.1:9933/` AND `…/dashboard` MUST each return a `Set-Cookie:`
    header (httpOnly session) on the PAGE GET itself.** No `Set-Cookie` on the page GET = FAIL (the
    page would 401 its first board fetch → browser-QA J31/J33/J34 break). This makes the §5.12
    page-Set-Cookie requirement enforceable even when the puppeteer gates can't run.
31. **Browser-QA pass (real page, real interactions — CEO 2026-06-18).** Load `:9933/` and
    `:9900/dashboard` in a real browser (headless ok): **zero console errors / failed requests**, the
    board renders, **add-task works from the UI** (type + Enter → the task appears), the card modal
    opens, a comment posts, and the HUD renders (**agents + retired tables; NO hydration grid** — §7.1
    removed). A page that 403/401s its own API, throws in console, has a dead control, or can't add a
    task = FAIL. **Two first-try traps it MUST clear with ZERO live patches (folded 2026-06-18):**
    (a) **`GET /favicon.ico` returns 204/200, never 404** (§5.13 — the browser auto-requests it; a 404
    is a console error); (b) **no transient cookie-401** — the page GET sets the session cookie
    (§5.12) so the first `/todo/board` fetch already carries it (no 401 in console). A blind generate
    that omits the favicon route or the page Set-Cookie trips this gate first-try.
    🔴 **(c) Modal close ALWAYS works with a normal click (CEO 2026-06-18, §7).** Open a card modal,
    then click the **✕** — assert the modal actually closes with a **plain click** (no force-click):
    after close, BOTH the panel and backdrop are hidden (no `#modal{display:block}` +
    `#modalbg{display:none}` desync), and re-opening + closing via **Escape** and **backdrop click**
    also work. A modal that needs a force-click or leaves a stuck hidden-DOM state = FAIL.
    🔴 **(d) ATTACH lands on the ROW's OWN window AND STAYS CONNECTED (CEO 2026-06-18, §5.7).** Click
    the **Boss** row's ATTACH while an engineer window is the session's active window → the opened
    ttyd pane shows the **Boss** pane (not the engineer's), and clicking an engineer row shows THAT
    engineer — each row isolated (grouped-session per viewer). The grouped view-session MUST persist:
    the pane shows live Boss content and does **NOT** show `can't find session: _v_…` or ttyd's
    "Press Enter to Reconnect" (the detached-session-reaped-by-destroy-unattached regression). An
    attach that lands on the active window, or whose session vanishes on connect = FAIL.
32. **FULL E2E comms loop — the joke protocol (CEO 2026-06-18).** First-time, no hand-holding: (a)
    create a real task "tell me jokes, one per turn"; (b) the node's **Boss receives the ping** and
    **spawns an engineer**; (c) the engineer posts **joke #1 as a `/todo/comment`** (its agent_id) and
    **waits** — does NOT dump 3 at once; (d) the **comment-ping reaches the Boss** for the engineer's
    post (proving comment→ping, §6); (e) **a CEO follow-up comment** ("another") **pings the Boss**
    (NOT exempt — only the Boss's own comments are), which drives **round 2** (joke #2), then round 3.
    *Assert:* the Boss pane receives BOTH a `[todo] task…` and a `[todo] comment…` line; ≥2 distinct
    engineer joke-comments land on the board across rounds, one per round (not batched). Any link
    broken (comment doesn't ping, engineer batches, Boss doesn't spawn) = FAIL.
33. **Hot reload — no manual refresh (§7.2, CEO 2026-06-18).** With `:9933/` (and an open card) already
    loaded in a browser, POST a new `/todo/comment` (and a new task) out-of-band; **within ~5s the new
    comment appears in the open thread and the new task on the board WITHOUT a reload**; likewise the
    HUD reflects a new `/agents`/`/roster` change live. A page that needs F5 to show the new comment =
    FAIL.
34. **Live-reload preserves FOCUS + CARET (§7.2, CEO 2026-06-18).** In a real browser, **focus the
    add-task input and type slowly across ≥2 poll cycles** (e.g. type a char, wait ~1.2s for a poll,
    type more): assert **focus stays on the input and the caret/selection is NOT reset** — the typed
    text accumulates intact, no characters lost, cursor not yanked to start/elsewhere. Repeat for an
    inline-edit field + the comment composer. A poll that steals focus or resets the caret = FAIL.
    (F25)
35. **The PRODUCT handles folder-trust on a VANILLA machine — no substrate/image assist (§5.5c, CEO
    2026-06-18).** This gate MUST prove the product stands alone, so it tests on a **CLEAN trust
    state**: first **strip any pre-seeded trust** — `python3 -c 'import json,os;p=os.path.expanduser
    ("~/.claude.json");d=json.load(open(p));d["projects"]={};json.dump(d,open(p,"w"))'` (wipe the
    `projects` trust map so NOTHING is pre-trusted, simulating a real user's fresh box). THEN confirm
    the product's OWN logic re-establishes trust: (a) re-running the install's trust step (or the
    generated `mp spawn`) restores `projects[<boss cwd>].hasTrustDialogAccepted=true`; (b) `mp spawn`
    an agent in a **fresh `mkdir`'d cwd** → it reaches the bypass banner **WITHOUT** any "trust this
    folder?"/onboarding prompt and WITHOUT blocking; agent ends `alive`. **The golden-image bake (or
    any externally-seeded trust) MUST NOT be what makes this pass** — that's why we wipe first. A
    spawn that hangs on a trust dialog after the wipe = the product doesn't self-handle trust = FAIL.
36. **Nested spawn does NOT disconnect (engineer-from-engineer, §4 mp-spawn, CEO 2026-06-18).** The
    CEO bug: an engineer born from a TODO comment runs `mp spawn` to create ANOTHER engineer and the
    ttyd/tmux session DROPS. This gate proves a nested spawn leaves everything intact. (a) Record the
    parent session id + window list + attached client: `tmux list-windows -t mc-main -F '#{window_name}'`
    and `tmux list-clients -t mc-main`. (b) Open an attached ttyd client to some window
    (`mc-main:Boss`) and confirm it renders. (c) From INSIDE an engineer's pane (e.g. `mp send
    <eng> "mp spawn mpgen…/main:eng-child --cwd …"`, i.e. spawn invoked from within tmux), create a
    child engineer. (d) Assert: `tmux has-session -t mc-main` STILL true; the pre-existing windows
    are ALL still listed (none killed); the attached client from (a) is STILL attached (`list-clients`
    shows no drop, ttyd attach URL still 200 + live pane); and the new child window
    `mc-main:eng-child` now ALSO exists + is `alive` in `/agents`. If the parent session was killed,
    a window vanished, or the client was switched/dropped = FAIL (the spawn clobbered the session).
37. **PINNING — WhatsApp-starred tasks (§7.3, CEO 2026-06-20).** Drive it end-to-end via the API and
    assert the board reflects it: (a) **Pin floats to top:** add ≥2 tasks, `update{op:'pin',id}` one
    → `/todo/board` (or the rendered page) shows it ABOVE the normal tasks. (b) **Pin order =
    insertion order:** pin five tasks T1..T5 in sequence → the pinned group is ordered T1,T2,T3,T4,T5
    by ascending `pinRank` (the order they were pinned), above all normal tasks. (c) **Max 5
    enforced:** with 5 pinned, `update{op:'pin'}` a 6th returns **`{ok:false, error:"pin_limit"}`**
    and the 6th is NOT pinned (still in its normal position). (d) **Unpin restores:** `unpin` one →
    its `pinned=false`/`pinRank=null`, it drops back to its normal `order` position, and the
    remaining pins keep their order; a 6th pin now succeeds. (e) **Persists:** the `pinned`+`pinRank`
    survive a board re-fetch AND a `todo-server` restart (they're in `board.v2.json`) — re-read
    `/todo/board` after a restart and the pinned set + order are unchanged. (f) **UI affordance:** the
    rendered page has a per-card ★ control and a distinct pinned group at top. Any of: pin not on top,
    wrong pin order, a 6th pin accepted, unpin not restoring, or pins lost on reload/restart = FAIL.
38. **JUMP-TO-LATEST in the comment thread (§7.4, CEO 2026-06-21).** In a real browser: open a card
    and post enough comments that the thread overflows its scroll area (≥~15 comments). Then assert:
    (a) with the thread scrolled to the bottom, the floating jump-to-latest button (↓) is **HIDDEN**;
    (b) scroll the thread UP → the button **APPEARS**; (c) click it → the thread **smooth-scrolls to
    the newest comment** (bottom) and the button **hides again** once at the bottom. Verify via the
    rendered DOM: the button element exists, its visibility toggles with the thread's scroll position
    (`scrollHeight - scrollTop - clientHeight`), and after click `scrollTop` is at the bottom. A
    button that never appears, never hides, doesn't scroll to the latest comment, or throws in console
    = FAIL.
39. **Nightwatch agent is alive in its OWN folder + profile (§8.5.1, CEO 2026-06-21).** After install,
    `mp status` / `GET /agents` shows **`<host>/nightwatch:Nightwatch [alive]`** with `boss_id=<host>/main:Boss`
    (NOT a master). Its tmux window is `mc-nightwatch:Nightwatch` (cwd `$INSTALL_DIR/run/nightwatch`), `run/nightwatch/CLAUDE.md`
    + `run/nightwatch/skills/send-whatsapp/` exist, and its **durable onboarding summary carries ≥2** of
    {`nightwatch`,`ceo-equivalent`,`approve`,`whatsapp`,`never-done`} (like J2c). A Nightwatch that is
    a master, has no folder/skill, or a 0-keyword summary = FAIL.
40. **Nightwatch NEVER posts as the CEO — IDENTITY BOUND TO AUTH, not the body (§8.5.1/§8.5.3, CEO
    2026-06-21 + knightwatch).** A request **authenticated as the Nightwatch** (header `NIGHTWATCH_TOKEN`) whose body
    claims `by`/`actor` = `"CEO"` (or anything ≠ `NIGHTWATCH_AGENT`) — on `/todo/comment`, `/todo/status`, or
    `/todo/update` — is **REJECTED `{ok:false, error:"nightwatch_cannot_spoof"}`** BEFORE any other check; the
    board is unchanged. A legit Nightwatch write (`by=<host>/nightwatch:Nightwatch`) posts fine. 🔴 The server must derive
    the caller from `NIGHTWATCH_TOKEN`, NEVER trust body `by`/`actor` — a Nightwatch-authed caller that lands ANY
    write as `by:"CEO"` = FAIL (forgeable-identity bypass).
41. **Nightwatch can NEVER mark done — CEO-only, bound to the authenticated caller (§8.5.3 #1, CEO
    2026-06-21 + knightwatch).** With a task on the board, EACH of these from an **authenticated Nightwatch
    caller** (`NIGHTWATCH_TOKEN`) returns **`{ok:false, error:"nightwatch_cannot_done"}`** with `state` **unchanged**:
    `POST /todo/status {state:"done"}`; `POST /todo/update {op:set,state:"done"}`; `set{done:true}`;
    `set{workToDone:true}`. 🔴 **SPOOF CANNOT BYPASS:** the same calls from the Nightwatch caller but with
    body `by:"CEO"`/`actor:"CEO"` do NOT succeed — they return `nightwatch_cannot_spoof` (the done block is
    keyed off the authenticated identity, not the body). The same calls from the **CEO** (authed
    WITHOUT `NIGHTWATCH_TOKEN`) succeed (proving it is Nightwatch-specific, not a global lock). Any Nightwatch done-transition
    that lands — directly or by claiming `by:"CEO"` — = FAIL.
42. **Nightwatch create-task is gated on a one-shot CEO token, minted ONLY by AUTHENTICATED inbound and
    delivered VIA THE QUEUE (§8.5.3 #2 / §8.5.4, CEO 2026-06-21 + knightwatch).** (a) `POST
    /todo/update {op:add}` from an **authenticated Nightwatch caller** (`NIGHTWATCH_TOKEN`) with NO token →
    **`{ok:false, error:"nightwatch_cannot_create"}`**, board unchanged; the SAME `add` claiming body
    `by:"CEO"` to dodge the gate → **`nightwatch_cannot_spoof`** (no task created). (b) Feed an **authenticated** inbound CEO
    WhatsApp "Nightwatch, create <X>" to `POST /nightwatch/inbound` (header `X-Queue-Secret`,
    `{from:<CEO_WHATSAPP>, text}`) → a one-shot token is minted. 🔴 **The token must arrive in the
    Nightwatch QUEUE EVENT (the `[nightwatch] … token=<minted>` that the server `mp send`s to `NIGHTWATCH_AGENT`), NOT be
    read from the webhook RESPONSE** (the response goes to Hermes, not the Nightwatch). Assert the queued
    `[nightwatch]` event carries the token; the Nightwatch's `add` presenting **that queued token** as `{op:"add",
    text, actor:<Nightwatch>, token:<minted>}` **succeeds** (task created). A token present only in the
    webhook response but absent from the queue event = FAIL (the real Nightwatch would never get it). (c)
    🔴 **SPOOF REJECTED:** the SAME POST WITHOUT `X-Queue-Secret` (an unauthenticated caller claiming
    `from=<CEO_WHATSAPP>`) → **401, mints NOTHING**; a subsequent Nightwatch `add` still returns
    `nightwatch_cannot_create`. (d) **Reuse fails:** a minted token on a second `add` → rejected (burned).
    (e) **Expiry fails:** mint a token, advance past its TTL (drive a short expiry), then `add` with
    it → **`nightwatch_cannot_create`**. (f) A token for a NON-CEO `from` (even authenticated) is never
    minted. An ungated Nightwatch create, a token only in the webhook response, a reusable/expired token, or
    a token minted from an unauthenticated/spoofed inbound = FAIL.
43. **Event fanout reaches the Nightwatch queue + idle-watchdog, with the right EXEMPTIONS (§8.5.4, CEO
    2026-06-21).** The §6 board→Boss ping is unchanged AND additionally: a non-test `add`, a
    work-state change, and **every** `/todo/comment` enqueue an event to the **Nightwatch queue** (assert
    via the `[nightwatch] …` delivery into `mc-nightwatch:Nightwatch`, or the queue sink). AND the **idle-watchdog**: a
    task with no CEO/Boss action for `NIGHTWATCH_IDLE_MIN` fires exactly one Nightwatch-queue event (drive with a
    small test window). 🔴 **NEGATIVE (must NOT fan out): (i)** a comment whose `by` is the Nightwatch
    itself (`by=<NIGHTWATCH_AGENT>`) produces **NO `[nightwatch]` delivery** (no self-loop); **(ii)** a `{test:true}`
    task add AND a comment on a `{test}` task produce **NO `[nightwatch]` delivery**. The Boss ping (J3/J32)
    MUST still pass unchanged. Boss ping regressed, no Nightwatch fanout on a real event, or a Nightwatch-self /
    `{test}` event that DOES fan out = FAIL.
44. **Hermes bridge = two thin functions, no logic, REUSES existing Hermes + is SECURE (§8.5.4,
    CEO 2026-06-21 + knightwatch).** (a) **INBOUND AUTH FIRST:** an **authenticated** `POST
    /nightwatch/inbound` (header `X-Queue-Secret`, `{from, text}`) enqueues the event to the Nightwatch queue (and
    mints + **enqueues** the token per J42); an **unauthenticated** `POST /nightwatch/inbound` → **401
    BEFORE `from` is read/used** (the server must not branch on `from` for an unauthed request).
    (b) **OUTBOUND ARGV, NO SHELL:** `POST /nightwatch/outbound {text}` invokes the transport via an
    explicit argv list (`subprocess.run([...], shell=False)`); a `text` containing shell
    metacharacters (e.g. `; touch /tmp/pwned`, `$(...)`, backticks) is delivered as **literal data
    and executes NOTHING** — assert the injected command did not run (no `/tmp/pwned`). With `hermes`
    absent it returns **501** (stub) WITHOUT 500/crash. (c) **REUSE, not rebuild + no Nightwatch logic in
    Hermes:** the wiring targets the host's EXISTING Hermes (a `hermes webhook` subscription +
    the bridge send contract) — no new bridge/number/QR; the Hermes side carries only the two
    message-moving hooks (assert it references `/nightwatch/inbound` + the send and contains no
    persona/decision rules). (d) 🔴 **TAILNET, NOT LOCALHOST/LAN (CEO 2026-06-21, Option A):** the
    generated server reaches Hermes via the **`HERMES_SEND_URL`** config (and the inbound webhook
    targets the node's tailnet `:9933`) — assert the generated `todo-server` does **NOT hard-code
    `127.0.0.1`/`localhost`** (nor a `192.168.x` LAN IP) as the Hermes send endpoint; it reads the
    `HERMES_SEND_URL` env (tailnet `100.x` at runtime). A hard-coded localhost/LAN Hermes endpoint,
    a shell-interpolated outbound, an inbound that reads `from` before auth, a newly-built bridge, or
    decision logic in Hermes = FAIL.

> Gates J14–J44 are NON-OPTIONAL (CEO 2026-06): the Verify harness MUST assert every one. A

45. **HOME VIEW-FILTER TOOLBAR (§7.5, CEO 2026-06-23, UI-diff alignment LOCKED).** The served
    `:9933/` renders a view-filter button row **`all` / `hide done` / `only done` / `unread`**. In a
    real browser (or DOM assertion): with cards in mixed states + at least one `unread>0`, clicking
    **`hide done`** removes every `state=done` card from the visible list; **`only done`** shows ONLY
    `state=done` cards; **`unread`** shows ONLY cards with `unread>0`; **`all`** restores the full
    list. The active button is visually marked, each is wired (zero console errors, J31), and pinned
    cards + live poll (§7.2) keep working under the active filter. A missing toolbar, a dead button,
    or a filter that doesn't change the visible set = FAIL.
46. **VISUAL-FIDELITY DETAILS vs the live board (§7.6, CEO 2026-06-23).** On the served `:9933/` and
    an open card, assert all four: (a) a fixed full-viewport **noise/grain overlay** exists (an
    element with an inline-SVG `feTurbulence` background at low opacity) — not a flat background;
    (b) each comment renders an **avatar with initials** (`.av`-style element bearing the author's
    derived initials) beside a bubble whose header carries the author label — i.e. a chat-bubble +
    profile, not a bare text line; (c) the open card shows an **assignee chip** (`@<assignee>` or
    `unassigned`); (d) comments and state events show a **relative "X ago" timestamp** derived from
    `ts`. Any of the four missing = FAIL. (Titles also checked here: the TODO VISIBLE H1 reads
    **"Priorities"** while the browser-tab `<title>` is `MyPeople - Priorities`; the HUD is **"MyPeople - HUD"**.)
47. **ATTACH BUTTON IS CLIENT-REACHABLE — no dead 127.0.0.1 link (§5.2, CEO 2026-06-24).** With a
    live agent in the HUD, fetch `/dashboard` through a PORT-SHIFTED / non-loopback origin (simulate
    the remote client: request the board with `Host: 100.64.0.9:38080`, external port ≠ inner) and
    assert the rendered ATTACH href: (a) contains **ZERO** `127.0.0.1`/`localhost`/`172.17.`/inner-
    bind literals; (b) its **host equals the host the client used** to reach the board (i.e.
    `window.location.hostname` for a same-node agent, or the agent's tailnet `attach_base` host for a
    cross-node agent) — NOT a server-baked loopback; (c) the ttyd port in the href is one ttyd is
    actually **listening on `0.0.0.0`** (bound to all interfaces, reachable off-box), verified by
    asserting ttyd's listen socket is `0.0.0.0:<ttyd>` not `127.0.0.1:<ttyd>`. BOTH install shapes
    must pass: a LOCAL install (client at localhost → attach opens localhost, works) and a CONTAINER
    reached REMOTELY (client at the tailnet host → attach opens `<tailnet>:<ttyd>`, reaches the
    container). A hardcoded `127.0.0.1` attach href, a loopback-only ttyd bind, or an attach host that
    differs from the client's reach host = FAIL.
    🔴 **ALL 3 LAYERS asserted (Phase-3 consensus, CEO 2026-06-24 — fixing only the page is a false
    pass):** (LAYER a) `GET /clients` (or the heartbeat record) shows this node's `attach_base` =
    `http://<tailnet-100.x>:<ttyd>` derived from `tailscale ip -4` — **NOT `127.0.0.1`, NOT empty when
    a tailnet IP exists, NOT gated on `TTYD_PUBLIC_URL`** (assert it's correct with `TTYD_PUBLIC_URL`
    UNSET); (LAYER b) `GET /agents` returns each live agent with `attach_base` joined + a non-empty
    `attach_url = "<attach_base>/?arg=-t&arg=<target>"` carrying ZERO `127.0.0.1` literals; (LAYER c)
    the served `dashboard.html` ATTACH href contains zero `127.0.0.1` and resolves to the client host
    (above). Any single layer emitting/falling back to `127.0.0.1` = FAIL, even if another layer is
    correct (the bug was the COLLAPSE of all three).
48. **BOSS ACTS ON MESSAGE #1 WITH ZERO RAMP-UP (§8 doctrine quickstart, CEO 2026-06-24).** Two
    checks: (STATIC) the generated `boss-CLAUDE.md` contains the front-loaded operational quickstart —
    the `mp` cheat-sheet (`send`/`peek`/`spawn`/`answer`/`revive` with syntax), the message-flow
    (CEO board comment → server `mp send` ping → Boss; read full context via `GET /todo/board`), and
    the **reply pattern** (`POST /todo/comment {task_id,body,by}` to answer the human; `mp spawn`+`mp
    send` to delegate). Missing any of these = FAIL. (BEHAVIORAL) post a CEO-style **question** comment
    to a non-test card (e.g. "Boss, what's your status?"); within the comms window the Boss responds
    **on its FIRST turn** by posting a `/todo/comment` to that card under its own agent_id (a direct
    answer) OR spawning an engineer — with NO turn spent discovering how to send. A Boss that asks how
    to use the queue, or whose first turn produces no board action, = FAIL.
49. 🔴 **EXTERNAL-BROWSER USER-JOURNEY SUITE — MANDATORY, NON-SKIPPABLE (CEO 2026-06-26: hydrates
    keep shipping bugs because we tested "process up + HTTP 200 + bytes", not the journeys the human
    actually does in his browser).** This gate drives a **real browser** (Playwright/Puppeteer) through
    the ACTUAL user journeys and asserts the rendered DOM + real navigation — NOT href strings.
    🔴 **MUST run in WEBKIT (Safari's engine) — not only Chromium (CEO 2026-06-28: a card-open scroll bug
    was GREEN in Chromium but BROKEN in Safari, because WebKit drops a synchronous scroll on a
    just-shown modal; Chromium-only testing hid it from the CEO who uses Safari). Run the journeys in
    `webkit` (at minimum; chromium too is fine).** 🔴 **And exercise the REAL interaction: OPEN a card by
    CLICKING its row (`page.click('li.task … .task-text')`), NOT by calling `openModal()`/deep-linking —
    a synthetic open takes a different code path and masks click-open bugs.** **It is NOT optional:** the
    Verify harness MUST self-install the browser (`npx playwright install webkit chromium`); if it
    cannot, the gate **FAILS** — it may NEVER
    be skipped or downgraded to a curl/grep substitute and still report green (that skippability is the
    exact hole that let the HUD→TODO 404 and the missing SPAWN CMD column ship). Run **every** journey on
    **BOTH** the HUD port AND the TODO port (per J6b), with **zero console errors / failed network
    requests** throughout (a console error or 4xx/5xx on any step = FAIL).
    🔴 **RECORD VIDEO of every run (CEO 2026-06-27, Rule 22 — proof must be WATCHABLE, not a pass/fail
    number).** The harness MUST create its browser context with **Playwright `recordVideo`** (e.g.
    `newContext({recordVideo:{dir:"verify/videos"}})`) so each journey produces a real captured video of
    the browser actually loading the page, rendering the element, clicking, and navigating. Save the
    videos under `verify/videos/` (convert to `.mp4` for sharing if `ffmpeg` is present) and print their
    paths. The pass/fail count is NOT the proof — the **video is** the proof; a verified requirement
    without a recorded video is not proven. (These videos are what get attached to the card per Rule 22.)
    The journeys:
    - **a. Open TODO board:** load `/` → the board renders (the "Priorities" H1, the addbar, the chips).
    - **b. Add a card:** type in the addbar + press Enter → the new card APPEARS in the list (assert the
      DOM node, not just the API).
    - **c. One-click DONE:** click the card's `.check` → it flips to done (struck/green ✓) and the
      board state persists on reload; click again → un-done. (No dropdown needed; the ★ star is pin-only.)
    - **d. Card modal + comment:** click the card → modal opens; post a comment → it appears in the
      thread live (no manual refresh); Esc/backdrop closes it.
    - **e. Cross-nav by REAL CLICK:** on the board, **click `HUD ↗`** → the browser lands on the HUD
      (`MyPeople - HUD`, agents table visible); on the HUD, **click `TODO ↗`** → lands on the board
      (`Priorities`). Assert the resulting page content after the click — do this **from BOTH ports**
      (open the HUD on its own port too and click TODO↗ → must reach the board, not a 404).
    - **f. SPAWN CMD visible:** spawn a real engineer; the HUD agents table shows that engineer's row
      with its **spawn command** (the SPAWN CMD cell, non-empty) AND its `mp revive <agent_id>`.
    - **g. Proof renders:** a card with an image/video proof (posted via API) shows the real
      `<img>`/`<video>` chip in the modal (no broken/text chip); there is NO proof-attach control in the UI.
    - **h. Assignee is a clickable link to the engineer's tab (CEO 2026-06-27):** add a card, assign it
      to a REAL agent on this node (e.g. the Boss from `/agents`); on the board, assert its assignee
      renders as an **anchor `a.asg-link`** (tagName `A`, not a plain `<span>`), and **clicking it opens
      the engineer's tab** — a popup/navigation to the attach URL (`…/?arg=-t&arg=<tmux target>`).
      FAIL if the assignee is plain text or the click does not navigate to that engineer's terminal.
    - **i. Attachments render INLINE in chat order + read-region size (CEO 2026-06-28, §7.7b):** open a
      card that has an image attachment posted BETWEEN two comments (and a text attachment); assert
      (a) the top proof region holds **0** hoisted attachments, (b) the image renders **inside the
      thread** and **actually loaded** (`naturalWidth>0` — proves the proof URL serves, incl. the legacy
      `/todo/proof/<tid>/<file>` route), (c) DOM order is comment1 < image < comment2 (inline at post
      point), (d) the text attachment is inline too, (e) the chat read-region height ≥ ~360px. FAIL if
      attachments are hoisted to the top, an image 404s/doesn't load, order is wrong, or the read area is small.
    - **j. Card-chat respects user-controlled scroll (CEO 2026-06-28, §7.4):** open a card with a long
      (scrollable) thread; assert (a) at bottom + a poll/re-render → STILL at bottom; (b) scrolled to a
      mid offset + an unchanged poll AND a content-changing re-render (a new comment posted) → the scroll
      offset is UNCHANGED (no jump-back, within a few px); (c) at bottom + a new comment arrives → sticks
      to bottom showing the new comment. FAIL if any poll/re-render force-jumps the user's scroll.
    - **k. Card view: open-at-newest + scroll-scoping + readability (CEO 2026-06-28, §7.4):** open a card
      with many comments (and an inline image); assert (a) the chat is scrolled to the BOTTOM and the
      LAST comment is in the viewport on open (even after the image loads — re-pin); (b) wheeling inside
      the thread at its top/bottom boundary leaves `document.scrollingElement.scrollTop` UNCHANGED and
      `body` is `overflow:hidden` (scroll stays in the card); (c) the message `.ev-text` computed
      `font-size ≈ 14.5px` and `line-height/font-size ≈ 1.55` (OLD-design readability). FAIL on open-not-
      at-bottom, page-scroll bleed, or cramped text.
    - **L. ALL comments render + live append + no-cache (CEO 2026-06-28 P0 regression):** load a card with
      N comments AND attachments; assert (a) the page response sends `Cache-Control: no-cache` (no stale
      JS), (b) ALL N items render in the thread (a single throwing comment/attachment must NOT blank the
      list — render defensively), (c) ZERO console/page errors, (d) post a comment via the composer UI →
      it APPEARS without a manual reload. FAIL if the list is blanked/truncated, any console error, the
      page is cacheable, or a posted comment doesn't show.
    - **M. Heavy-card open-at-newest (after settle) + filter persistence (CEO 2026-06-28 P0):** (1) open a
      HEAVY card (20+ comments WITH inline image/video proofs); after the images load + a settle delay,
      assert the FINAL resting scroll position is at the BOTTOM and the newest item is in view (a
      pre-settle check is not enough). (2) apply a state-chip + view filter, RELOAD the page, assert the
      same filters are still active (chip/vbtn highlighted) AND the list is still filtered. FAIL if a
      heavy card settles mid-list or filters reset on reload.
    - **N. Comment thread: human-readable timestamps + real submit renders (CEO 2026-06-29, WebKit):**
      render comments whose `ts` are stored as BOTH seconds-epoch AND ms-epoch; assert (a) every shown
      timestamp is a proper relative string ("just now"/"Nm ago"/"Nh ago"/"Nd ago") — NO raw unix number
      or negative anywhere (check the timestamp segment, not the agent name); a 5-min-old ts in EITHER
      unit shows "5m ago"; (b) TYPE a comment in the composer and SUBMIT it (real interaction) → it
      appears in the thread AND is visible in the viewport without a reload (the new comment sorts to the
      true bottom and scrolls into view). FAIL on any raw/negative timestamp or a submitted comment that
      doesn't show/scroll into view. (Run in webkit + chromium.)
    Any dead control, console error, failed click-through, 404 on a clicked link, or missing rendered
    element = FAIL. **A hydrate is only "ready" (and the agent may only tell the CEO to use it) after
    THIS suite passes via the real browser** — supersedes the weaker self-graded J31 (which becomes the
    smoke-subset of this). (F-browser-journeys)
> Gates J14–J38 are NON-OPTIONAL (CEO 2026-06): the Verify harness MUST assert every one. A
> green run with any F-feature unexercised — OR that leaves ANY test fixture / placeholder host on
> the live grid, runs default tmux, shows ANY animation, leaks the secret to the browser, fails the
> joke-protocol E2E loop, needs a manual refresh, steals focus/caret on poll, **or hangs on a
> folder-trust/onboarding dialog in any cwd** — is a FALSE GREEN, and the harness fails.

---

## 16. Failure modes (host quirks — guidance, not code)

- **add-task never reaches the Boss** → `mp` not on todo-server's PATH (§5.1); the server's
  `shutil.which("mp")` was None and `boss_ping` silently skipped. Launch with PATH set.
- **HUD attach link dead from the human's machine** → `attach_base` is a docker/LAN IP (§5.2);
  fix the tailnet-IP resolution (+ §5.6 socket symlink).
- **No Boss in the HUD after a green Verify** → cleanup killed the master Boss, or no supervisor
  (§5.3). Verify must leave the Boss alive and assert it (J2/J4).
- **Spawned agent hangs / `mp spawn` blocks** → first-run onboarding dialog (§5.5).
- **`tailscale ip -4` empty on a no-systemd node** → missing default-socket symlink (§5.6).
- **ttyd "not running" false-fail** → grepped `disableLeaveAlert=true`; ttyd rewrote argv —
  verify functionally (HTTP 200) and by bare option name (§5.7).
- **Self-install kills its own driver** → a daemon stop was pre-emptive; do graceful in-place
  handoff right before relaunch (§5.8).

---

## 17. Convergence notes (read before building)

- **You write every component in one pass → they interoperate by construction.** The §4 protocol
  pins only what must be exact (agent_id↔tmux, the gated endpoints, heartbeat `attach_base`).
- **The contracts in §5 are non-negotiable** — each is a real bug that bit a prior build. The
  fastest path to a clean one-shot is to satisfy all of §5 up front, not rediscover them.
- **Verify against §-values, never a reference app** (§14). A blind agent on a clean node with no
  other mypeople anywhere must reach exit 0.
- **Stay in scope (§9).** Stub the out-of-scope surfaces; don't let them block a gate.

---

## A. UI + tmux — GENERATED from spec (truly generative; Decision B 2026-06-17)

**No pinned/pasted components, no sha256 checksum.** You GENERATE `bin/todos.html`,
`bin/dashboard.html`, and `~/.tmux.conf` yourself from the **design tokens/consts** (small spec
values — fine and wanted, so the result LOOKS PLOW) plus the natural-language layout (§7) and the
behavioral feature contracts (§A.2). The **≤10% code budget = tokens/consts only, never pasted
components.** Pixel-exactness to any prior app is NOT required; faithful PLOW look + every gated
behavior IS (verified behaviorally, §A.2 + §15 — there is no byte-identity gate).

**Browser auth — SERVER-SIDE ONLY, never embed the secret (§5.12).** The page makes **same-origin**
calls and contains **NO secret**. Serving the page mints a browser session — set an **httpOnly**
cookie (a random session token, NOT the QUEUE_SECRET) that the browser auto-sends on same-origin
fetches; the server accepts that cookie OR an `X-Queue-Secret` header (machines) on gated endpoints.
The QUEUE_SECRET lives only on the server. **Do NOT inject the secret into the HTML/JS** (the old
`__INJECT_SECRET__` approach was a security bug — secret visible to anyone who loads the page).

**§A.1 `~/.tmux.conf` — GENERATE it to apply the CEO's style (his settings are the consts below;
this is NOT a pasted file and there is NO checksum — J28 gates the RUNNING server, not bytes).**
Apply exactly these settings (the tmux tension is resolved by shipping his *settings as consts* and
generating the file + gating behavior):
- **Dracula via TPM:** `@plugin 'tmux-plugins/tpm'`, `@plugin 'dracula/tmux'`; `@dracula-plugins
  "cpu-usage ram-usage time"`, `@dracula-show-powerline false`, `@dracula-show-left-icon session`,
  `@dracula-military-time true`, `@dracula-day-month false`, `@dracula-show-timezone false`.
- **General:** `default-terminal "tmux-256color"` + `terminal-overrides ",xterm-256color:Tc"`,
  `mouse on`, **`base-index 1`**, `pane-base-index 1`, **`renumber-windows on`**,
  **`history-limit 50000`**, **`escape-time 10`**.
- **Hard-won TUI fixes (REQUIRED):** **UNBIND `WheelUpPane`/`WheelDownPane` at `-T root`** (the Claude
  TUI renders on the main screen and tmux's default wheel→`copy-mode -e` silently traps every
  keystroke); and bind `MouseDragEnd1Pane` to **`copy-pipe-and-cancel`** (NOT plain `copy-pipe` — without
  `-and-cancel` the pane stays stuck in copy-mode). `pbcopy` is macOS-only → a no-op in the container,
  harmless; keep the structural fix.
- End with `run '~/.tmux/plugins/tpm/tpm'`.

**THEN — the file alone is NOT enough; the RUNNING server must load it (this was a FALSE GREEN):** a
prior node had the right conf on disk yet the live server ran defaults (`WheelUpPane` bound,
`base-index 0`, no Dracula) because the server started before the conf and TPM/Dracula were never
installed. So: (1) `git clone tpm` + `~/.tmux/plugins/tpm/bin/install_plugins` (clones Dracula); (2)
`tmux source-file ~/.tmux.conf` on any running server (or place the conf before any server starts);
(3) every `mc-*` Boss/agent session must run in a server that loaded it. **J28 verifies the LIVE
server, not the file.**
> **NEVER `tmux kill-server` (and never kill sessions you didn't create) to apply/verify the conf
> — folded 2026-06-18 (CEO).** `kill-server` destroys EVERY session on that server, including
> **detached flows like an in-progress `claude auth login`** (a pending device login lives in its own
> tmux session) — killing it aborts the login and forces a fresh URL. Apply the config with
> **`tmux source-file ~/.tmux.conf` ONLY** (it applies global options + key-binds to the already-
> running server WITHOUT touching sessions), or write the conf before the FIRST server starts. **J28
> verification is READ-ONLY** — `tmux show-options -g` / `tmux list-keys` only; it must never restart
> or kill the server. (Root cause of the J28-killed-the-login bug: a kill-server/restart to "reload"
> the conf wiped the detached `auth` login session mid-flight.)

**§A.2 UI feature contracts — GENERATED `todos.html` + `dashboard.html`, every feature MANDATORY +
behaviorally gated.** You generate both pages (from §7 tokens + the layout) and the servers; each row
is a behavioral contract with a Verify gate (J-id) that exercises it. NO feature is optional; the
blind agent generates all of it and may skip nothing. (No byte-identity check — a faithful PLOW UI
that passes every gate is correct, per Decision B.)

**TODO (generated `todos.html` + `todo-server.py`, `:9933`) — every gate:**

| # | Feature (the generated UI must do this) | Contract | Gate |
|---|---|---|---|
| F1 | add task (Enter) | `update{op:add,text}` → task born **`needs_brainstorm`**, prepended to `order` | J3 |
| F2 | delete task | `update{op:del,id}` removes from tasks+order | J15 |
| F3 | edit text / done-condition / assignee inline | `update{op:set,id,text\|doneCondition\|assignee}` patches the field (REAL names, §6) | J16 |
| F4 | state change | `update{op:set,id,state}` (field **`state`**) or `/todo/status`; enum `needs_brainstorm\|working\|review\|done` + `blocked\|cancelled` (NO `idle`; `review` displays "review (CEO)") | J17 |
| F5 | done checkbox / work-to-done toggle | `set{done}`/`set{workToDone}` flips `state`→`done`, renders strikethrough | J18 |
| F6 | **needs_brainstorm → working, NO gate** (boss-doctrine, REVERTED 2026-06-26) | new task is `needs_brainstorm`; an engineer `set{state:working}` with no brainstorm/answer gate | J19 |
| ~~F7~~ | ~~brainstorm/answer gate~~ **REMOVED (CEO 2026-06-18)** | no `/todo/brainstorm`, no `/todo/answer`, no `brainstorm` field, no needs-brainstorm banner | J20 (negative) |
| F8 | comment thread (card modal) | `/todo/comment{task_id,by,body}` appends to `comments[]`, `by`=agent_id\|CEO | J5 |
| F9 | unread badge | **server-side rule (folded 2026-06-18 — was underspecified → first impl left `unread` null):** each task has an integer `unread` (default **0**); the server **increments it on every `/todo/comment` whose `by` is NOT the CEO/reader**, and `/todo/board` returns it. UI reads localStorage READ_KEY for the read-state. | J21 |
| F10 | proofs (image/video/link/text + more) | `/todo/proof{task_id,kind,url\|body}` appends to `proofs[]`; board returns them | J22 |
| F11 | assignee chip → attach to that engineer's terminal | `/todo/attach?agent=` resolves `{target,base}` (live) | J7 |
| F12 | ITEM 3 — clickable commenter agent name → terminal | same resolver; non-agent (`CEO`) authors are plain text | J7 |
| ~~F13~~ | ~~dependencies/subtasks/hard-gate~~ **REMOVED (CEO 2026-06-17)** | backend must NOT implement `add{parent}`/`dependsOn`/`hardGate`; generated UI has no such controls | J23 (negative) |
| F14 | board→Boss ping on **add AND every comment** | `mp send <BOSS_AGENT>` on non-test add + on each `/todo/comment` (exempt only the Boss's own); logged to `boss-inbox.log` (§6) | J3 + J32 |
| F15 | ITEM 2 — cross-nav HUD ↗ | static link to `:9900/dashboard` (built from `location.hostname`) | J6 |
| F16 | verified badge | board returns `verified`; UI shows the "verified" badge | J24 |
| F23 | LIVE hot-reload (no manual refresh) | page polls ≤~3s (or SSE); new tasks/comments/state appear without F5 (§7.2) | J33 |
| F24 | full E2E comms loop (joke protocol) | task→Boss→spawn engineer→engineer comments one-per-turn→CEO comment pings Boss→next round (§8) | J32 |
| F25 | poll preserves FOCUS + caret (folded 2026-06-18) | the incremental update never re-renders/replaces the focused input nor resets caret/selection; typing in the add box is uninterrupted by the 1s poll (§7.2) | J34 |

**HUD backend (`queue-server.py`, `/dashboard`) — every gate:**

| # | Feature | Backend contract | Gate |
|---|---|---|---|
| ~~F17~~ | ~~machines grid grouped by purpose + count~~ **REMOVED (CEO 2026-06-18)** | the generated HUD has NO machines/hydration grid (§7.1 removed); J11 dropped | — |
| ~~F18~~ | ~~per-node state/alive in grid~~ **REMOVED** | (the `/clients` `state`/`last_seen` fields still exist for the OUTER uplink, just not rendered in the product HUD) | — |
| ~~F19~~ | ~~per-node type/machine/attach/recording card~~ **REMOVED** | — | — |
| F20 | agents table (id/state/backend/boss/summary/attach) | `/agents` carries those fields + `tmux_target` | J8 |
| F21 | retired engineers + Revive button | `/roster` carries `retired`; `POST /revive{agent_id}` works | J25 |
| F22 | ITEM 2 — cross-nav TODO ↗ + live/stale pill + agent count | static link to `:9933`; count from `/agents` | J6 |
| F23 | PIN tasks (WhatsApp-starred, §7.3) — ★ pin/unpin, pinned float to top in pin order, MAX 5, unpin restores, persists | `update{op:'pin'\|'unpin',id}`; `pinned`+`pinRank` in `board.v2.json`; board renders pinned-first by `pinRank` | J37 |
| F24 | jump-to-latest in comment thread (§7.4) — floating ↓ button, appears when scrolled up, smooth-scrolls to newest, hides at bottom | client-only: thread `scroll` handler toggles the button; click → `scrollTo({top:scrollHeight,behavior:'smooth'})` | J38 |

**§A.3 How the UI is verified now (Decision B — behavioral, NOT checksum).** There is **no
byte-identity / sha256 gate**. The generated UI is correct when: it carries the PLOW tokens (J9 —
Volt `#D5EF8A` + Instrument Serif/DM Sans/DM Mono), has **no animations** (J29), omits the cut
features (J23: no subtasks/deps/hard-gate, no reorder), and **passes every F-gate above** (the
behaviors). A faithful PLOW look that clears all gates IS the product — pixel-exactness is not
required. tmux fidelity is gated on the RUNNING server (J28).