"""Wiring roadmapify into an agent host.

The **always-on block** pays off immediately: no trigger, no skill fire. The
**skill** (``skill.md`` + ``references/``) is opt-in via ``--skill`` /
``--platform`` and covers only three hosts on purpose — see risk R-376r.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from roadmapify.paths import ensure_marked_block, write_text_atomic

BLOCK_START = "<!-- >>> roadmapify >>> -->"
BLOCK_END = "<!-- <<< roadmapify <<< -->"

#: Files an agent host reads unprompted. CLAUDE.md is Claude Code's; AGENTS.md is
#: the cross-host convention. Both get the same block — an agent should not
#: behave differently because of which one its host happens to load.
ALWAYS_ON_TARGETS = ("CLAUDE.md", "AGENTS.md")

#: Platforms that receive the skill. Deliberately tiny — graphify's 20+ forks
#: are how the CLI surface outruns the engine.
PLATFORMS = ("claude", "cursor", "agents")


def always_on_text() -> str:
    """The packaged block, read from disk rather than inlined."""
    path = Path(__file__).parent / "always_on" / "claude-md.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(
            f"roadmapify is installed incompletely: missing {path}. Reinstall the package."
        ) from exc


def skill_source() -> Path:
    return Path(__file__).parent / "skill.md"


def references_source() -> Path:
    return Path(__file__).parent / "references"


def install_always_on(root: "str | Path", *, targets=ALWAYS_ON_TARGETS) -> dict[str, str]:
    """Write the block into each target file that the project uses.

    A target is only written when it already exists, EXCEPT CLAUDE.md, which is
    created if nothing else is present — otherwise a project with neither file
    silently gets no always-on layer at all.
    """
    root = Path(root)
    body = always_on_text()
    results: dict[str, str] = {}
    existing = [t for t in targets if (root / t).is_file()]
    for target in existing or targets[:1]:
        results[target] = ensure_marked_block(
            root / target, body, start=BLOCK_START, end=BLOCK_END)
    return results


def uninstall_always_on(root: "str | Path", *, targets=ALWAYS_ON_TARGETS) -> dict[str, str]:
    """Remove our block, leaving everything else in the file untouched."""
    root = Path(root)
    results: dict[str, str] = {}
    for target in targets:
        path = root / target
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if BLOCK_START not in text:
            results[target] = "absent"
            continue
        head, _, rest = text.partition(BLOCK_START)
        _, _, tail = rest.partition(BLOCK_END)
        write_text_atomic(path, (head.rstrip("\n") + "\n" + tail.lstrip("\n")).lstrip("\n"))
        results[target] = "removed"
    return results


def _copy_skill_tree(dst_skill: Path) -> str:
    """Write skill.md (+ references/) atomically-enough. Returns created|updated."""
    src = skill_source()
    refs = references_source()
    if not src.is_file():
        raise RuntimeError(
            f"roadmapify is installed incompletely: missing {src}. Reinstall the package."
        )
    existed = dst_skill.is_file()
    dst_skill.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(dst_skill, src.read_text(encoding="utf-8"))
    if refs.is_dir():
        dst_refs = dst_skill.parent / "references"
        if dst_refs.exists():
            shutil.rmtree(dst_refs)
        shutil.copytree(refs, dst_refs)
    return "updated" if existed else "created"


def _skill_destination(platform: str, *, project: bool, root: Path) -> Path:
    home = Path.home()
    if platform == "claude":
        base = root / ".claude" / "skills" if project else home / ".claude" / "skills"
        return base / "roadmapify" / "SKILL.md"
    if platform == "cursor":
        # Cursor uses rules, not SKILL.md — handled separately.
        return root / ".cursor" / "rules" / "roadmapify.mdc"
    if platform == "agents":
        base = root / ".agents" / "skills" if project else home / ".agents" / "skills"
        return base / "roadmapify" / "SKILL.md"
    raise ValueError(f"unknown platform {platform!r}; choose from {PLATFORMS}")


def _cursor_rule_body() -> str:
    skill = skill_source().read_text(encoding="utf-8")
    # Strip YAML frontmatter for the rule body; Cursor wants alwaysApply frontmatter.
    if skill.startswith("---"):
        parts = skill.split("---", 2)
        body = parts[2].strip() if len(parts) >= 3 else skill
    else:
        body = skill
    return (
        "---\n"
        "description: roadmapify — plan and decision memory that survives context loss\n"
        "alwaysApply: true\n"
        "---\n\n"
        f"{body}\n"
    )


def install_skill(platform: str, *, project: bool = False,
                  root: "str | Path | None" = None) -> dict[str, str]:
    """Install the skill (or Cursor rule) for one platform."""
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}; choose from {PLATFORMS}")
    root = Path(root) if root else Path.cwd()
    results: dict[str, str] = {}

    if platform == "cursor":
        # Cursor rules are always project-scoped.
        path = _skill_destination("cursor", project=True, root=root)
        existed = path.is_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, _cursor_rule_body())
        results[str(path)] = "updated" if existed else "created"
        # Also drop references next to the rule for on-demand load.
        refs = references_source()
        if refs.is_dir():
            dst = path.parent / "roadmapify-references"
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(refs, dst)
        return results

    dst = _skill_destination(platform, project=project, root=root)
    results[str(dst)] = _copy_skill_tree(dst)
    if platform == "claude" and project:
        # Project-scoped Claude also gets the always-on block for free.
        for target, state in install_always_on(root).items():
            results[target] = state
    return results


def uninstall_skill(platform: str, *, project: bool = False,
                    root: "str | Path | None" = None) -> dict[str, str]:
    root = Path(root) if root else Path.cwd()
    results: dict[str, str] = {}
    if platform == "cursor":
        path = root / ".cursor" / "rules" / "roadmapify.mdc"
        refs = root / ".cursor" / "rules" / "roadmapify-references"
        if path.is_file():
            path.unlink()
            results[str(path)] = "removed"
        else:
            results[str(path)] = "absent"
        if refs.is_dir():
            shutil.rmtree(refs)
            results[str(refs)] = "removed"
        return results
    dst = _skill_destination(platform, project=project, root=root)
    parent = dst.parent
    if parent.is_dir() and parent.name == "roadmapify":
        shutil.rmtree(parent)
        results[str(parent)] = "removed"
    else:
        results[str(dst)] = "absent"
    return results
