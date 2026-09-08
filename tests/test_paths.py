from __future__ import annotations

import json
import threading

import pytest

from roadmapify import paths


def test_atomic_write_leaves_no_temp_files(tmp_path):
    target = tmp_path / "out" / "f.txt"
    paths.write_text_atomic(target, "hello")
    assert target.read_text() == "hello"
    assert [p.name for p in target.parent.iterdir()] == ["f.txt"]


def test_atomic_write_preserves_previous_content_on_failure(tmp_path):
    target = tmp_path / "f.txt"
    paths.write_text_atomic(target, "good")
    with pytest.raises(RuntimeError):
        paths._atomic_replace(target, lambda f: (_ for _ in ()).throw(RuntimeError("boom")))
    assert target.read_text() == "good"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".rmap-")]


def test_atomic_write_follows_symlinks(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("old")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    paths.write_text_atomic(link, "new")
    assert link.is_symlink(), "the link must survive, not be replaced by a regular file"
    assert real.read_text() == "new"


def test_a_torn_write_never_swallows_the_next_record(tmp_path):
    """A crash mid-append leaves a line with no newline; the next append lands
    directly behind it. The remnant is genuinely lost — it was never fully
    written — but the record behind it must survive."""
    log = tmp_path / "j.jsonl"
    paths.append_jsonl(log, {"id": "a"})
    paths.append_jsonl(log, {"id": "b"})
    raw = log.read_bytes()
    log.write_bytes(raw[: -len('"b"}\n')])  # torn: no trailing newline
    paths.append_jsonl(log, {"id": "c"})
    ids = [r["id"] for r in paths.read_jsonl(log)]
    assert ids == ["a", "c"], f"got {ids}"


def test_recovery_gives_up_on_a_line_of_pure_garbage(tmp_path):
    log = tmp_path / "j.jsonl"
    log.write_text("{{{{{{{{{{{{ not json anywhere in here {\n" + '{"id":"ok"}\n')
    assert [r["id"] for r in paths.read_jsonl(log)] == ["ok"]


def test_read_jsonl_skips_garbage_rather_than_failing(tmp_path):
    log = tmp_path / "j.jsonl"
    log.write_text('{"id":"a"}\nnot json at all\n\n{"id":"b"}\n["not an object"]\n')
    assert [r["id"] for r in paths.read_jsonl(log)] == ["a", "b"]


def test_append_jsonl_is_single_write(tmp_path):
    """Records must be atomic against a concurrent appender."""
    log = tmp_path / "j.jsonl"

    def worker(n: int) -> None:
        for i in range(40):
            paths.append_jsonl(log, {"id": f"{n}-{i}", "pad": "x" * 200})

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = [ln for ln in log.read_text().split("\n") if ln]
    assert len(lines) == 160, "a lost or split record means the appends interleaved"
    for line in lines:
        json.loads(line)  # every record landed whole
    assert len(paths.read_jsonl(log)) == 160


def test_ensure_marked_block_is_idempotent_and_preserves_neighbours(tmp_path):
    f = tmp_path / ".gitignore"
    f.write_text("*.pyc\nnode_modules/\n")
    assert paths.ensure_marked_block(f, "roadmap-out/*", start="# >>>", end="# <<<") == "updated"
    first = f.read_text()
    assert paths.ensure_marked_block(f, "roadmap-out/*", start="# >>>", end="# <<<") == "unchanged"
    assert f.read_text() == first
    assert paths.ensure_marked_block(f, "roadmap-out/**", start="# >>>", end="# <<<") == "updated"
    assert "*.pyc" in f.read_text() and "node_modules/" in f.read_text()
    assert f.read_text().count("# >>>") == 1, "a second install must replace, not append"
    assert "roadmap-out/**" in f.read_text() and "roadmap-out/*\n" not in f.read_text()


def test_derive_lock_is_exclusive_and_hooks_can_skip(tmp_path):
    with paths.derive_lock(tmp_path, wait=0):
        with pytest.raises(paths.LockBusy):
            # wait=0 is the hook's mode: skip immediately rather than queue behind
            # a long CLI run and delay somebody's commit.
            with paths.derive_lock(tmp_path, wait=0):
                pass
    with paths.derive_lock(tmp_path, wait=0):
        pass  # released cleanly


def test_project_root_finds_the_plan_from_a_subdirectory(tmp_path):
    root = tmp_path / "p"
    (root / "a" / "b").mkdir(parents=True)
    (root / paths.PLAN_FILENAME).write_text("schema = 1\n")
    assert paths.project_root(root / "a" / "b") == root.resolve()


def test_project_root_falls_back_to_git_then_to_cwd(tmp_path):
    root = tmp_path / "p"
    (root / "sub").mkdir(parents=True)
    (root / ".git").mkdir()
    assert paths.project_root(root / "sub") == root.resolve()
    lone = tmp_path / "lone"
    lone.mkdir()
    assert paths.project_root(lone) == lone.resolve()


def test_out_dir_honours_an_absolute_override(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ROADMAP_OUT", str(tmp_path / "shared"))
    assert paths.out_dir(tmp_path / "anything") == tmp_path / "shared"
