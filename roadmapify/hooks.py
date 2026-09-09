"""Git hooks that keep derived files current.

Installs marked blocks into ``.git/hooks/post-commit`` and ``post-checkout``.
The hook body runs ``roadmap brief`` (which also rewrites ``graph.json``) with
``derive_lock(wait=0)`` — skip on contention; the next fire catches up.

**Never** writes a git ref, never runs ``git config``, never installs a custom
merge driver. The only filesystem writes are the hook scripts themselves and,
when a hook fires, the disposable derived set under ``roadmap-out/``.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from roadmapify.paths import project_root, write_text_atomic

HOOK_START = "# >>> roadmapify >>>"
HOOK_END = "# <<< roadmapify <<<"

HOOK_NAMES = ("post-commit", "post-checkout")

_HOOK_BODY = """\
# Refresh BRIEF.md + graph.json. wait=0: skip if another roadmap process holds
# the derive lock — hooks are idempotent and the next run catches up.
# PINNED was recorded at `roadmap hook install` so GUI clients without
# ~/.local/bin on PATH still find the package.
if [ "${ROADMAP_SKIP_HOOK:-0}" != "1" ]; then
_PINNED='__PINNED_PYTHON__'
_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || _ROOT=""
if [ -n "$_ROOT" ] && [ -n "$_PINNED" ] && [ -x "$_PINNED" ]; then
  "$_PINNED" -c "from roadmapify.cli import refresh_brief; from pathlib import Path; import sys; refresh_brief(Path(sys.argv[1]), wait=0)" "$_ROOT" >/dev/null 2>&1 &
elif [ -n "$_ROOT" ] && command -v roadmap >/dev/null 2>&1; then
  roadmap brief --root "$_ROOT" >/dev/null 2>&1 &
fi
fi
"""


def _find_git_dir(root: "str | Path") -> "Path | None":
    """Nearest ``.git`` directory (or the gitdir file target for worktrees)."""
    here = Path(root).resolve()
    for d in [here, *here.parents]:
        git = d / ".git"
        if git.is_dir():
            return git
        if git.is_file():
            # worktree: .git is "gitdir: /path/to/..."
            try:
                text = git.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if text.lower().startswith("gitdir:"):
                target = Path(text.split(":", 1)[1].strip())
                if not target.is_absolute():
                    target = (d / target).resolve()
                return target if target.is_dir() else None
            return None
    return None


def _hook_body(pinned: str) -> str:
    return _HOOK_BODY.replace("__PINNED_PYTHON__", pinned.replace("'", "'\\''"))


def _ensure_shebang(path: Path) -> None:
    """Hook files need a shebang when we create them from scratch."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    if text.startswith("#!"):
        return
    write_text_atomic(path, "#!/bin/sh\n" + text)


def _chmod_executable(path: Path) -> None:
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def _replace_marked(path: Path, body: str) -> str:
    """Insert or replace our marked block without churning neighbouring whitespace."""
    block = f"{HOOK_START}\n{body.strip()}\n{HOOK_END}\n"
    try:
        original = path.read_text(encoding="utf-8")
    except OSError:
        original = ""
    if HOOK_START in original and HOOK_END in original:
        head, _, rest = original.partition(HOOK_START)
        _, _, tail = rest.partition(HOOK_END)
        # Keep a single newline between shebang/neighbours and our block.
        head = head.rstrip("\n")
        prefix = (head + "\n") if head else ""
        new = prefix + block + tail.lstrip("\n")
        if new == original:
            return "unchanged"
        write_text_atomic(path, new)
        return "updated"
    new = (original.rstrip("\n") + "\n" if original.strip() else "") + block
    if not new.startswith("#!"):
        new = "#!/bin/sh\n" + new
    write_text_atomic(path, new)
    return "created" if not original.strip() else "updated"


def install(root: "str | Path | None" = None, *,
            python: "str | None" = None) -> dict[str, str]:
    """Install/update roadmapify blocks in post-commit and post-checkout."""
    root = Path(root) if root else project_root()
    git_dir = _find_git_dir(root)
    if git_dir is None:
        raise FileNotFoundError(
            f"no .git under {root} — init a repository before installing hooks"
        )
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    pinned = python or sys.executable
    body = _hook_body(pinned)
    results: dict[str, str] = {}
    for name in HOOK_NAMES:
        path = hooks_dir / name
        state = _replace_marked(path, body)
        _ensure_shebang(path)
        _chmod_executable(path)
        results[name] = state
    return results


def uninstall(root: "str | Path | None" = None) -> dict[str, str]:
    """Remove our marked blocks; leave neighbouring hook content untouched."""
    root = Path(root) if root else project_root()
    git_dir = _find_git_dir(root)
    if git_dir is None:
        return {n: "absent" for n in HOOK_NAMES}
    results: dict[str, str] = {}
    for name in HOOK_NAMES:
        path = git_dir / "hooks" / name
        if not path.is_file():
            results[name] = "absent"
            continue
        text = path.read_text(encoding="utf-8")
        if HOOK_START not in text:
            results[name] = "absent"
            continue
        head, _, rest = text.partition(HOOK_START)
        _, _, tail = rest.partition(HOOK_END)
        new = (head.rstrip("\n") + "\n" + tail.lstrip("\n")).lstrip("\n")
        if new.strip() in ("", "#!/bin/sh"):
            try:
                path.unlink()
            except OSError:
                write_text_atomic(path, "#!/bin/sh\n")
            results[name] = "removed"
        else:
            write_text_atomic(path, new if new.endswith("\n") else new + "\n")
            results[name] = "removed"
    return results


def status(root: "str | Path | None" = None) -> dict[str, str]:
    """Report whether each hook currently carries our block."""
    root = Path(root) if root else project_root()
    git_dir = _find_git_dir(root)
    if git_dir is None:
        return {n: "no-git" for n in HOOK_NAMES}
    out: dict[str, str] = {}
    for name in HOOK_NAMES:
        path = git_dir / "hooks" / name
        if not path.is_file():
            out[name] = "missing"
            continue
        text = path.read_text(encoding="utf-8")
        out[name] = "installed" if HOOK_START in text and HOOK_END in text else "other"
    return out
