#!/usr/bin/env python3
"""Clean-container board Verify gates for card 33de4ec059 (§15 25b/25c, SQLite store).
Exit 0 iff every gate passes. Self-contained; uses the reference boardstore + the
patched exporter/restore, driven against a sandbox store so nothing live is touched."""
import os, sys, json, time, hashlib, subprocess, tempfile, threading, sqlite3, glob

BIN = "/opt/mp-node/bin"
sys.path.insert(0, BIN)
import boardstore as BS

FAILS = []
def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + (" :: " + detail if detail else ""))
    if not cond:
        FAILS.append(name)

def big_board(n, extra=None):
    b = BS.default_board()
    for i in range(n):
        tid = "t%03d" % i
        b["order"].append(tid)
        b["tasks"][tid] = {"id": tid, "text": "task %d" % i, "state": "todo",
                           "assignee": "", "comments": [], "proofs": [],
                           "created": 1.0 * i, "updated": 1.0 * i}
    if extra:
        b.update(extra)
    return b

def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None


print("== Gate 25b: store engine + atomicity + shrink guard + lossless round-trip ==")
work = tempfile.mkdtemp(prefix="gate25b_")
jsonp = os.path.join(work, "board.v2.json")
db = BS.db_path_for(jsonp)

# 25b(1) store path resolves to .sqlite3 (engine = sqlite)
check("25b.1 board store path is board.v2.sqlite3", db.endswith("board.v2.sqlite3"), db)

# selector resolves sqlite from queue.env (BOARD_BACKEND) with no env override
os.environ.pop("MYPEOPLE_BOARD_BACKEND", None)
sys.path.insert(0, BIN)
import mpcommon as C
sel = (os.environ.get("MYPEOPLE_BOARD_BACKEND") or C.CFG.get("BOARD_BACKEND") or "json").strip().lower()
check("25b.sel selector resolves 'sqlite' from queue.env", sel == "sqlite", "resolved=%s" % sel)

# 25b(2) single-transaction atomicity + integrity under concurrency
BS.save_board(db, big_board(40))
errors = []
def hammer(k):
    try:
        for j in range(15):
            b = BS.load_board(db)
            b["tasks"]["t000"]["comments"].append({"id": "%d-%d" % (k, j), "by": "w%d" % k,
                                                    "kind": "comment", "body": "x", "ts": time.time()})
            b["tasks"]["t000"]["updated"] = time.time()
            BS.save_board(db, b)
    except Exception as e:
        errors.append(repr(e))
ths = [threading.Thread(target=hammer, args=(k,)) for k in range(8)]
[t.start() for t in ths]; [t.join() for t in ths]
integ = sqlite3.connect(db).execute("PRAGMA integrity_check").fetchone()[0]
jmode = sqlite3.connect(db).execute("PRAGMA journal_mode").fetchone()[0]
check("25b.2 concurrent writes: no errors", not errors, ";".join(errors))
check("25b.2 integrity_check = ok", integ == "ok", integ)
check("25b.2 journal_mode = wal", jmode.lower() == "wal", jmode)

# 25b(3) catastrophic-shrink guard: >50% drop refused, DB unchanged, SUSPECT written
before = BS.load_board(db); n_before = len(before["tasks"])
db_sha_before = sha(db)
ok = BS.save_board(db, big_board(3))   # 3 << 50% of 40 -> must refuse
after = BS.load_board(db); n_after = len(after["tasks"])
suspects = glob.glob(db + ".SUSPECT.*")
check("25b.3 shrink refused (save returns False)", ok is False, "ret=%s" % ok)
check("25b.3 board unchanged after refused shrink", n_after == n_before, "%d->%d" % (n_before, n_after))
check("25b.3 SUSPECT dump written", len(suspects) >= 1, str(suspects))

# 25b(4) migrator round-trip lossless (JSON <-> SQLite)
src = big_board(37, {"pinSeq": 9})
json.dump(src, open(jsonp, "w"))
r = subprocess.run([sys.executable, os.path.join(BIN, "migrate_board.py"), "verify", jsonp],
                   capture_output=True, text=True)
check("25b.4 migrator verify LOSSLESS", "LOSSLESS    : True" in r.stdout, r.stdout.strip().splitlines()[-1] if r.stdout else r.stderr)


print("== Gate 25c: git-tracked export (from DB) + restore (to DB) ==")
work2 = tempfile.mkdtemp(prefix="gate25c_")
todos = os.path.join(work2, "todos"); os.makedirs(todos)
jsonp2 = os.path.join(todos, "board.v2.json")
db2 = BS.db_path_for(jsonp2)
export_repo = os.path.join(work2, "export-repo")
src2 = big_board(30, {"pinSeq": 4})
BS.save_board(db2, src2, shrink_guard=False)

cfg = os.path.join(work2, "queue.env")
open(cfg, "w").write("\n".join([
    'export INSTALL_DIR="%s"' % work2, 'export HOST_ID="gatehost"', 'export HUD_PORT="9900"',
    'export TODO_PORT="9933"', 'export TTYD_PORT="7681"', 'export QUEUE_SECRET="test-secret"',
    'export BOARD_BACKEND="sqlite"', 'export EXPORT_REPO="%s"' % export_repo,
    'export MYPEOPLE_SUPPRESS_BOSS_NOTIFY="1"']) + "\n")
env = dict(os.environ)
env.update(MYPEOPLE_CONFIG_PATH=cfg, MYPEOPLE_BOARD_BACKEND="sqlite", BOARD_PATH=jsonp2)

def export_once():
    return subprocess.run([sys.executable, os.path.join(BIN, "board-exporter.py"), "--once"],
                          capture_output=True, text=True, env=env)

# 25c(1) change -> commit containing the change; exporter reads FROM the DB
export_once()
head = subprocess.run(["git", "-C", export_repo, "show", "HEAD:board.v2.json"],
                      capture_output=True, text=True).stdout
committed = json.loads(head) if head.strip() else {}
check("25c.1 export commit reconstructs board FROM sqlite", len(committed.get("tasks", {})) == 30,
      "tasks=%d" % len(committed.get("tasks", {})))
# add a card, re-export, assert new commit contains it
b = BS.load_board(db2); b["tasks"]["NEW"] = {"id": "NEW", "text": "added", "state": "todo",
    "assignee": "", "comments": [], "proofs": [], "created": 99.0, "updated": 99.0}; b["order"].append("NEW")
db2_sha_before = sha(db2)
BS.save_board(db2, b, shrink_guard=False)
export_once()
head2 = subprocess.run(["git", "-C", export_repo, "show", "HEAD:board.v2.json"],
                       capture_output=True, text=True).stdout
check("25c.1 new change lands in a fresh commit", "NEW" in head2)

# 25c(2) read-only: exporter never writes the live DB
db2_sha_after = sha(db2)
export_once()  # another pass
check("25c.2 exporter is read-only (DB sha unchanged across an export)", sha(db2) == db2_sha_after)

# 25c(3) wipe auto-quarantined: <50% snapshot -> SUSPECT, HEAD stays at last good
good_head_tasks = len(json.loads(subprocess.run(["git", "-C", export_repo, "show", "HEAD:board.v2.json"],
                      capture_output=True, text=True).stdout).get("tasks", {}))
wiped = big_board(2); BS.save_board(db2, wiped, shrink_guard=False)  # force a wipe in the store
export_once()
head_after_wipe = json.loads(subprocess.run(["git", "-C", export_repo, "show", "HEAD:board.v2.json"],
                     capture_output=True, text=True).stdout).get("tasks", {})
suspects2 = glob.glob(os.path.join(export_repo, "*SUSPECT*"))
check("25c.3 wipe NOT promoted to HEAD", len(head_after_wipe) == good_head_tasks,
      "head=%d good=%d" % (len(head_after_wipe), good_head_tasks))
check("25c.3 wipe quarantined as SUSPECT", len(suspects2) >= 1, str(suspects2))

# restore the store to the good HEAD (so restore test starts from a real state)
# 25c(4) restore-to-CURRENT: board-restore writes HEAD back INTO the sqlite store + prerestore dump
r = subprocess.run([os.path.join(BIN, "board-restore")], capture_output=True, text=True, env=env)
restored = BS.load_board(db2)
prerestore = glob.glob(jsonp2 + ".bak.prerestore.*")
check("25c.4 restore writes HEAD back into the sqlite store", len(restored["tasks"]) == good_head_tasks,
      "restored=%d expected=%d :: %s" % (len(restored["tasks"]), good_head_tasks, r.stdout.strip()))
check("25c.4 restore wrote a reversible prerestore dump first", len(prerestore) >= 1, str(prerestore))

print()
if FAILS:
    print("BOARD GATES: FAIL (%d) -> %s" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("BOARD GATES: PASS (25b + 25c, SQLite store)")
sys.exit(0)
