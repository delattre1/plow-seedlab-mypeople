# SQLite board reference implementation — card `33de4ec059`

The **proven reference implementation** behind the new §3 board-persistence prose: the TODO board is
persisted in **SQLite** instead of `board.v2.json` (CEO 2026-07-14: *"THIS IS BOARD ONLY. The JSON,
instead of being JSON, I want it to be SQLite."*). Same §6 REST API, same behavior, storage engine
swapped underneath. **BOARD store only** — the queue/roster/status stores are untouched.

`mypeople` is a **generative seed** — the canonical artifact is the spec, and the runtime is generated
from it. These files are the concrete, proven code behind the new spec prose, kept here for review and
for the live cutover. They are NOT part of the generative install.

## Files

| file | what it is |
|------|-----------|
| `boardstore.py` | self-contained SQLite engine (stdlib `sqlite3`). WAL, `foreign_keys=ON`, `synchronous=FULL`. Row-per-card: full task object as JSON in `data` (lossless) + mirrored indexed columns; `meta` holds `version`/`pinSeq`/`order`. Per-card content hash → `save` writes only changed rows. `load` returns a FRESH board dict identical to the JSON shape; single-transaction atomic `save` with the catastrophic-shrink refusal. |
| `migrate_board.py` | reversible one-shot migrator: `to-sqlite`, `to-json` (rollback), `verify` (deep-equal round-trip). |
| `todo-server.patch` | unified diff vs the installed `bin/todo-server.py`: adds the backend selector + routes `load_board`/`save_board` through `boardstore` when the SQLite backend is selected. The seam is the only change; every handler is untouched. |
| `board-exporter.patch` | makes the decoupled git-export daemon backend-aware — it reconstructs canonical JSON **from the SQLite store** and commits it (the git snapshot stays JSON = human-diffable + rollback source), polling `board.v2.sqlite3`+`-wal`. |
| `board-restore.patch` | makes restore write the recovered JSON snapshot back **into the SQLite store** (single transaction), after a reversible `*.bak.prerestore.<epoch>.json`. |
| `seed-spec.patch` | the `mypeople.seed.md` diff (§3 store + durability contracts + §15 J-gates 25b/25c + engine-agnostic wording). |
| `proof-transcript.txt` | isolation proofs (migrator lossless, full-API parity, concurrency/restart/integrity, latency, backup/restore, schema) + the **live production cutover** transcript. |

## Backend selector (respawn-safe)

`load_board`/`save_board` route through the engine chosen by
`MYPEOPLE_BOARD_BACKEND` (env) → `BOARD_BACKEND` (`queue.env`, read via `mpcommon.CFG`) → default.

- **This reference impl defaults to `json`** — a deliberate *migration safety gate* so an existing live
  JSON install can never flip silently; the cutover is an explicit, gated act (set `BOARD_BACKEND=sqlite`
  in `queue.env`). `supervise.sh` re-execs each daemon fresh, so the `queue.env` value survives respawns
  with **no supervisor bounce** (scoped to the board daemons only).
- **The seed spec defaults a FRESH generation to `sqlite`** (the CEO directive: SQLite is the board).
  A seed-generated install therefore bakes `BOARD_BACKEND=sqlite` into `queue.env`.

## Verifying

Board acceptance is unchanged and passes on **both** backends:

```sh
python3 tests/test_owner_lifecycle.py \
  --runtime <bin/todo-server.py carrying todo-server.patch> \
  --ui bin/todos.html --culture plans/boss-culture-v6.md
# and the §15 board gates 25b/25c against the SQLite store (see verify/ in the clean-container run)
```

## Clean-container Verify — PASS (exit 0)

`verify/verify.sh` runs the board slice of `## Verify` in a fresh `debian:12` with **nothing
pre-existing** (installs python3+git, assembles a from-scratch `$INSTALL_DIR`, bakes
`BOARD_BACKEND=sqlite` into `queue.env`): **Gate A** = `test_owner_lifecycle.py` on the SQLite store,
**Gate B** = §15 board gates 25b (store path `.sqlite3`, selector resolves sqlite, concurrent-write
`integrity_check=ok`, WAL, shrink refused → SUSPECT, migrator round-trip lossless) + 25c (export
reconstructs JSON **from the DB**, exporter read-only, wipe quarantined, restore writes HEAD back into
the DB with a reversible prerestore dump). Exit code is the truth — see
`verify/clean-container-transcript.txt` (ends `VERIFY_BOARD_OK`, container exit 0).

## Live cutover — DONE 2026-07-14 (CEO "Go!")

The production board was cut over to SQLite and independently verified 6/6 by the Boss (DB active +
`integrity_check` ok, JSON frozen as rollback, `BOARD_BACKEND` persisted, single process, git backup
now sourced from the DB). Rollback remains one flag away: set `queue.env` `BOARD_BACKEND` back to `json`
and restart the two board daemons → they read the untouched `board.v2.json`. See `proof-transcript.txt`.
