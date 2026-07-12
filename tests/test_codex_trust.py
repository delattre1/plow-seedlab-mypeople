#!/usr/bin/env python3
"""Regression test for MyPeople's narrowly scoped Codex trust override."""
import argparse
import importlib.machinery
import importlib.util
import os
import shlex
import tempfile
import tomllib


def load_runtime(path):
    loader = importlib.machinery.SourceFileLoader("mypeople_mp_under_test", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def config_arg(launch):
    words = shlex.split(launch)
    positions = [i for i, word in enumerate(words) if word == "--config"]
    assert len(positions) <= 1, "Codex launch must have at most one trust override"
    return words[positions[0] + 1] if positions else ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, help="path to installed bin/mp")
    args = parser.parse_args()
    mp = load_runtime(os.path.abspath(args.runtime))

    eng_root = os.path.join(mp.INSTALL_DIR, "run", "eng")
    os.makedirs(eng_root, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="trust-positive-", dir=eng_root) as managed, \
         tempfile.TemporaryDirectory(prefix="trust-negative-") as outside:
        launch = mp.build_launch("host/main:eng-test", managed, "host/main:Boss", False,
                                 "test-model", "codex")
        override = config_arg(launch)
        assert override, "managed Codex cwd did not receive a trust override"
        parsed = tomllib.loads(override)
        resolved = os.path.realpath(managed)
        assert parsed == {"projects": {resolved: {"trust_level": "trusted"}}}
        assert "--dangerously-bypass-hook-trust" in shlex.split(launch)
        assert "test-model" in shlex.split(launch)

        outside_launch = mp.build_launch("host/main:eng-test", outside, "host/main:Boss", False,
                                         "test-model", "codex")
        assert not config_arg(outside_launch), "outside cwd was incorrectly trusted"

        link = os.path.join(eng_root, "trust-escape-%s" % os.getpid())
        os.symlink(outside, link)
        try:
            escaped_launch = mp.build_launch("host/main:eng-test", link, "host/main:Boss", False,
                                             "test-model", "codex")
            assert not config_arg(escaped_launch), "managed-root symlink escape was trusted"
        finally:
            os.unlink(link)

        claude_launch = mp.build_launch("host/main:eng-test", outside, "host/main:Boss", False,
                                        "test-model", "claude")
        assert "--config" not in shlex.split(claude_launch), "Claude launch behavior changed"

    print("PASS Codex trust override: exact managed cwd only; outside and symlink escape rejected")


if __name__ == "__main__":
    main()
