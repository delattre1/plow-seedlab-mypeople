#!/usr/bin/env python3
"""Regression for generated tmux delivery: no empty or blind fallback submissions."""
import argparse
import importlib.util
from pathlib import Path


class Result:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, help="Path to generated mpcommon.py")
    args = parser.parse_args()

    spec = importlib.util.spec_from_file_location("mpcommon_under_test", Path(args.runtime).resolve())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    calls = []
    captures = []

    def run(argv, **_kwargs):
        calls.append(argv)
        if argv[:2] == ["tmux", "capture-pane"]:
            return Result(stdout=captures.pop(0) if captures else "")
        return Result()

    module.subprocess.run = run
    module.time.sleep = lambda _seconds: None

    target = "mc-main:Boss"
    for empty in (None, "", "   ", "\n\t"):
        before = len(calls)
        assert module.tmux_send_message(target, empty) is False
        assert len(calls) == before, "empty delivery touched tmux"

    calls.clear()
    assert module.tmux_send_message(target, "normal TODO reply") is True
    enters = [call for call in calls if call[-1:] == ["Enter"]]
    assert len(enters) == 1, calls

    calls.clear()
    captures[:] = ["working\n› \n"]
    assert module.tmux_send_message(target, "line one\nline two") is True
    enters = [call for call in calls if call[-1:] == ["Enter"]]
    assert len(enters) == 1, calls

    calls.clear()
    captures[:] = ["› [Pasted text #1 +1 lines]\n"]
    assert module.tmux_send_message(target, "line one\nline two") is True
    enters = [call for call in calls if call[-1:] == ["Enter"]]
    assert len(enters) == 2, calls

    print("PASS message delivery: empty rejected, normal single-submit, paste retry conditional")


if __name__ == "__main__":
    main()
