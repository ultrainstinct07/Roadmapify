"""Shared, structured policy result for CLI, MCP and context consumers."""
from roadmapify import journal, textmatch
import re


def check(query, records, plan=None):
    memory = journal.memory_state(records)
    rejections = memory["rejections"]
    constraints = [r for r in memory["records"] if r["kind"] == "constraint"]
    # Words that appear in most records — this project's own vocabulary —
    # inflate every score and make every proposal look related to everything.
    # They are only discoverable from the corpus, so drop them from the query
    # before scoring anything.
    corpus = [f"{x['alt']} {x.get('reason', '')}" for x in rejections] + \
             [f"{r['text']} {r.get('why', '')}" for r in constraints]
    ubiquitous = textmatch.corpus_stopwords(corpus)
    approach = textmatch.strip_tokens(query, ubiquitous) or query

    # The mirror of `ubiquitous`: terms this project names rarely. A
    # constraint's one-word bans ("no networkx") are only enforceable for these,
    # because a word used elsewhere is a category, not an artefact.
    #
    # And never a word the PLAN builds with. Corpus frequency cannot separate
    # "networkx" from "branch" — in a sixty-record corpus both are rare — so
    # C-dzha's "no branch, checkout, ..." blocked `bind a branch to a task, and
    # explain the mapping`, which is this CLI's own shipped description of
    # `roadmap map`. The plan's labels and intents say what we are building;
    # nothing in them can be banned by a single word.
    distinctive = textmatch.distinctive_terms(corpus)
    if plan is not None:
        distinctive -= textmatch.vocabulary(
            [plan.goal.label, plan.goal.statement or "", plan.goal.why]
            + [f"{x.label} {x.intent or ''} {x.ships or ''} "
               f"{x.exit_criteria or ''} {x.demo or ''}" for x in plan.phases]
            + [f"{t.label} {t.intent or ''}" for t in plan.tasks])

    rej_scored = textmatch.rank(
        approach,
        [(x["id"], x["alt"], f"{x.get('reason', '')} {x['parent_text']}") for x in rejections],
    )
    con_hits = {r["id"]: textmatch.constraint_score(approach, r["text"], r.get("why", ""),
                                                    distinctive=distinctive)
                for r in constraints}
    con_scored = sorted(((k, v[0]) for k, v in con_hits.items()), key=lambda kv: -kv[1])
    by_id = {x["id"]: x for x in rejections}
    by_id.update({r["id"]: r for r in constraints})


    # Only simple, explicit avoidance clauses are classified as such. Mixed
    # proposals are still checked clause by clause, never cleared wholesale.
    clauses = re.split(r"[.;]|\b(?:but|however|then)\b|\band\s+(?=use|add|install|enable|run|introduce|execute)", query, flags=re.I)
    positive = [c for c in clauses if c.strip() and not re.match(
        r"^\s*(?:avoid|do not use|don't use|never use|without)\b", c, re.I)]
    if len(positive) != len([c for c in clauses if c.strip()]):
        if positive:
            result = check("; ".join(positive), records, plan)
            return {**result, "approach": query}
        return {"approach": query, "verdict": "CLEAR", "exit_code": 0,
                "hit": None, "score": 0, "clause": "", "related": [],
                "interpretation": "explicit avoidance; rules remain in force"}
    for scored, verdict, code in ((rej_scored, "REJECTED", 3),
                                  (con_scored, "VIOLATES_CONSTRAINT", 4)):
        if scored and scored[0][1] >= textmatch.MATCH_FLOOR:
            key, score = scored[0]
            return {"approach": query, "verdict": verdict, "exit_code": code,
                    "hit": by_id[key], "score": score,
                    "clause": con_hits[key][1] if key in con_hits else "", "related": []}
    full = {x["id"]: f"{x['alt']} {x.get('reason', '')} {x['parent_text']}" for x in rejections}
    full.update({r["id"]: f"{r['text']} {r.get('why', '')}" for r in constraints})
    related = [(by_id[k], score) for k, score in sorted(
        rej_scored + con_scored, key=lambda kv: -kv[1])
        if score >= textmatch.RELATED_FLOOR and
        textmatch.shared(approach, full[k]) >= textmatch.MIN_SHARED_TO_SURFACE][:3]
    return {"approach": query, "verdict": "CLEAR", "exit_code": 0,
            "hit": None, "score": 0, "clause": "", "related": related}
