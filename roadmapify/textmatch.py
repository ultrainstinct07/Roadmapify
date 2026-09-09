"""Deterministic, dependency-free text matching.

Used by ``roadmap check`` to decide whether a proposed approach is one this
project already rejected, and by the branch↔task mapper to report how close a
branch name came to a task. Both are decisions where a WRONG match is much worse
than a missed one:

- A wrongly blocked proposal teaches an agent to route around the gate, which is
  worse than having no gate at all.
- A wrong branch→task mapping silently attributes work to the wrong task and
  corrupts every number derived from it.

So the bar is deliberately high, ties never resolve to a match, and every caller
prints the score and the runner-up so the decision is auditable.

Pure Python token Jaccard rather than rapidfuzz: one fewer dependency, and the
inputs here are short phrases where a character-level ratio mostly measures
shared English morphology rather than shared meaning.
"""

from __future__ import annotations

import re
import unicodedata

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Words that carry no discriminating signal in a phrase like "use redis for the
#: dedupe cache". Kept small on purpose — an aggressive stoplist starts deleting
#: the domain words that matter ("state", "file", "test" are all meaningful here).
_STOP = frozenset("""
a an the and or but if then than that this these those for of to in on at by with
from as is are was were be been being it its we you i they them their our your
use using used do does did make makes making just really very should would could
""".split())

#: Light suffix folding so "caching"/"cached"/"cache" and "files"/"file" agree.
#: Not a stemmer: it must never merge two words a reader would consider distinct.
_SUFFIXES = ("ings", "ing", "ies", "ed", "es", "s")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _fold(token: str) -> str:
    """Light, deliberately lossy stemming.

    Strips one inflectional suffix, then a trailing silent "e", so that
    cache / caching / cached and file / files / filing all agree. The final
    e-strip is what makes it consistent: without it "cache" folds to itself
    while "caching" folds to "cach", and the two never match — which is
    precisely the pair this tool sees most.

    It is lossy (status and statue both fold to "statu"), and that is an
    accepted trade: over-merging two words that never co-occur in a build plan
    costs nothing, while failing to match "caching" against "cache" costs a
    missed gate.
    """
    if len(token) > 4:
        for suf in _SUFFIXES:
            if token.endswith(suf) and len(token) - len(suf) >= 3:
                token = token[: -len(suf)]
                if suf == "ies":
                    token += "y"
                break
    if len(token) >= 4 and token.endswith("e"):
        token = token[:-1]
    return token


def tokens(text: str) -> set[str]:
    """Content tokens: lowercased, accent-folded, stopworded, lightly suffix-folded."""
    return {
        _fold(t) for t in _TOKEN_RE.findall(normalize(text))
        if t not in _STOP and len(t) > 1
    }


def similarity(a: str, b: str) -> float:
    """Symmetric Jaccard over content tokens, in [0, 1]. Empty on either side is 0."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def containment(query: str, candidate: str) -> float:
    """How much of the SHORTER side the two share, in [0, 1].

    Jaccard alone under-scores a short query against a long recorded reason —
    "use redis" vs a two-sentence rejection is barely 0.15 even when it is
    obviously the same idea. Containment catches that; the caller combines them
    so neither can fire alone.
    """
    ta, tb = tokens(query), tokens(candidate)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def coverage(phrase: str, text: str) -> float:
    """How much of ``phrase`` appears in ``text``, in [0, 1].

    Asymmetric on purpose, and the single most useful measure here. When someone
    proposes "add bidirectional sync so edits flow back", the signal that this is
    a rejected idea is that ALL the words of the recorded phrase "bidirectional
    sync" are present — not that the two strings are similar overall. Jaccard
    against the rejection's full text buries that signal under the paragraph of
    reasoning attached to it.
    """
    tp, tt = tokens(phrase), tokens(text)
    if not tp or not tt:
        return 0.0
    return len(tp & tt) / len(tp)


def score(query: str, candidate: str) -> float:
    """General similarity, used where neither side is a short label.

    ``max(jaccard, containment × 0.9)``: containment is discounted so a two-word
    query cannot fully saturate the score against a long paragraph, which is the
    main false-positive shape.
    """
    return max(similarity(query, candidate), containment(query, candidate) * 0.9)


def proposal_score(query: str, phrase: str, context: str = "") -> float:
    """Is ``query`` a re-proposal of the recorded ``phrase``?

    ``phrase`` is the short label of the recorded thing — a rejection's ``alt``,
    or a constraint's text. ``context`` is the surrounding prose (the reason, the
    parent decision) and only ever raises the score, never gates it.

    A multi-word phrase almost wholly present in the query is near-certainty, so
    it scores at 0.95 × coverage. A SINGLE-word phrase is discounted to 0.75 ×
    coverage: "json" appearing in a proposal is much weaker evidence that the
    proposal is the rejected idea, and single-word rejections are exactly where a
    false block would be most annoying and least explicable.
    """
    cov = coverage(phrase, query)
    weight = 0.95 if len(tokens(phrase)) >= 2 else 0.75
    # The weight applies to BOTH paths. Applying it only to coverage let the
    # general-similarity fallback route around the single-word discount
    # entirely — a one-word phrase is fully "contained" in any query mentioning
    # it, so containment scored 0.9 and the discount never took effect.
    return weight * max(cov, score(query, f"{phrase} {context}".strip()))


#: Nothing below this is ever BLOCKED — by `check`, by the mapper, by anything.
#: Chosen so a genuine restatement of a recorded idea clears it while a merely
#: topical overlap does not.
MATCH_FLOOR = 0.60

#: Above this but below MATCH_FLOOR, an item is SURFACED rather than blocked:
#: `check` exits 0 and prints it as "related, worth reading". Blocking on
#: near-certainty while surfacing on topical overlap is the whole reason the gate
#: stays trustworthy — a wrongly blocked proposal teaches an agent to route
#: around the gate, which is worse than having no gate at all.
RELATED_FLOOR = 0.22


def best(query: str, candidates: "list[tuple[str, str]]") -> "list[tuple[str, float]]":
    """Score ``(key, text)`` candidates against ``query``, best first.

    Returns every candidate with its score — including the ones below the floor —
    because callers print the runner-up and the margin so the user can see how
    close the call was.
    """
    scored = [(key, score(query, text)) for key, text in candidates]
    scored.sort(key=lambda kv: (-kv[1], kv[0]))
    return scored


def rank(query: str, candidates: "list[tuple[str, str, str]]") -> "list[tuple[str, float]]":
    """Rank ``(key, phrase, context)`` candidates by :func:`proposal_score`, best first."""
    scored = [(key, proposal_score(query, phrase, ctx)) for key, phrase, ctx in candidates]
    scored.sort(key=lambda kv: (-kv[1], kv[0]))
    return scored


# ── prohibitions ──────────────────────────────────────────────────────────────

#: Words that introduce a prohibition. Deliberately excludes a bare "not", which
#: is far too common in ordinary prose ("does not need to be fast") and would
#: turn half of every constraint into a banned phrase.
_PROHIBIT_RE = re.compile(
    r"\b(?:no|never|without|avoid|must\s+not|cannot|can't|do\s+not|don't|"
    r"forbidden|banned|off\s+the\s+table)\b[:\s]+(.{2,60})",
    re.IGNORECASE,
)

#: A prohibition runs to the first clause boundary. "no interactive OAuth — an
#: integration token in the env only" prohibits "interactive OAuth", not the
#: whole sentence including the thing it prescribes instead.
_CLAUSE_END_RE = re.compile(
    r"\s*(?:[,;.(]|—|--|\bbut\b|\binstead\b|\bunless\b|\bexcept\b)")


def _prohibitions(text: str) -> list[str]:
    """Every prohibition in ``text``, cleaned and cut at its clause boundary.

    Scanning resumes at the end of the CAPTURED phrase, not at the end of the
    60-character window the pattern matched. Greedy capture plus ``finditer``
    swallowed every later ban in the same sentence, so "No networkx (stdlib
    graphlib), no rapidfuzz (...), no tree-sitter" yielded only the first — the
    exact shape a dependency constraint is written in.
    """
    out: list[str] = []
    body = text or ""
    pos = 0
    while True:
        m = _PROHIBIT_RE.search(body, pos)
        if not m:
            return out
        phrase = m.group(1)
        # "must never contain: no subprocess, no importlib" — the marker was a
        # PREAMBLE and the real bans follow the colon. Skip past it and rescan,
        # rather than emitting "contain" as a ban and silently dropping the
        # first real one, which is how the commonest way of writing a list of
        # prohibitions left its first item unenforced.
        colon = phrase.find(":")
        if colon != -1 and colon < (_CLAUSE_END_RE.search(phrase).start()
                                    if _CLAUSE_END_RE.search(phrase) else len(phrase)):
            pos = m.start(1) + colon + 1
            continue
        cut = _CLAUSE_END_RE.search(phrase)
        end = m.start(1) + (cut.start() if cut else len(phrase))
        if cut:
            phrase = phrase[: cut.start()]
        phrase = phrase.strip(" -\u2013\u2014:\"'")
        if phrase:
            out.append(phrase)
        pos = max(end, m.start(1) + 1)


def prohibited_phrases(text: str) -> list[str]:
    """Extract the phrases a constraint forbids, e.g. "no interactive OAuth" -> ["interactive OAuth"].

    Constraints are written as prohibitions and prohibitions are the one shape
    where matching the WHOLE text is exactly wrong: the constraint's own words
    include the alternative it prescribes, which dilutes the forbidden phrase
    into noise. Pulling the phrase out lets it be matched the way a rejection's
    ``alt`` is — the difference between catching "use interactive OAuth" and
    shrugging at it.

    Only phrases with two or more content tokens are returned. A one-word
    prohibition ("no globals") is too blunt to block on and would fire on any
    proposal that merely mentions the word.
    """
    return [p for p in _prohibitions(text) if len(tokens(p)) >= 2]


#: A one-word ban is scored at the same discount ``proposal_score`` applies to a
#: single-word rejection alt. 0.75 still clears ``MATCH_FLOOR``, so an exact
#: mention blocks — but only for a term the corpus says is distinctive.
SINGLE_TERM_WEIGHT = 0.75


def prohibited_terms(text: str) -> list[str]:
    """The ONE-word bans a constraint states: "no networkx" -> ["networkx"].

    :func:`prohibited_phrases` drops these, because a one-word prohibition is
    normally too blunt to block on — "no globals" would fire on any proposal
    that merely mentions the word. But every ban that actually matters in a
    dependency constraint is one word, and they were being discarded: this
    project's own ``why`` reads "No networkx (stdlib graphlib), no rapidfuzz
    (pure-Python Jaccard), no tree-sitter", and only ``tree-sitter`` survived —
    by the accident of tokenising as two words.

    So the terms are returned separately and the CALLER decides, by passing
    :func:`constraint_score` a ``distinctive`` set. See :func:`rare_tokens`.
    """
    return [p for p in _prohibitions(text) if len(tokens(p)) == 1]


def constraint_score(query: str, text: str, why: str = "", *,
                     distinctive: "frozenset[str] | set[str]" = frozenset(),
                     ) -> "tuple[float, str]":
    """Score a proposal against a constraint. Returns ``(score, matched_clause)``.

    Tries each prohibited phrase first — that is where the signal is — and falls
    back to the constraint's full text. Prohibitions are scanned in BOTH the
    constraint and its ``why``, because people routinely state the rule
    abstractly ("one conditional dependency only") and name the specific banned
    things in the reason ("no networkx, no rapidfuzz").

    The matched clause is returned so the output can say WHICH part of the
    constraint the proposal collides with, rather than asserting a violation and
    leaving the reader to guess.
    """
    best_score, best_clause = proposal_score(query, text, why), text
    for phrase in prohibited_phrases(text) + prohibited_phrases(why):
        s = coverage(phrase, query) * 0.95
        if s > best_score:
            best_score, best_clause = s, phrase
    # One-word bans, but ONLY for terms the corpus says are distinctive. Without
    # that gate this is the blunt instrument prohibited_phrases refuses to be;
    # with it, "networkx" blocks and "globals" cannot, because a word this
    # project uses elsewhere is a category, not a named artefact.
    for term in prohibited_terms(text) + prohibited_terms(why):
        if not (tokens(term) & set(distinctive)):
            continue
        s = coverage(term, query) * SINGLE_TERM_WEIGHT
        if s > best_score:
            best_score, best_clause = s, term
    return best_score, best_clause


# ── corpus-relative stopwords ─────────────────────────────────────────────────

#: A token in more than this fraction of the corpus carries no discriminating
#: signal *within this project*. In a roadmapify roadmap, "roadmap" is in half
#: the records; matching on it makes every proposal look related to everything.
_UBIQUITOUS_FRACTION = 0.4

#: Below this many records the document-frequency estimate is noise — three
#: records that happen to share a word do not make it a stopword.
_MIN_CORPUS = 6


def corpus_stopwords(texts: "list[str]") -> set[str]:
    """Tokens so common in THIS corpus that matching on them means nothing.

    The global stoplist can only hold words that are uninformative in English.
    The words that actually ruin matching are the project's own vocabulary —
    "roadmap", "task", "graph" — and those are only discoverable from the corpus
    itself.
    """
    if len(texts) < _MIN_CORPUS:
        return set()
    df: dict[str, int] = {}
    for t in texts:
        for tok in tokens(t):
            df[tok] = df.get(tok, 0) + 1
    cut = len(texts) * _UBIQUITOUS_FRACTION
    return {tok for tok, n in df.items() if n > cut}



#: A term this rare is a NAMED artefact, not a category. "networkx" appears only
#: where it is banned; "state", "file" and "block" recur all over a build plan.
#: That difference is the whole reason a one-word ban can be trusted at all.
_RARE_DF = 1


def rare_tokens(texts: "list[str]", max_df: int = _RARE_DF) -> set[str]:
    """Tokens appearing in at most ``max_df`` documents — this corpus's proper nouns.

    The mirror of :func:`corpus_stopwords`. That drops the words a project says
    constantly; this keeps the ones it says exactly once, which is what makes a
    single-word prohibition safe to enforce (see :func:`prohibited_terms`).

    Below ``_MIN_CORPUS`` documents every word looks rare, so nothing is
    returned: in a two-constraint project this would otherwise make every
    one-word ban blocking, which is precisely the over-firing it exists to
    avoid.
    """
    if len(texts) < _MIN_CORPUS:
        return set()
    df: dict[str, int] = {}
    for t in texts:
        for tok in tokens(t):
            df[tok] = df.get(tok, 0) + 1
    return {tok for tok, n in df.items() if n <= max_df}


#: A ban stops being enforceable only when the term is genuinely this project's
#: ordinary vocabulary. Well under _UBIQUITOUS_FRACTION, because a word does not
#: have to be everywhere to be a category rather than a name.
_DISTINCTIVE_FRACTION = 0.25


def distinctive_terms(texts: "list[str]") -> set[str]:
    """Terms a one-word prohibition may be enforced on.

    Not ``rare_tokens(texts)``: an exact document-frequency of 1 means that
    *citing* a ban anywhere else disables it. That is not hypothetical — the
    moment this project recorded a rejection whose reason read "C-fycw forbids
    tree-sitter and networkx", ``networkx`` reached two documents and stopped
    blocking. A rule you break by explaining it is not a rule.

    So the test is relative: distinctive means well below the fraction of the
    corpus at which a word is this project's ordinary vocabulary. A handful of
    references to a ban reinforce it; only genuine everyday use retires it.
    """
    if len(texts) < _MIN_CORPUS:
        return set()
    return rare_tokens(texts, max_df=max(1, int(len(texts) * _DISTINCTIVE_FRACTION)))

def shared(a: str, b: str) -> int:
    """How many content tokens two texts have in common.

    A score alone is a bad surfacing rule: one shared word out of a four-word
    phrase scores 0.24, which sits just above any floor low enough to be useful,
    so a single incidental overlap makes every proposal look related to
    everything. Requiring a MINIMUM SHARED COUNT is scale-free — it does not need
    retuning when phrases get longer or the corpus grows.
    """
    return len(tokens(a) & tokens(b))


#: Surfacing needs at least this many shared content tokens, measured against
#: the candidate's FULL text (phrase plus its reason), not the phrase alone —
#: constraints routinely state the rule abstractly and name the specific thing
#: in the reason.
MIN_SHARED_TO_SURFACE = 2


def strip_tokens(text: str, drop: set[str]) -> str:
    """Rebuild ``text`` as the tokens that survive ``drop``. Order is stable."""
    if not drop:
        return text
    kept = [t for t in _TOKEN_RE.findall(normalize(text))
            if _fold(t) not in drop and t not in _STOP and len(t) > 1]
    return " ".join(kept)


# ── typo suggestions ──────────────────────────────────────────────────────────

def edit_distance(a: str, b: str, cap: int = 3) -> int:
    """Levenshtein distance, giving up once it exceeds ``cap``.

    Token Jaccard is the right tool for "is this the same idea"; it is the wrong
    tool for "did you mean `check`", because a transposed pair shares no tokens
    at all. Short-identifier typos need character edits, so they get their own
    function rather than a knob on the semantic matcher.
    """
    a, b = a.lower(), b.lower()
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def nearest(word: str, candidates, max_distance: int = 2) -> "str | None":
    """The closest candidate within ``max_distance`` edits, or None. Ties resolve
    alphabetically so the suggestion is stable run to run."""
    scored = sorted(((edit_distance(word, c, max_distance), c) for c in candidates))
    if scored and scored[0][0] <= max_distance:
        return scored[0][1]
    return None
