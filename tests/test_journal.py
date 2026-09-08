from __future__ import annotations

import pytest

from roadmapify import journal


def test_ids_are_content_hashed_not_sequential():
    """The property that makes git's union merge safe on a tracked append-only log.

    A counter (D-01, D-02) collides the moment two branches each record a
    decision; the union then holds two DIFFERENT records under one id and the
    projector silently keeps whichever it saw first.
    """
    a = journal.record("decision", "use sqlite", ts="2026-01-01T00:00:00Z", author="x")
    b = journal.record("decision", "use sqlite", ts="2026-01-01T00:00:00Z", author="x")
    c = journal.record("decision", "use postgres", ts="2026-01-01T00:00:00Z", author="x")
    d = journal.record("decision", "use sqlite", ts="2026-01-01T00:00:01Z", author="x")
    assert a["id"] == b["id"], "same content, same id — so a union merge dedups"
    assert a["id"] != c["id"] and a["id"] != d["id"]
    assert a["id"].startswith("D-")
    assert journal.record("constraint", "x", ts="t", author="a")["id"].startswith("C-")
    assert journal.record("risk", "x", ts="t", author="a")["id"].startswith("R-")


def test_record_is_pure_when_ts_is_given():
    kw = dict(ts="2026-01-01T00:00:00Z", author="x")
    assert journal.record("note", "hi", **kw) == journal.record("note", "hi", **kw)


def test_sanitize_strips_ansi_and_control_characters():
    """Journal text is injected into an agent's context via BRIEF.md.

    A cursor-control sequence could hide a record's real contents from a human
    reading the file while leaving them visible to the model.
    """
    dirty = "use \x1b[31mredis\x1b[0m\x00 for\x07 the cache"
    assert journal.sanitize(dirty) == "use redis for the cache"
    assert journal.sanitize("a\r\nb") == "a\nb"
    assert journal.sanitize("x" * 5000).endswith("…")
    assert len(journal.sanitize("x" * 5000)) <= journal.MAX_TEXT


def test_empty_text_is_refused():
    with pytest.raises(ValueError):
        journal.record("note", "\x00\x1b[0m   ")


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        journal.record("proclamation", "hi")


def test_parse_rejection_splits_on_the_first_colon():
    assert journal.parse_rejection("redis: an extra service to run") == {
        "alt": "redis", "reason": "an extra service to run"}
    assert journal.parse_rejection("redis") == {"alt": "redis", "reason": ""}
    # A URL in the reason must not confuse the split.
    assert journal.parse_rejection("x: see https://a/b")["reason"] == "see https://a/b"


def test_rejections_get_stable_first_class_ids(project):
    rec = journal.record("decision", "one-way sync", ts="2026-01-01T00:00:00Z", author="x",
                         rejected=["bidirectional: conflicts", "webhooks: needs a server"])
    journal.append(rec, project)
    xs = journal.rejections(root=project)
    assert [x["id"] for x in xs] == [f"X-{rec['id'][2:]}-1", f"X-{rec['id'][2:]}-2"]
    assert xs[0]["parent"] == rec["id"]
    # Re-projecting the same log yields the same ids — nothing is minted at read time.
    assert [x["id"] for x in journal.rejections(root=project)] == [x["id"] for x in xs]


def test_load_dedups_and_orders_stably(project):
    """A union merge duplicates every line the two branches shared."""
    rec = journal.record("note", "hello", ts="2026-01-02T00:00:00Z", author="x")
    older = journal.record("note", "earlier", ts="2026-01-01T00:00:00Z", author="x")
    from roadmapify.paths import append_jsonl
    for r in (rec, older, rec, rec):  # the duplicate lines a union merge produces
        append_jsonl(journal.journal_path(project), r)
    loaded = journal.load(project)
    assert [r["id"] for r in loaded] == [older["id"], rec["id"]]


def test_append_extends_the_id_on_a_genuine_collision(project, monkeypatch):
    monkeypatch.setattr(journal, "make_id", lambda *a, **k: "D-zzzz")
    first = journal.append(journal.record("decision", "a", ts="t1", author="x"), project)
    second = journal.append(journal.record("decision", "b", ts="t2", author="x"), project)
    assert first["id"] == "D-zzzz"
    assert second["id"] != first["id"], "different content must never share an id"
    assert len(journal.load(project)) == 2


def test_open_session_with_no_close_is_the_crash_flag(project):
    s = journal.record("session_open", "wiring the parser", ts="2026-01-01T00:00:00Z", author="x")
    journal.append(s, project)
    assert [r["id"] for r in journal.open_sessions(root=project)] == [s["id"]]
    journal.append(journal.record("session_close", "done", ts="2026-01-01T01:00:00Z",
                                  author="x", extra={"session": s["id"]}), project)
    assert journal.open_sessions(root=project) == []


def test_session_ids_do_not_collide_within_the_same_second():
    a = journal.new_session_id("2026-01-01T00:00:00Z", "one")
    b = journal.new_session_id("2026-01-01T00:00:00Z", "two")
    assert a != b and a.startswith("S-")


def test_superseded_ids():
    recs = [{"id": "D-1"}, {"id": "D-2", "supersedes": "D-1"}]
    assert journal.superseded_ids(recs) == {"D-1"}
