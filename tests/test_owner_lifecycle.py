#!/usr/bin/env python3
"""Focused regression for the generated MyPeople owner lifecycle runtime."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class FakeQueueHandler(BaseHTTPRequestHandler):
    roster = []

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/roster":
            payload = self.roster
        elif self.path == "/agents":
            payload = [{
                "agent_id": row["agent_id"], "tmux_target": "mc-main:" + row["agent_id"].split(":")[-1],
                "attach_base": "http://example.test:7681",
            } for row in self.roster if row.get("state") == "alive"]
        else:
            self.send_error(404)
            return
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def request(base, path, body=None, secret="test-secret"):
    raw = None if body is None else json.dumps(body).encode()
    headers = {"X-Queue-Secret": secret}
    if raw is not None:
        headers["Content-Type"] = "application/json"
    req = Request(base + path, data=raw, headers=headers, method="POST" if raw is not None else "GET")
    try:
        with urlopen(req, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, help="Path to generated todo-server.py")
    parser.add_argument("--ui", required=True, help="Path to generated todos.html")
    parser.add_argument("--culture", required=True, help="Path to full current culture message")
    args = parser.parse_args()

    runtime = Path(args.runtime).resolve()
    ui_path = Path(args.ui).resolve()
    culture_path = Path(args.culture).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        fake_queue = ThreadingHTTPServer(("127.0.0.1", 0), FakeQueueHandler)
        threading.Thread(target=fake_queue.serve_forever, daemon=True).start()
        cfg = Path(tmp) / "queue.env"
        cfg.write_text(
            "\n".join([
                "INSTALL_DIR=" + tmp,
                "HOST_ID=testhost",
                "HUD_PORT=" + str(fake_queue.server_port),
                "TODO_PORT=0",
                "QUEUE_URL=http://127.0.0.1:" + str(fake_queue.server_port),
                "QUEUE_SECRET=test-secret",
                "TTYD_PORT=7681",
                "TTYD_BROWSER_PORT=7681",
            ]) + "\n"
        )
        os.environ["MYPEOPLE_CONFIG_PATH"] = str(cfg)
        for key in ("INSTALL_DIR", "HOST_ID", "HUD_PORT", "TODO_PORT", "QUEUE_URL", "QUEUE_SECRET"):
            os.environ.pop(key, None)
        sys.path.insert(0, str(runtime.parent))
        sys.modules.pop("mpcommon", None)
        spec = importlib.util.spec_from_file_location("todo_server_under_test", runtime)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.TODOS_DIR = str(Path(tmp) / "todos")
        module.BOARD_PATH = str(Path(tmp) / "todos" / "board.v2.json")
        module.PROOFS_DIR = str(Path(tmp) / "todos" / "proofs")
        module.INBOX_LOG = str(Path(tmp) / "todos" / "boss-inbox.log")
        signals, routed = [], []
        module.ping_boss = signals.append
        module.mp_send = lambda agent, message: routed.append((agent, message)) or 0
        module.save_board(module.default_board())
        todo = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
        threading.Thread(target=todo.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:" + str(todo.server_port)

        status, added = request(base, "/todo/update", {"op": "add", "text": "owner regression"})
        assert status == 200 and added["ok"], (status, added)
        task_id = added["id"]
        boss = "testhost/main:Boss"
        owner1 = "testhost/main:owner-1"
        owner2 = "testhost/main:owner-2"
        owner3 = "testhost/main:owner-3"
        temporary = "testhost/main:temp-1"

        def row(agent_id, lifecycle="owner", owner_task_id=task_id, **extra):
            value = {
                "agent_id": agent_id, "state": "alive", "retired": False,
                "boss_id": boss, "is_master": False, "lifecycle": lifecycle,
                "owner_task_id": owner_task_id,
            }
            value.update(extra)
            return value

        FakeQueueHandler.roster = [
            row(owner1), row(owner2), row(owner3),
            row(temporary, lifecycle="temporary", owner_task_id=""),
            row("testhost/main:dead", state="dead"),
            row("testhost/main:retired", retired=True),
        ]

        def owner_call(action, agent_id, by=boss):
            return request(base, "/todo/owner", {
                "action": action, "task_id": task_id, "agent_id": agent_id, "by": by,
            })

        assert owner_call("assign", "arbitrary")[0] == 400
        assert owner_call("assign", "testhost/main:unknown")[0] == 400
        assert owner_call("assign", "testhost/main:dead")[0] == 400
        assert owner_call("assign", "testhost/main:retired")[0] == 400
        assert owner_call("assign", temporary)[0] == 400
        assert owner_call("assign", owner1, by="testhost/main:not-boss")[0] == 403
        status, assigned = owner_call("assign", owner1)
        assert status == 200 and assigned["assignee"] == owner1
        status, implicit = owner_call("assign", owner2)
        assert status == 409 and implicit["error"] == "owner_exists"
        status, generic = request(base, "/todo/update", {
            "op": "set", "id": task_id, "assignee": owner2,
        })
        assert status == 400 and generic["error"] == "assignee_controlled"
        status, replaced = owner_call("replace", owner2)
        assert status == 200 and replaced["previous"] == owner1

        for text in ("first follow-up", "second follow-up"):
            assert request(base, "/todo/comment", {
                "task_id": task_id, "by": "CEO", "body": text,
            })[0] == 200
        time.sleep(0.1)
        assert [agent for agent, _ in routed[-2:]] == [owner2, owner2], routed
        assert request(base, "/todo/status", {
            "task_id": task_id, "state": "review", "by": owner2,
        })[0] == 200
        board = request(base, "/todo/board")[1]
        assert board["tasks"][task_id]["assignee"] == owner2

        assert request(base, "/todo/status", {
            "task_id": task_id, "state": "done", "by": "CEO",
        })[0] == 200
        closed = request(base, "/todo/board")[1]["tasks"][task_id]
        assert closed["assignee"] == owner2
        assert any("CLOSED by the CEO" in message and owner2 in message for message in signals)
        assert request(base, "/todo/status", {
            "task_id": task_id, "state": "working", "by": "CEO",
        })[0] == 200
        reopened = request(base, "/todo/board")[1]["tasks"][task_id]
        assert reopened["ownerNeedsReplacement"] is True
        assert owner_call("reopen", owner2)[0] == 409
        status, fresh = owner_call("reopen", owner3)
        assert status == 200 and fresh["assignee"] == owner3
        final_task = request(base, "/todo/board")[1]["tasks"][task_id]
        actions = [event["action"] for event in final_task["ownerHistory"]]
        assert actions == ["assign", "replace", "closed", "reopen_requested", "reopen"], actions
        assert final_task["assignee"] == owner3 and not final_task["ownerNeedsReplacement"]

        ui = ui_path.read_text()
        assert 'patchTask(task.id,{assignee' not in ui
        assert 'placeholder="host/session:agent"' not in ui
        assert 'task.ownerHistory||[]' in ui and 'owner.textContent=task.assignee||"unassigned"' in ui
        culture = culture_path.read_text()
        required = [
            "BOSS CULTURE — A LOVE DECLARATION (v6)", "REAL WORK CARD", "TEMPORARY engineer",
            "until I close the card", "If I reopen the card", "never written into a card's assignee",
            "never micromanage", "gold-standard process", "SEED HYDRATION", "todo app, NEVER the terminal",
        ]
        assert all(fragment in culture for fragment in required)
        assert "when the CEO" not in culture.lower()
        assert "you spawn to know and kill when you know" not in culture

        todo.shutdown()
        fake_queue.shutdown()
    print("PASS owner lifecycle: validation, authority, routing, turn persistence, close/reopen, UI, culture")


if __name__ == "__main__":
    main()
