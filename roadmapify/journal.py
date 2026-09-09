"""The memory log: append-only JSONL of decisions, rejections, constraints, risks and sessions.

One JSON object per line in ``roadmap-out/journal.jsonl``. The file is TRACKED
and append-only, and ``.gitattributes`` gives it git's built-in ``merge=union``,
so two branches that each record a decision merge without a conflict and without
a custom merge driver.

Union merge is only correct because ids are CONTENT-HASHED. A monotonic counter
(``D-01``, ``D-02``) collides the moment two branches each record a decision, and
the union would silently produce two different records with the same id. A UUID
would not collide but a human has to type these into ``roadmap why``, so the id
is a short base32 digest instead.

Format note: JSONL, not graphify's markdown-with-YAML-frontmatter. graphify's
``reflect.parse_memory_doc`` hand-rolls a YAML subset and ``ingest._yaml_str``
spends ~60 lines escaping hostile strings out of injecting sibling keys — all of
which exists because graphify's memory docs must round-trip through a markdown
extractor. We have no such requirement, so ``json.dumps`` removes that machinery
and an entire injection class. The lesson we DO take is the id-collision one, in
its stronger content-hash form.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
from datetime import datetime, timezone
from pathlib import Path

from roadmapify.paths import JOURNAL_FILENAME, append_jsonl, out_path, read_jsonl

KINDS = (
    "decision",
    "constraint",
    "risk",
    "note",
    "question",
    "session_open",
    "session_close",
)

#: Which kinds get which id prefix. Typed prefixes let a human read an id and
#: know what it is, and let `roadmap why` route without a lookup.
_PREFIX = {
    "decision": "D",
    "constraint": "C",
    "risk": "R",
    "note": "N",
    "question": "Q",
    "session_open": "S",
    "session_close": "S",
}

#: Records sourced from someone else's commit trailers. See `trust` below.
TRUST_LOCAL = "local"
TRUST_FOREIGN = "foreign"

# Control characters and ANSI escape sequences are stripped ON INGEST, not at
# render time. Journal text is injected into an agent's context via BRIEF.md, and
# a record carrying a cursor-control sequence could hide its own contents from a
# human reading the file while remaining visible to the model.
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

MAX_TEXT = 4000


def sanitize(text: str, *, limit: int = MAX_TEXT) -> str:
    """Strip ANSI/control characters, collapse whitespace runs, and cap length."""
    if not isinstance(text, str):
        text = str(text)
    text = _ANSI_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_id(kind: str, text: str, ts: str, author: str) -> str:
    """Typed prefix plus a 130-bit base32 digest of canonical record content.

    The record builder includes rationale, relationships and rejections in the
    canonical text. Existing short IDs remain readable; ambiguous legacy
    collisions are preserved and quarantined by load().
    """
    prefix = _PREFIX.get(kind, "N")
    digest = hashlib.sha256(f"{text}|{ts}|{author}".encode("utf-8")).digest()
    body = base64.b32encode(digest).decode("ascii").lower().rstrip("=")
    return f"{prefix}-{body[:26]}"


def _author() -> str:
    """Who is recording. Not identity — provenance, used for the trust boundary."""
    return os.environ.get("ROADMAP_AUTHOR") or os.environ.get("USER") or "local"


def parse_rejection(spec: str) -> dict:
    """Parse ``"<alternative>: <why not>"`` into a rejection dict.

    The colon form is deliberate: it makes the reason mandatory-by-habit without
    making it a required flag. A bare string with no colon is still accepted —
    an unexplained rejection beats an unrecorded one — and lands with an empty
    reason that `roadmap doctor` can nag about later.
    """
    spec = sanitize(spec, limit=1000)
    alt, sep, reason = spec.partition(":")
    if not sep:
        return {"alt": alt.strip(), "reason": ""}
    return {"alt": alt.strip(), "reason": reason.strip()}


def record(
    kind: str,
    text: str,
    *,
    ts: "str | None" = None,
    author: "str | None" = None,
    why: "str | None" = None,
    rejected: "list[str] | list[dict] | None" = None,
    about: "list[str] | None" = None,
    supersedes: "str | None" = None,
    review_by: "str | None" = None,
    reversal_trigger: "str | None" = None,
    trigger_kind: "str | None" = None,
    commit: "str | None" = None,
    branch: "str | None" = None,
    trust: str = TRUST_LOCAL,
    extra: "dict | None" = None,
) -> dict:
    """Build one journal record. Pure — no clock read unless ``ts`` is omitted, no I/O.

    Purity matters because every rendering test fixes ``now`` and asserts
    byte-stable output; a hidden ``datetime.now()`` in here would make the whole
    derived layer untestable.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    ts = ts or now_iso()
    author = author or _author()
    text = sanitize(text)
    if not text:
        raise ValueError("record text is empty after sanitisation")

    rej: list[dict] = []
    for item in rejected or []:
        rej.append(parse_rejection(item) if isinstance(item, str) else dict(item))

    rec: dict = {
        "id": make_id(kind, text, ts, author),
        "ts": ts,
        "kind": kind,
        "text": text,
        "author": author,
        "trust": trust,
    }
    if why:
        rec["why"] = sanitize(why)
    if rej:
        rec["rejected"] = rej
    if about:
        rec["about"] = [sanitize(a, limit=64) for a in about if a]
    if supersedes:
        rec["supersedes"] = supersedes
    if review_by:
        rec["review_by"] = review_by
    if reversal_trigger:
        rec["reversal_trigger"] = sanitize(reversal_trigger, limit=500)
        rec["trigger_kind"] = trigger_kind or "manual"
    if commit:
        rec["commit"] = commit
    if branch:
        rec["branch"] = sanitize(branch, limit=255)
    for k, v in (extra or {}).items():
        if v is not None and k not in rec:
            rec[k] = v
    payload = json.dumps({k: v for k, v in rec.items() if k != "id"},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    rec["id"] = make_id(kind, payload, ts, author)
    return rec


def journal_path(root: "str | Path | None" = None) -> Path:
    return out_path(JOURNAL_FILENAME, root=root)


def load(root: "str | Path | None" = None) -> list[dict]:
    """Every journal record, deduped on id and stably ordered by (ts, id).

    Dedup is what makes ``merge=union`` safe: a union merge of two branches that
    both contain a shared ancestor's lines yields duplicates, and content-hashed
    ids make those duplicates provably identical.
    """
    groups: dict[str, dict[str, dict]] = {}
    for rec in read_jsonl(journal_path(root)):
        rid = rec.get("id")
        if not isinstance(rid, str) or rec.get("kind") not in KINDS:
            continue
        payload = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        groups.setdefault(rid, {})[payload] = rec
    result = []
    for rid, variants in groups.items():
        for payload, rec in sorted(variants.items()):
            if len(variants) > 1:
                # Preserve both observations, quarantine ambiguous authority.
                digest = hashlib.sha256(payload.encode()).hexdigest()[:32]
                rec = {**rec, "id": rid + "-" + digest,
                       "original_id": rid, "trust": "conflict"}
            result.append(rec)
    return sorted(result, key=lambda r: (r.get("ts", ""), r["id"]))


def append(rec: dict, root: "str | Path | None" = None) -> dict:
    """Append a record, extending its id on the (rare) collision with a different record."""
    path = journal_path(root)
    existing = {r.get("id"): r for r in read_jsonl(path)}
    rid = rec["id"]
    if rid in existing and existing[rid] != rec:
        digest = hashlib.sha256(json.dumps(
            rec, sort_keys=True, separators=(",", ":")).encode()).digest()
        body = base64.b32encode(digest).decode("ascii").lower().rstrip("=")
        for n in range(5, len(body)):
            candidate = f"{rid[:2]}{body[:n]}"
            if candidate not in existing:
                rec = {**rec, "id": candidate}
                break
    append_jsonl(path, rec)
    return rec


def rejections(records: "list[dict] | None" = None,
               root: "str | Path | None" = None) -> list[dict]:
    """Flatten every ``rejected`` entry into a first-class rejection with its own id.

    A rejection buried as a field can only be printed. A rejection with an id can
    be cited, path-walked, matched and ENFORCED — which is the entire mechanism
    behind ``roadmap check``. ``X-<parent-body>-<n>`` is stable under
    re-projection because it derives only from the parent id and the ordinal.
    """
    recs = load(root) if records is None else records
    out: list[dict] = []
    for parent in recs:
        for n, r in enumerate(parent.get("rejected") or [], start=1):
            out.append({
                "id": f"X-{parent['id'].split('-', 1)[-1]}-{n}",
                "alt": r.get("alt", ""),
                "reason": r.get("reason", ""),
                "parent": parent["id"],
                "parent_text": parent.get("text", ""),
                "ts": parent.get("ts", ""),
                "trust": parent.get("trust", TRUST_LOCAL),
                "author": parent.get("author", ""),
            })
    return out


def superseded_ids(records: list[dict]) -> set[str]:
    """Ids that some later record explicitly reversed."""
    return memory_state(records)["superseded"]


def open_sessions(records: "list[dict] | None" = None,
                  root: "str | Path | None" = None) -> list[dict]:
    """Session_open records with no matching session_close.

    This IS the crash flag. SIGKILL, a closed tab, a machine restart and a
    mid-task compaction all leave one behind, and none of them get the chance to
    write anything else.
    """
    recs = load(root) if records is None else records
    admitted = memory_state(recs)["admitted"]
    closed = {r.get("session") for r in recs if r.get("kind") == "session_close" and r["id"] in admitted}
    return [r for r in recs if r.get("kind") == "session_open" and r["id"] in admitted and r["id"] not in closed]


def new_session_id(ts: str, intent: str = "") -> str:
    """A session id that is unique per (host, pid, ts) rather than per minute."""
    host = socket.gethostname()
    digest = hashlib.sha256(f"{host}|{os.getpid()}|{ts}|{intent}".encode("utf-8")).digest()
    return "S-" + base64.b32encode(digest).decode("ascii").lower()[:6]


def memory_state(records: list[dict]) -> dict:
    """Resolve authority once. Only local acceptance observations grant trust.

    Supersession is durable: retiring a replacement does not resurrect its
    predecessor. Foreign transitions have no effect until locally accepted.
    Missing trust is supported for legacy local journal records only.
    """
    local = {r["id"] for r in records if r.get("trust", TRUST_LOCAL) == TRUST_LOCAL}
    locally_retired = {r["supersedes"] for r in records if r["id"] in local
                       and isinstance(r.get("supersedes"), str) and r["supersedes"] != r["id"]}
    accepted = {r["accepted"] for r in records
                if r["id"] in local - locally_retired and isinstance(r.get("accepted"), str)}
    admitted = local | {r["id"] for r in records
                        if r.get("trust") == TRUST_FOREIGN and r["id"] in accepted}
    superseded = {r["supersedes"] for r in records
                  if r["id"] in admitted and isinstance(r.get("supersedes"), str)
                  and r["supersedes"] != r["id"]}
    active = admitted - superseded
    return {"accepted": accepted, "admitted": admitted, "superseded": superseded,
            "active": active,
            "records": [r for r in records if r["id"] in active],
            "informational": [r for r in records if r["id"] not in admitted],
            "rejections": [x for x in rejections(records) if x["parent"] in active]}
