"""install.py — always-on block + skill for claude / cursor / agents."""

from __future__ import annotations

from roadmapify import install
from roadmapify.cli import EXIT_OK


def test_skill_source_is_packaged():
    assert install.skill_source().is_file()
    assert (install.references_source() / "check.md").is_file()
    assert (install.references_source() / "query.md").is_file()
    assert (install.references_source() / "hooks.md").is_file()


def test_install_skill_claude_project(project, run):
    code, out = run("install", "--root", str(project), "--skill", "--project")
    assert code == EXIT_OK
    skill = project / ".claude" / "skills" / "roadmapify" / "SKILL.md"
    assert skill.is_file()
    assert "roadmap check" in skill.read_text()
    assert (skill.parent / "references" / "query.md").is_file()
    assert (project / "CLAUDE.md").is_file()


def test_install_skill_cursor(project, run):
    code, out = run("install", "--root", str(project), "--platform", "cursor")
    assert code == EXIT_OK
    rule = project / ".cursor" / "rules" / "roadmapify.mdc"
    assert rule.is_file()
    text = rule.read_text()
    assert "alwaysApply: true" in text
    assert "roadmap check" in text


def test_install_skill_agents_user_scope(project, run, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(install.Path, "home", classmethod(lambda cls: home))
    # Force Path.home() used inside install_skill
    import pathlib
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
    results = install.install_skill("agents", project=False, root=project)
    dst = home / ".agents" / "skills" / "roadmapify" / "SKILL.md"
    assert dst.is_file()
    assert any(str(dst) in k or k.endswith("SKILL.md") for k in results)


def test_uninstall_skill_cursor(project, run):
    run("install", "--root", str(project), "--platform", "cursor")
    code, _ = run("install", "--root", str(project), "--platform", "cursor",
                  "--uninstall")
    assert code == EXIT_OK
    assert not (project / ".cursor" / "rules" / "roadmapify.mdc").exists()
