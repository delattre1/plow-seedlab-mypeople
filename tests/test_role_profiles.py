#!/usr/bin/env python3
"""Regression tests for versioned MyPeople role resolution and backend adapters."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import json
import io
import os
import shlex
import shutil
import subprocess
import tempfile


def load_script(name, path):
    loader = importlib.machinery.SourceFileLoader(name, os.path.abspath(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def assert_materialized(bundle, backend, role):
    root = bundle["bundle_path"]
    attestation = json.load(open(os.path.join(root, "attestation.json")))
    assert attestation["role_ref"] == bundle["role_ref"]
    assert attestation["role_digest"] == bundle["digest"]
    startup = open(os.path.join(root, "common", "startup.md")).read()
    assert "# MyPeople locked role" in startup
    assert "# Operating MyPeople" in startup
    assert "Role: `%s`" % bundle["role_ref"] in startup
    for skill in bundle["skills"]:
        common = os.path.join(root, "common", "skills", skill["name"], "SKILL.md")
        assert os.path.isfile(common)
        native = (os.path.join(root, "claude", "plugin", "skills", skill["name"], "SKILL.md")
                  if backend == "claude" else
                  os.path.join(root, "codex", "home", "skills", skill["name"], "SKILL.md"))
        assert open(common, "rb").read() == open(native, "rb").read()
        assert not (os.stat(common).st_mode & 0o222), common
    if backend == "claude":
        assert os.path.isfile(os.path.join(bundle["plugin_path"], ".claude-plugin", "plugin.json"))
    else:
        assert os.path.isfile(bundle["settings_path"])
        assert os.path.isfile(os.path.join(bundle["codex_home"], "hooks.json"))
        assert os.path.islink(os.path.join(bundle["codex_home"], "sessions"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, help="installed bin/mp")
    parser.add_argument("--roles", required=True, help="canonical roles directory")
    parser.add_argument("--boss-doc", required=True, help="installed/generated Boss doctrine")
    parser.add_argument("--queue-client", help="installed queue-client.py")
    parser.add_argument("--codex-prompt", action="store_true",
                        help="attest Codex model-visible developer instructions")
    args = parser.parse_args()

    mp = load_script("mypeople_mp_role_test", args.runtime)
    mp.ROLE_DIR = os.path.abspath(args.roles)
    mp.BOSS_DOC = os.path.abspath(args.boss_doc)

    with tempfile.TemporaryDirectory(prefix="mypeople-role-test-") as temp:
        mp.ROLE_BUNDLE_DIR = os.path.join(temp, "bundles")
        by_role = {}
        for role in ("engineer", "boss"):
            resolved = {}
            for backend in ("claude", "codex"):
                item = mp.resolve_role(role, backend)
                item = mp.materialize_role(item, "test/main:%s-%s" % (role, backend), backend)
                assert_materialized(item, backend, role)
                resolved[backend] = item
            assert resolved["claude"]["digest"] == resolved["codex"]["digest"]
            assert resolved["claude"]["personality_digest"] == resolved["codex"]["personality_digest"]
            assert {x["name"]: x["sha256"] for x in resolved["claude"]["skills"]} == {
                x["name"]: x["sha256"] for x in resolved["codex"]["skills"]}
            by_role[role] = resolved

        assert open(by_role["boss"]["claude"]["personality_path"], "rb").read() == \
            open(args.boss_doc, "rb").read(), "Boss personality forked BOSS_DOC"

        engineer_claude = by_role["engineer"]["claude"]
        launch = mp.build_launch("test/main:eng", "/tmp", "test/main:Boss", False,
                                 "test-model", "claude", role_bundle=engineer_claude)
        words = shlex.split(launch)
        assert "--append-system-prompt-file" in words and "--plugin-dir" in words and "--settings" in words
        assert "MYPEOPLE_ROLE_REF=engineer@1.0.0" in launch

        engineer_codex = by_role["engineer"]["codex"]
        launch = mp.build_launch("test/main:eng", "/tmp", "test/main:Boss", False,
                                 "test-model", "codex", role_bundle=engineer_codex)
        words = shlex.split(launch)
        assert "--profile" in words and "CODEX_HOME=" in launch
        assert "developer_instructions" in open(engineer_codex["settings_path"]).read()

        legacy = mp.build_launch("test/main:legacy", "/tmp", "test/main:Boss", False,
                                 "test-model", "claude")
        assert "MYPEOPLE_ROLE" not in legacy and "--append-system-prompt-file" not in legacy
        assert "--plugin-dir" not in legacy and "--settings" not in legacy

        # A damaged materialized native skill is reconstructed from canonical bytes.
        damaged = os.path.join(engineer_claude["plugin_path"], "skills",
                               "mypeople-system", "SKILL.md")
        os.chmod(damaged, 0o644)
        os.unlink(damaged)
        healed = mp.materialize_role(mp.resolve_role("engineer", "claude"),
                                     "test/main:engineer-claude", "claude")
        assert os.path.isfile(os.path.join(healed["plugin_path"], "skills",
                                           "mypeople-system", "SKILL.md"))

        # Missing/corrupt canonical required content fails before a caller can launch tmux.
        broken_roles = os.path.join(temp, "broken-roles")
        shutil.copytree(args.roles, broken_roles)
        broken_skill = os.path.join(broken_roles, "skills", "mypeople-system", "2.0.0", "SKILL.md")
        os.chmod(os.path.dirname(broken_skill), 0o755)
        os.chmod(broken_skill, 0o644)
        os.unlink(broken_skill)
        mp.ROLE_DIR = broken_roles
        try:
            mp.resolve_role("engineer", "claude")
            raise AssertionError("missing mandatory skill did not fail closed")
        except ValueError as error:
            assert "unavailable" in str(error)
        try:
            mp.resolve_role("unknown", "claude")
            raise AssertionError("unknown role did not fail closed")
        except ValueError as error:
            assert "unknown role" in str(error)
        mp.ROLE_DIR = os.path.abspath(args.roles)

        diagnostics = io.StringIO()
        with contextlib.redirect_stderr(diagnostics):
            assert mp.do_spawn(["main:role-test-invalid-boss", "--backend", "claude",
                                "--role", "boss"]) == 2
            assert mp.do_spawn(["main:role-test-invalid-engineer", "--backend", "claude",
                                "--role", "engineer", "--master"]) == 2
            assert mp.do_spawn(["main:role-test-drift", "--backend", "claude",
                                "--role", "engineer"], locked_role_ref="engineer@1.0.0",
                               expected_role_digest="0" * 64) == 5
        assert "requires --master" in diagnostics.getvalue()
        assert "cannot be combined" in diagnostics.getvalue()
        assert "locked role digest drift" in diagnostics.getvalue()

        if args.codex_prompt:
            output = subprocess.check_output([
                "codex", "--profile", engineer_codex["codex_profile"],
                "debug", "prompt-input", "role-probe"
            ], env={**os.environ, "CODEX_HOME": engineer_codex["codex_home"]}, text=True)
            visible = json.dumps(json.loads(output), ensure_ascii=False)
            assert "Role: `engineer@1.0.0`" in visible
            assert "# Operating MyPeople" in visible
            assert "# Engineer card-owner workflow" in visible

    if args.queue_client:
        client = load_script("mypeople_queue_client_role_test", args.queue_client)
        calls = []

        class Result:
            returncode = 0
            stdout = "spawned"
            stderr = ""

        client.subprocess.run = lambda argv, **kwargs: calls.append(argv) or Result()
        ok, _ = client.dispatch({"type": "spawn", "target_agent": "test/main:remote",
                                 "payload": {"backend": "codex", "role": "engineer",
                                             "boss": "test/main:Boss", "owner_task_id": "card-1"}})
        assert ok
        command = calls[-1]
        assert command[command.index("--role") + 1] == "engineer"
        assert command[command.index("--owner-task") + 1] == "card-1"

    print("PASS roles: fail-closed resolution, digest parity, native adapters, legacy compatibility")


if __name__ == "__main__":
    main()
