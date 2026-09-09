"""The gate's matching rules.

`roadmap check` false positives are the most dangerous failure mode in the whole
tool: a wrongly blocked legitimate proposal teaches an agent to route around the
gate, which leaves it worse off than having no gate at all. So the
must-not-block cases below matter more than the must-block ones.
"""

from __future__ import annotations

import pytest

from roadmapify import textmatch as tm

REJECTED_ALT = "bidirectional sync"
REJECTED_CTX = ("conflict resolution across two schemas is its own product "
                "sync is one-way, Notion to markdown, never back")


@pytest.mark.parametrize("proposal", [
    "add bidirectional sync so edits in the repo flow back to Notion",
    "make the sync bidirectional",
    "BIDIRECTIONAL SYNC, both directions",
    "bidirectional syncing between the two",
])
def test_a_restatement_of_a_rejected_idea_is_blocked(proposal):
    assert tm.proposal_score(proposal, REJECTED_ALT, REJECTED_CTX) >= tm.MATCH_FLOOR


@pytest.mark.parametrize("proposal", [
    "cache page bodies on disk so a re-sync is incremental",
    "add a --verbose flag",
    "write the sync results to a json file",
    "document the sync direction in the README",
])
def test_merely_related_proposals_are_not_blocked(proposal):
    assert tm.proposal_score(proposal, REJECTED_ALT, REJECTED_CTX) < tm.MATCH_FLOOR


def test_a_single_word_rejection_scores_below_a_multi_word_one():
    """A one-word rejection is blunt — it fires on any proposal mentioning the
    word — so it is discounted. It still blocks a direct re-proposal, which is
    correct: if the project rejected "redis", proposing redis IS the rejected
    thing. The discount buys margin, not immunity."""
    multi = tm.proposal_score("use redis for the cache", "redis cache", "")
    single = tm.proposal_score("use redis for the cache", "redis", "")
    assert single < multi
    assert single == pytest.approx(0.75, abs=0.01)
    assert single >= tm.MATCH_FLOOR, "a direct re-proposal must still be caught"


def test_coverage_is_asymmetric():
    assert tm.coverage("bidirectional sync", "add bidirectional sync now") == 1.0
    assert tm.coverage("add bidirectional sync now", "bidirectional sync") < 1.0


def test_tokens_fold_morphology_but_not_meaning():
    assert tm.tokens("caching") == tm.tokens("cache") == tm.tokens("cached")
    assert tm.tokens("files") == tm.tokens("file")
    assert tm.tokens("sync") != tm.tokens("syntax")
    assert tm.tokens("file") != tm.tokens("filter")
    assert tm.tokens("the and or of") == set(), "stopwords carry no signal"


def test_matching_is_accent_and_case_insensitive():
    assert tm.coverage("café sync", "CAFE SYNC now") == 1.0


# ── prohibitions ──────────────────────────────────────────────────────────────

def test_prohibited_phrase_extraction_stops_at_the_clause_boundary():
    """A constraint's own words include the alternative it PRESCRIBES; matching
    the whole sentence dilutes the forbidden phrase into noise."""
    assert tm.prohibited_phrases(
        "no interactive OAuth — an integration token in the env only"
    ) == ["interactive OAuth"]
    assert tm.prohibited_phrases("must work with no API key") == ["API key"]
    assert tm.prohibited_phrases("never use a background daemon, use cron instead") == [
        "use a background daemon"]


def test_bare_not_is_not_a_prohibition_marker():
    """'not' is far too common in ordinary prose to treat as a ban."""
    assert tm.prohibited_phrases("this does not need to be fast") == []


def test_single_word_prohibitions_are_dropped():
    assert tm.prohibited_phrases("no globals") == []


def test_every_ban_in_one_sentence_is_extracted_not_just_the_first():
    """A dependency constraint states its bans in a series. Greedy capture plus
    finditer swallowed every ban after the first, so the exact sentence this
    project uses to forbid three libraries only ever forbade one."""
    why = ("graphify declares 30 required deps because it parses source code; "
           "we parse one TOML file and some git output. No networkx (stdlib "
           "graphlib), no rapidfuzz (pure-Python Jaccard), no tree-sitter")
    assert tm.prohibited_terms(why) == ["networkx", "rapidfuzz"]
    assert tm.prohibited_phrases(why) == ["tree-sitter"]


def test_a_one_word_ban_blocks_only_when_the_corpus_calls_the_term_distinctive():
    """"no networkx" has to be enforceable — it is how every dependency
    constraint is actually written — but "no globals" must not be, or the gate
    fires on any proposal that merely says the word. Corpus rarity is the whole
    difference: a term named once is an artefact, a term used throughout is a
    category."""
    text = "base install has exactly one conditional dependency"
    why = "no networkx (stdlib graphlib), no globals"
    proposal = "use networkx for the task DAG"

    score, _ = tm.constraint_score(proposal, text, why)
    assert score < tm.MATCH_FLOOR, "with no corpus evidence a one-word ban must stay inert"

    score, clause = tm.constraint_score(proposal, text, why,
                                        distinctive={"networkx"})
    assert score >= tm.MATCH_FLOOR
    assert clause == "networkx"

    # The same constraint, the same mechanism, a term the corpus does not
    # single out: still no block.
    score, _ = tm.constraint_score("avoid globals in the render layer", text, why,
                                   distinctive={"networkx"})
    assert score < tm.MATCH_FLOOR


def test_rare_tokens_needs_a_real_corpus_before_it_calls_anything_distinctive():
    """In a two-record project every word appears once. Trusting that would make
    every one-word ban blocking on day one, which is the over-firing the whole
    distinctive gate exists to prevent."""
    assert tm.rare_tokens(["no networkx", "one dependency only"]) == set()
    corpus = ["no networkx here", "roadmap plan tasks", "roadmap phases",
              "roadmap memory", "roadmap journal", "roadmap graph", "roadmap brief"]
    rare = tm.rare_tokens(corpus)
    assert "networkx" in rare
    assert "roadmap" not in rare, "a word in most records is the opposite of distinctive"


def test_constraint_score_reports_which_clause_matched():
    text = "no interactive OAuth — an integration token in the env only"
    score, clause = tm.constraint_score(
        "use interactive OAuth so the user logs in through a browser", text,
        "it has to run from cron on a headless box")
    assert score >= tm.MATCH_FLOOR
    assert clause == "interactive OAuth"


@pytest.mark.parametrize("proposal", [
    "read the integration token from the env var",
    "document why we do not use oauth in the README",
])
def test_constraint_compatible_proposals_are_not_blocked(proposal):
    text = "no interactive OAuth — an integration token in the env only"
    score, _ = tm.constraint_score(proposal, text, "it runs from cron")
    assert score < tm.MATCH_FLOOR, proposal


def test_related_floor_sits_below_the_block_floor():
    """Block on near-certainty, surface on overlap. Both floors have to exist."""
    assert 0 < tm.RELATED_FLOOR < tm.MATCH_FLOOR < 1.0


def test_rank_is_deterministic_and_sorted():
    cands = [("a", "one thing", ""), ("b", "another thing entirely", "")]
    assert tm.rank("one thing", cands) == tm.rank("one thing", cands)
    assert tm.rank("one thing", cands)[0][0] == "a"


def test_empty_inputs_never_match():
    assert tm.proposal_score("", "anything", "") == 0.0
    assert tm.proposal_score("anything", "", "") == 0.0
    assert tm.coverage("", "") == 0.0


def test_nearest_catches_a_transposition_that_token_matching_cannot():
    """`chekc` and `check` share no tokens at all — this is why typo suggestions
    use edit distance rather than the semantic matcher."""
    assert tm.similarity("chekc", "check") == 0.0
    assert tm.nearest("chekc", ["check", "note", "init"]) == "check"
    assert tm.nearest("noet", ["check", "note", "init"]) == "note"
    assert tm.nearest("completely-different", ["check", "note"]) is None


def test_edit_distance_gives_up_past_the_cap():
    assert tm.edit_distance("abc", "abc") == 0
    assert tm.edit_distance("abc", "abd") == 1
    assert tm.edit_distance("abc", "xyzxyzxyz", cap=3) == 4


def test_nearest_ties_resolve_stably():
    assert tm.nearest("aa", ["ab", "ac"]) == tm.nearest("aa", ["ac", "ab"])


def test_shared_token_count_is_the_surfacing_rule():
    """One shared word out of four scores ~0.24 — just above any usefully low
    floor — so a score alone makes everything look related to everything."""
    q = "add a --json flag to roadmap next for scripting"
    noise = "a status field in roadmap.toml"
    assert tm.proposal_score(q, noise, "") >= tm.RELATED_FLOOR, "score alone would surface it"
    assert tm.shared(q, noise) < tm.MIN_SHARED_TO_SURFACE, "the count rule rejects it"


def test_shared_counts_against_the_full_text_not_just_the_phrase():
    """Constraints state the rule abstractly and name the banned thing in the why."""
    q = "pull in networkx for the dependency graph"
    text = "base install has exactly one conditional dependency"
    why = "No networkx (stdlib graphlib), no rapidfuzz, no tree-sitter"
    assert tm.shared(q, text) < tm.MIN_SHARED_TO_SURFACE
    assert tm.shared(q, f"{text} {why}") >= tm.MIN_SHARED_TO_SURFACE


def test_prohibitions_are_read_from_the_why_as_well():
    score, clause = tm.constraint_score(
        "add a background daemon that polls every minute",
        "keep the runtime simple",
        "never use a background daemon; cron is enough")
    assert score >= tm.MATCH_FLOOR
    assert "background daemon" in clause


def test_corpus_stopwords_need_a_real_corpus():
    assert tm.corpus_stopwords(["roadmap a", "roadmap b"]) == set()
    texts = [f"roadmap thing {i}" for i in range(10)]
    assert "roadmap" in tm.corpus_stopwords(texts)
    assert tm.strip_tokens("roadmap next flag", {"roadmap"}) == "next flag"


def test_citing_a_ban_elsewhere_does_not_disable_the_ban():
    """A rule you break by explaining it is not a rule. With an exact
    document-frequency test, recording a rejection whose reason read "C-fycw
    forbids tree-sitter and networkx" pushed networkx to two documents and
    silently stopped it blocking — which is how this was found."""
    corpus = [
        "base install has exactly one conditional dependency. No networkx, no rapidfuzz",
        "tree-sitter code graph: C-fycw forbids tree-sitter and networkx, so we project by hand",
        "the plan file lives at the project root",
        "records are content hashed so a union merge stays safe",
        "every render function takes now as an argument",
        "the brief is capped so an agent can afford to read it",
        "status is derived from evidence, never stored",
        "the block is written between markers so another tool survives",
    ]
    assert "networkx" not in tm.rare_tokens(corpus), "it is in two documents, so not rare"
    assert "networkx" in tm.distinctive_terms(corpus), "but it is still a name, not vocabulary"

    score, clause = tm.constraint_score(
        "use networkx for the task DAG",
        "base install has exactly one conditional dependency",
        "no networkx (stdlib graphlib)",
        distinctive=tm.distinctive_terms(corpus))
    assert score >= tm.MATCH_FLOOR and clause == "networkx"


def test_a_term_this_project_says_constantly_is_not_enforceable_as_a_one_word_ban():
    """The other half of the same rule: everyday vocabulary must not block, or
    the gate fires on any proposal that merely mentions it."""
    corpus = [f"the roadmap tracks {w}" for w in
              ("phases", "tasks", "memory", "journal", "graph", "briefs", "plans")]
    assert "roadmap" not in tm.distinctive_terms(corpus)


def test_a_prohibition_preamble_does_not_swallow_the_first_ban_in_the_list():
    """"must never contain: no subprocess, no importlib" is the commonest way a
    constraint states a list of bans, and the marker before the colon is a
    preamble, not the ban. Capturing across it turned the first item into the
    two-token phrase "contain: no subprocess" — which scored 0.475, below the
    floor — so the FIRST ban in every such list was silently unenforced while
    the rest blocked. The gate answered two different ways about one mechanism."""
    text = ("the execution primitives verify must never contain: "
            "no subprocess, no importlib, no popen, no eval")
    assert tm.prohibited_terms(text) == ["subprocess", "importlib", "popen", "eval"]
    assert "contain" not in " ".join(tm.prohibited_phrases(text)), \
        "the preamble verb must never itself become a ban"


def test_a_colon_free_prohibition_is_unchanged():
    """The preamble rule must not disturb the ordinary shapes."""
    assert tm.prohibited_phrases(
        "no interactive OAuth — an integration token in the env only"
    ) == ["interactive OAuth"]
    assert tm.prohibited_phrases("must work with no API key") == ["API key"]


def test_one_marker_governs_a_whole_enumeration_of_bans():
    """"no branch, checkout, switch, rebase, merge, push, reset" is one marker
    over seven bans. Collapsing it to the first item made the gate wrong in both
    directions at once: it banned the ordinary noun "branch" — blocking this
    CLI's own description of `roadmap map` — while letting `git push` and
    `git checkout`, the things the rule exists to forbid, through at exit 0."""
    text = ("never write a git ref — no branch, checkout, switch, rebase, "
            "merge, push, reset, or git config write")
    assert tm.prohibited_terms(text) == [
        "branch", "checkout", "switch", "rebase", "merge", "push", "reset"]
    assert "git config write" in tm.prohibited_phrases(text)


def test_an_enumeration_stops_at_the_alternative_a_constraint_prescribes():
    """A constraint's own words include the thing it wants instead. "use cron
    instead" is the prescription, not a second ban, and taking it would forbid
    the remedy."""
    assert tm.prohibited_phrases(
        "never use a background daemon, use cron instead") == [
        "use a background daemon"]


def test_a_predicate_nominative_is_not_a_prohibition():
    """"the LLM pass is a refinement, never a requirement" bans nothing. Read as
    a ban it made "requirement" an enforceable one-word prohibition, which would
    block any proposal that used the word."""
    assert tm.prohibited_terms(
        "the LLM pass is a refinement, never a requirement") == []


def test_a_colon_keeps_a_head_that_says_something_and_drops_one_that_does_not():
    """"must never contain:" is a preamble. "never executes a deliverable:" is
    the rule itself, and dropping it left the exact attack that constraint
    exists to stop scoring below the blocking floor."""
    assert "executes a deliverable" in tm.prohibited_phrases(
        "roadmap verify never executes a deliverable: no subprocess, no shell")
    assert "contain" not in " ".join(tm.prohibited_phrases(
        "the primitives must never contain: no subprocess, no eval"))


def test_the_vocabulary_a_project_builds_with_excludes_what_it_forbids():
    """A one-word ban is only safe on a term foreign to the project. Corpus
    frequency cannot separate "networkx" from "branch" — in a small corpus both
    are rare — so the plan's own labels decide. And a plan that NAMES a ban
    ("stdlib graphlib; no networkx") must not thereby retire it."""
    vocab = tm.vocabulary([
        "Read-only git plumbing and branch mapping",
        "Derive status, ready tasks and the critical path: stdlib graphlib; no networkx",
    ])
    assert "branch" in vocab, "the project's own subject matter"
    assert "networkx" not in vocab, "naming a ban must not retire it"


def test_a_long_constraint_does_not_swallow_a_short_unrelated_proposal():
    """Containment divides by the shorter side, so a sixty-token constraint
    contains a short proposal's whole vocabulary almost by construction. A flat
    0.9 discount left "run git for-each-ref to list branches read-only" scoring
    0.85 against a constraint that merely uses the words git, ref and branch
    somewhere — a legitimate read-only proposal blocked by a rule about writes."""
    long_constraint = (
        "roadmapify never runs a git verb that writes: no git checkout, no git "
        "rebase, no git push, no git reset, no git config, no ref update, no ref "
        "deletion; and it never wraps git in a library: no GitPython, no dulwich")
    assert tm.score("run git for-each-ref to list branches read-only",
                    long_constraint) < tm.MATCH_FLOOR
    assert tm.score("run git push after recording the merge",
                    long_constraint) >= tm.RELATED_FLOOR


def test_containment_still_catches_a_short_restatement_of_a_short_record():
    """The discount must not retire containment itself: a short proposal against
    a similarly short record is the case it exists for."""
    assert tm.score("use redis for the cache",
                    "redis as the dedupe cache") >= tm.MATCH_FLOOR
