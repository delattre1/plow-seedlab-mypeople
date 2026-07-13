#!/usr/bin/env python3
"""Regression: Terminal Graph discovers live tmux windows even when AGENTS is empty/partial."""
import argparse
import importlib.util
import os
from pathlib import Path
import sys
import tempfile


class Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, help="Path to generated todo-server.py")
    args = parser.parse_args()
    runtime = Path(args.runtime).resolve()

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "queue.env"
        cfg.write_text("\n".join([
            "INSTALL_DIR=" + tmp,
            "HOST_ID=testhost",
            "HUD_PORT=19900",
            "TODO_PORT=19933",
            "QUEUE_URL=http://127.0.0.1:19900",
            "QUEUE_SECRET=test-secret",
            "TTYD_PORT=7681",
            "TTYD_BROWSER_PORT=7681",
        ]) + "\n")
        os.environ["MYPEOPLE_CONFIG_PATH"] = str(cfg)
        for key in ("INSTALL_DIR", "HOST_ID", "HUD_PORT", "TODO_PORT", "QUEUE_URL", "QUEUE_SECRET"):
            os.environ.pop(key, None)
        sys.path.insert(0, str(runtime.parent))
        sys.modules.pop("mpcommon", None)
        spec = importlib.util.spec_from_file_location("todo_graph_enumeration_under_test", runtime)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        names = ["Boss"] + ["eng-%d" % number for number in range(1, 15)]
        roster = []
        durable_roster = {}
        for name in names:
            aid = "testhost/main:%s" % name
            row = {
                "agent_id": aid, "host": "testhost", "retired": False,
                "boss_id": "" if name == "Boss" else "testhost/main:Boss",
                "is_master": name == "Boss",
            }
            roster.append(row)
            durable_roster[aid] = dict(row)
        run_dir = Path(tmp) / "run"
        run_dir.mkdir(parents=True)
        (run_dir / "roster.json").write_text(__import__("json").dumps(durable_roster))

        live_lines = ["mc-main\t%s\t0\t160\t48" % name for name in names]
        live_lines += [
            "mc-main\teng-dead\t1\t160\t48",
            "mc-main\ttest-fixture\t0\t160\t48",
            "mc-verify\teng-verify\t0\t160\t48",
            "_vro_Boss_123\tBoss\t0\t160\t48",
        ]
        tmux_calls = []

        def run(argv, **_kwargs):
            tmux_calls.append(argv)
            assert argv[:3] == ["tmux", "list-windows", "-a"], argv
            return Result("\n".join(live_lines) + "\n")

        module.subprocess.run = run
        module.load_board = lambda: {"tasks": {}}
        handler = object.__new__(module.Handler)

        def exercise(agents):
            def http_json(_method, url, _body=None, _headers=None, **_kwargs):
                return (200, agents if url.endswith("/agents") else roster)
            module.C.http_json = http_json
            graph = handler._terminal_graph()
            assert len(graph["nodes"]) == 15, [node["agent_id"] for node in graph["nodes"]]
            assert {node["target"] for node in graph["nodes"]} == {
                "mc-main:%s" % name for name in names
            }
            assert graph["nodes"][0]["agent_id"] == "testhost/main:Boss"
            assert all(node["cols"] == 160 and node["rows"] == 48 for node in graph["nodes"])
            return graph

        exercise([])  # queue-server restarted: AGENTS has not repopulated at all
        partial = [
            {"agent_id": "testhost/main:Boss", "host": "testhost", "state": "alive",
             "status": "working", "tmux_target": "mc-main:Boss", "is_master": True},
            {"agent_id": "testhost/main:eng-1", "host": "testhost", "state": "alive",
             "status": "blocked", "tmux_target": "mc-main:eng-1"},
            {"agent_id": "testhost/main:stale", "host": "testhost", "state": "alive",
             "status": "working", "tmux_target": "mc-main:stale"},
        ]
        graph = exercise(partial)
        assert next(node for node in graph["nodes"] if node["agent_id"].endswith(":eng-1"))["state"] == "blocked"
        assert not any(node["agent_id"].endswith(":stale") for node in graph["nodes"])
        assert len(tmux_calls) == 2 and all(call[1] == "list-windows" for call in tmux_calls)

    print("PASS Terminal Graph enumeration: empty/partial AGENTS still yields all 15 live canonical windows")


if __name__ == "__main__":
    main()
