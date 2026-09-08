"""The always-on block is the layer that needs no trigger.

`install.py` was the only module without a test file, which is how the two
things this file pins down went unnoticed: which files get written when only
some of them exist, and whether a second install replaces our block or appends
a second one. The second question is not academic — this repo's own AGENTS.md
was a hand-copy with no markers, so an install would have appended a duplicate,
differently-worded block underneath it.
"""

from __future__ import annotations

from roadmapify import install
from roadmapify.paths import ensure_marked_block

OTHER_TOOL = """<!-- >>> othertool >>> -->
## othertool
Some other tool owns these lines.
<!-- <<< othertool <<< -->
"""


def test_the_block_is_read_from_the_packaged_markdown():
    """It ships as reviewable prose next to the module so the installed bytes
    are provably the reviewed ones, not a string literal nobody diffs."""
    text = install.always_on_text()
    assert text.startswith("## roadmap")
    assert "roadmap check" in text


def test_install_writes_every_target_that_already_exists(project):
    (project / "CLAUDE.md").write_text("# project notes\n", encoding="utf-8")
    (project / "AGENTS.md").write_text("# agent notes\n", encoding="utf-8")

    result = install.install_always_on(project)

    assert set(result) == {"CLAUDE.md", "AGENTS.md"}
    for name in ("CLAUDE.md", "AGENTS.md"):
        body = (project / name).read_text(encoding="utf-8")
        assert install.BLOCK_START in body and install.BLOCK_END in body
        assert "notes" in body, "the user's own content must survive"


def test_install_creates_claude_md_when_the_project_has_neither(project):
    """Otherwise a project with neither file silently gets no always-on layer,
    which is the one failure that makes the whole design inert."""
    result = install.install_always_on(project)

    assert set(result) == {"CLAUDE.md"}
    assert (project / "CLAUDE.md").is_file()
    assert not (project / "AGENTS.md").exists()


def test_install_writes_only_agents_md_when_that_is_the_file_the_project_uses(project):
    (project / "AGENTS.md").write_text("# agent notes\n", encoding="utf-8")

    result = install.install_always_on(project)

    assert set(result) == {"AGENTS.md"}
    assert not (project / "CLAUDE.md").exists()


def test_a_second_install_replaces_the_block_rather_than_appending_one(project):
    """A block that accumulates copies of itself is worse than no block: the
    agent reads several differently-worded versions of the same rules."""
    (project / "CLAUDE.md").write_text("# notes\n", encoding="utf-8")

    install.install_always_on(project)
    first = (project / "CLAUDE.md").read_text(encoding="utf-8")
    install.install_always_on(project)
    second = (project / "CLAUDE.md").read_text(encoding="utf-8")

    assert first == second, "a re-install must be byte-identical"
    assert second.count(install.BLOCK_START) == 1


def test_another_tools_block_survives_byte_for_byte(project):
    """`ensure_marked_block` exists so the file belongs to the user and we only
    ever own the lines between our two markers."""
    (project / "CLAUDE.md").write_text(OTHER_TOOL, encoding="utf-8")

    install.install_always_on(project)
    body = (project / "CLAUDE.md").read_text(encoding="utf-8")

    assert OTHER_TOOL.strip() in body
    assert install.BLOCK_START in body


def test_uninstall_removes_our_block_and_leaves_everything_else(project):
    (project / "CLAUDE.md").write_text(OTHER_TOOL + "\n# my own heading\n", encoding="utf-8")
    install.install_always_on(project)

    result = install.uninstall_always_on(project)

    body = (project / "CLAUDE.md").read_text(encoding="utf-8")
    assert result["CLAUDE.md"] == "removed"
    assert install.BLOCK_START not in body
    assert OTHER_TOOL.strip() in body
    assert "# my own heading" in body


def test_uninstall_reports_absent_rather_than_failing(project):
    """`roadmap install --uninstall` has to be safe to run twice, and safe to
    run on a project that never installed."""
    (project / "CLAUDE.md").write_text("# notes\n", encoding="utf-8")

    assert install.uninstall_always_on(project)["CLAUDE.md"] == "absent"
    assert install.uninstall_always_on(project / "nope") == {}


def test_an_unmarkered_hand_copy_is_not_recognised_and_gets_a_real_block(project):
    """This is exactly the state this repo's own AGENTS.md was in: the same
    prose, hand-copied, drifted, with no markers. Nothing can recognise it, so
    the honest outcome is a real block added alongside — which is why the fix
    was to regenerate the file, not to run install on top of it."""
    (project / "AGENTS.md").write_text("## roadmap\n\nRules:\n- read the brief\n",
                                       encoding="utf-8")

    install.install_always_on(project)
    body = (project / "AGENTS.md").read_text(encoding="utf-8")

    assert body.count("## roadmap") == 2
    assert body.count(install.BLOCK_START) == 1


def test_ensure_marked_block_reports_what_it_did(project):
    path = project / "CLAUDE.md"
    path.write_text("# notes\n", encoding="utf-8")
    first = ensure_marked_block(path, "body", start=install.BLOCK_START,
                                end=install.BLOCK_END)
    second = ensure_marked_block(path, "body", start=install.BLOCK_START,
                                 end=install.BLOCK_END)
    assert first in {"added", "created", "updated", "unchanged"}
    assert second in {"unchanged", "updated"}
