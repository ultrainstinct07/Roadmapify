"""hooks.py — marked post-commit / post-checkout scripts."""

from __future__ import annotations

from pathlib import Path

from roadmapify import hooks
from roadmapify.cli import EXIT_ERROR, EXIT_OK


def test_install_requires_a_git_repo(project, run):
    code, _ = run("hook", "install", "--root", str(project))
    assert code == EXIT_ERROR


def test_install_writes_marked_hooks(project, run):
    git = project / ".git" / "hooks"
    git.mkdir(parents=True)
    # Pre-existing neighbour content must survive.
    (git / "post-commit").write_text("#!/bin/sh\necho neighbour\n")
    code, out = run("hook", "install", "--root", str(project))
    assert code == EXIT_OK
    text = (git / "post-commit").read_text()
    assert "echo neighbour" in text
    assert hooks.HOOK_START in text and hooks.HOOK_END in text
    assert "refresh_brief" in text or "roadmap brief" in text
    assert (git / "post-checkout").is_file()
    assert ">>> roadmapify >>>" in (git / "post-checkout").read_text()


def test_install_is_idempotent(project, run):
    (project / ".git" / "hooks").mkdir(parents=True)
    run("hook", "install", "--root", str(project))
    first = (project / ".git" / "hooks" / "post-commit").read_text()
    code, out = run("hook", "install", "--root", str(project))
    assert code == EXIT_OK
    assert "unchanged" in out or (project / ".git" / "hooks" / "post-commit").read_text() == first
    assert first.count(hooks.HOOK_START) == 1


def test_uninstall_restores_neighbour(project, run):
    git = project / ".git" / "hooks"
    git.mkdir(parents=True)
    (git / "post-commit").write_text("#!/bin/sh\necho keep-me\n")
    run("hook", "install", "--root", str(project))
    code, _ = run("hook", "uninstall", "--root", str(project))
    assert code == EXIT_OK
    text = (git / "post-commit").read_text()
    assert "keep-me" in text
    assert hooks.HOOK_START not in text


def test_status_reports_installed(project, run):
    (project / ".git" / "hooks").mkdir(parents=True)
    run("hook", "install", "--root", str(project))
    code, out = run("hook", "status", "--root", str(project))
    assert code == EXIT_OK
    assert "installed" in out


def test_hook_body_never_contains_mutating_git_verbs():
    """T-19 preview: the installed script must not mutate the repo."""
    body = hooks._hook_body("/usr/bin/python3")
    for verb in ("git branch", "git checkout", "git switch", "git rebase",
                 "git merge", "git push", "git reset", "git config"):
        assert verb not in body, f"hook body contains {verb!r}"
