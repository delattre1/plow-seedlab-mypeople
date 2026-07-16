# Boss spawn-backend buttons fixed (card f6339b85a2)

Root cause: the board's "Spawn Claude/Codex/Grok" buttons submit a queue task
`type=spawn {backend, is_master:true}`. queue-client.dispatch() built
`mp spawn <Boss> --backend X --master` WITHOUT `--role boss`, so do_spawn's
fail-closed guard ("refusing a bundle-less Boss", mp:487) returned rc=2 for
EVERY dashboard Boss-spawn. Only Revive worked (it rebuilds role from roster).

Fix (2 files):
- bin/queue-client.py: default `--role boss` for any master spawn (forward an
  explicit payload role if present).
- bin/todo-server.py: include `role:"boss"` in the _boss_op spawn payload.

Verified live: kill grok Boss via /todo/boss, spawn claude via /todo/boss ->
Boss came up as authenticated Claude (role boss), one master, no flap.
