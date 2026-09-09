"""Render the roadmap as a single self-contained HTML page.

Design notes, because they were decisions rather than defaults:

**No network requests at all.** No CDN, no font host, no analytics. The first
project this was built for is an air-gapped AI security gateway, and a roadmap
page that phones out to fetch a typeface is a page that cannot be opened on the
machine that most needs it. System font stacks only.

**Sans for the plan, serif for the memory.** The plan is structure — phases,
tasks, dependencies — and gets the UI sans. The memory is prose someone wrote
down on purpose — the goal's *why*, a decision's reason, a rejection — and gets
a serif. The split is not decoration: it is the tool's central distinction
(a derived plan beside a durable memory) made visible before a word is read.

**The dependency graph is interactive, not drawn.** Fifty-six nodes of SVG
spaghetti communicates less than one click. Selecting a task lights its upstream
blockers and its downstream dependents and dims everything else, which is the
question people actually bring to a DAG: *what is in my way, and who is waiting
on me.*

**Provisional work is present but visibly lighter.** It has to appear — the whole
path is the point — without reading as commitment.
"""

from __future__ import annotations

import html
from datetime import datetime

from roadmapify.journal import TRUST_FOREIGN
# The DAG primitives live in project.py: status.py ranks the ready list by the
# same "frees N" number this page puts on a card, and two definitions of it
# would drift apart without anyone noticing.
from roadmapify.project import dependents as _dependents, unblocks as _unblocks

_E = html.escape


CSS = """
*, *::before, *::after { box-sizing: border-box; }

:root {
  --ground:      #f3f5f7;
  --surface:     #ffffff;
  --surface-sub: #eef1f4;
  --ink:         #151b23;
  --ink-soft:    #43505f;
  --muted:       #6b7787;
  --line:        #dde3ea;
  --line-strong: #c3ccd7;

  /* Accent: plan, structure, progress. */
  --plan:        #1f4e8c;
  --plan-soft:   #e7edf7;
  /* Memory: what was decided and what was ruled out. Warm against the cool ground. */
  --memory:      #8a5a14;
  --memory-soft: #f7efe1;
  /* Semantics, deliberately separate from the accent. */
  --danger:      #a3302a;
  --danger-soft: #f8e9e8;
  --good:        #2c6349;

  --radius: 7px;
  --sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --serif: ui-serif, Georgia, "Iowan Old Style", "Times New Roman", serif;
  --mono: ui-monospace, "SF Mono", "Cascadia Mono", "Roboto Mono", Menlo, Consolas, monospace;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ground:      #0f1319;
    --surface:     #161c24;
    --surface-sub: #1c242e;
    --ink:         #e4e9ef;
    --ink-soft:    #b3bdc9;
    --muted:       #8794a3;
    --line:        #26303b;
    --line-strong: #36434f;
    --plan:        #7ba7e0;
    --plan-soft:   #17263a;
    --memory:      #d9a45e;
    --memory-soft: #2b2216;
    --danger:      #e58b83;
    --danger-soft: #2e1a18;
    --good:        #74b898;
  }
}
:root[data-theme="dark"] {
  --ground:      #0f1319;
  --surface:     #161c24;
  --surface-sub: #1c242e;
  --ink:         #e4e9ef;
  --ink-soft:    #b3bdc9;
  --muted:       #8794a3;
  --line:        #26303b;
  --line-strong: #36434f;
  --plan:        #7ba7e0;
  --plan-soft:   #17263a;
  --memory:      #d9a45e;
  --memory-soft: #2b2216;
  --danger:      #e58b83;
  --danger-soft: #2e1a18;
  --good:        #74b898;
}

body {
  margin: 0;
  background: var(--ground);
  color: var(--ink);
  font-family: var(--sans);
  font-size: 15px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}

.wrap { max-width: 1180px; margin: 0 auto; padding: 0 28px 96px; }

/* ── masthead ─────────────────────────────────────────────────────────── */

.masthead { padding: 56px 0 34px; border-bottom: 1px solid var(--line); }
.eyebrow {
  font-family: var(--mono); font-size: 11px; letter-spacing: .14em;
  text-transform: uppercase; color: var(--muted); margin: 0 0 14px;
}
h1 {
  font-size: clamp(28px, 4vw, 40px); line-height: 1.15; margin: 0 0 18px;
  letter-spacing: -.02em; font-weight: 640; text-wrap: balance; max-width: 22ch;
}
.statement {
  font-family: var(--serif); font-size: 17px; line-height: 1.6;
  color: var(--ink-soft); max-width: 62ch; margin: 0 0 22px;
}
.why {
  font-family: var(--serif); font-size: 15.5px; line-height: 1.62;
  color: var(--ink-soft); max-width: 62ch; margin: 0;
  padding-left: 18px; border-left: 2px solid var(--memory);
}
.why b { font-weight: 600; color: var(--ink); font-variant: small-caps;
         letter-spacing: .03em; }

.criteria { list-style: none; margin: 26px 0 0; padding: 0;
            display: grid; gap: 7px; max-width: 74ch; }
.criteria li {
  display: grid; grid-template-columns: 16px 1fr; gap: 11px;
  font-size: 14px; color: var(--ink-soft); align-items: baseline;
}
.criteria li::before {
  content: ""; width: 9px; height: 9px; border-radius: 2px;
  border: 1.5px solid var(--good); display: block; transform: translateY(1px);
}

/* ── state strip ──────────────────────────────────────────────────────── */

.state {
  display: flex; flex-wrap: wrap; gap: 0; margin: 0 0 8px;
  border-bottom: 1px solid var(--line); padding: 20px 0;
  align-items: center;
}
.stat { padding-right: 34px; margin-right: 34px; border-right: 1px solid var(--line); }
.stat:last-of-type { border-right: 0; }
.stat .n {
  font-family: var(--mono); font-size: 25px; font-weight: 600;
  font-variant-numeric: tabular-nums; display: block; line-height: 1.1;
}
.stat .l { font-size: 11.5px; color: var(--muted); letter-spacing: .05em;
           text-transform: uppercase; }
.stat.plan .n   { color: var(--plan); }
.stat.memory .n { color: var(--memory); }
.stat.risk .n   { color: var(--danger); }

.controls { margin-left: auto; display: flex; gap: 8px; align-items: center; }
button.ctl {
  font: inherit; font-size: 13px; color: var(--ink-soft);
  background: var(--surface); border: 1px solid var(--line-strong);
  border-radius: var(--radius); padding: 6px 13px; cursor: pointer;
}
button.ctl:hover { border-color: var(--plan); color: var(--plan); }
button.ctl:focus-visible { outline: 2px solid var(--plan); outline-offset: 2px; }
button.ctl[aria-pressed="true"] { background: var(--plan-soft); border-color: var(--plan);
                                  color: var(--plan); }

/* ── sections ─────────────────────────────────────────────────────────── */

h2.section {
  font-size: 13px; letter-spacing: .13em; text-transform: uppercase;
  color: var(--muted); font-weight: 600; margin: 54px 0 20px;
  padding-bottom: 9px; border-bottom: 1px solid var(--line);
}

/* ── phases ───────────────────────────────────────────────────────────── */

.phase { margin: 0 0 12px; }
.phase-head {
  display: grid; grid-template-columns: 46px 1fr; gap: 18px;
  padding: 22px 0 14px; align-items: start;
}
.phase-num {
  font-family: var(--mono); font-size: 12px; font-weight: 600;
  color: var(--plan); background: var(--plan-soft);
  border-radius: var(--radius); text-align: center; padding: 7px 0;
  letter-spacing: .04em;
}
.phase h3 { margin: 0 0 5px; font-size: 20px; font-weight: 620; letter-spacing: -.01em; }
.phase .intent { margin: 0 0 10px; color: var(--ink-soft); font-size: 14px; max-width: 76ch; }
.ships {
  font-size: 13.5px; color: var(--ink-soft); max-width: 76ch;
  background: var(--surface-sub); border-radius: var(--radius);
  padding: 9px 13px; display: flex; gap: 9px; align-items: baseline;
}
.ships b {
  font-family: var(--mono); font-size: 10.5px; letter-spacing: .1em;
  text-transform: uppercase; color: var(--muted); flex: none; padding-top: 2px;
}

.phase.provisional .phase-num { color: var(--muted); background: var(--surface-sub); }
.phase.provisional h3 { color: var(--ink-soft); }
.tag-prov {
  font-family: var(--mono); font-size: 10px; letter-spacing: .09em;
  text-transform: uppercase; color: var(--muted);
  border: 1px solid var(--line-strong); border-radius: 4px;
  padding: 2px 6px; vertical-align: 3px; margin-left: 9px; font-weight: 500;
}

/* ── tasks ────────────────────────────────────────────────────────────── */

.tasks { display: grid; grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
         gap: 11px; margin: 4px 0 0 64px; }
@media (max-width: 700px) { .tasks { margin-left: 0; } }

.task {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 13px 15px; cursor: pointer;
  text-align: left; font: inherit; color: inherit; width: 100%;
  display: block; transition: border-color .12s, box-shadow .12s, opacity .12s;
}
.task:hover { border-color: var(--line-strong); }
.task:focus-visible { outline: 2px solid var(--plan); outline-offset: 2px; }
.phase.provisional .task { background: transparent; border-style: dashed; }

.task-top { display: flex; gap: 9px; align-items: baseline; margin-bottom: 5px; }
.tid {
  font-family: var(--mono); font-size: 11.5px; font-weight: 600;
  color: var(--plan); letter-spacing: .02em; flex: none;
}
.phase.provisional .tid { color: var(--muted); }
.task-label { font-size: 14px; font-weight: 550; line-height: 1.38; letter-spacing: -.005em; }
.task-intent {
  font-size: 12.5px; color: var(--muted); line-height: 1.5; margin: 6px 0 0;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
  overflow: hidden;
}
.task.open .task-intent { -webkit-line-clamp: unset; }

.task-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 9px; }
.chip {
  font-family: var(--mono); font-size: 10.5px; letter-spacing: .02em;
  border-radius: 4px; padding: 2px 6px; border: 1px solid var(--line);
  color: var(--muted); white-space: nowrap;
}
.chip.dep    { color: var(--plan); border-color: var(--plan); background: var(--plan-soft); }
.chip.frees  { color: var(--memory); border-color: var(--memory); background: var(--memory-soft); }
.chip.path   { color: var(--ink-soft); font-size: 10px; }

/* selection states */
body.has-selection .task { opacity: .3; }
body.has-selection .task.sel,
body.has-selection .task.up,
body.has-selection .task.down { opacity: 1; }
.task.sel  { border-color: var(--ink); box-shadow: 0 0 0 1px var(--ink); }
.task.up   { border-color: var(--plan);   box-shadow: 0 0 0 1px var(--plan); }
.task.down { border-color: var(--memory); box-shadow: 0 0 0 1px var(--memory); }

.legend {
  position: sticky; bottom: 18px; margin: 26px 0 0; z-index: 5;
  background: var(--surface); border: 1px solid var(--line-strong);
  border-radius: var(--radius); padding: 10px 15px; font-size: 12.5px;
  display: none; gap: 18px; align-items: center; flex-wrap: wrap;
  box-shadow: 0 6px 22px rgba(0,0,0,.10);
}
body.has-selection .legend { display: flex; }
.legend .sw { width: 9px; height: 9px; border-radius: 2px; display: inline-block;
              margin-right: 6px; vertical-align: 0; }
.legend .up-sw { background: var(--plan); }
.legend .down-sw { background: var(--memory); }
.legend button { margin-left: auto; }

/* ── memory ───────────────────────────────────────────────────────────── */

.mem-grid { display: grid; gap: 11px; }
.rec {
  background: var(--surface); border: 1px solid var(--line);
  border-left: 3px solid var(--line-strong);
  border-radius: var(--radius); padding: 13px 16px;
}
.rec.constraint { border-left-color: var(--good); }
.rec.rejection  { border-left-color: var(--memory); }
.rec.risk       { border-left-color: var(--danger); }
.rec.question   { border-left-color: var(--plan); }

.rec-head { display: flex; gap: 10px; align-items: baseline; margin-bottom: 4px; }
.rid { font-family: var(--mono); font-size: 11px; color: var(--muted); flex: none; }
.rec-text { font-size: 14.5px; font-weight: 550; line-height: 1.42; }
.rec.rejection .rec-text { font-weight: 600; }
.rec-why {
  font-family: var(--serif); font-size: 14px; line-height: 1.58;
  color: var(--ink-soft); margin: 7px 0 0; max-width: 76ch;
}
.rec-src { font-size: 12px; color: var(--muted); margin: 7px 0 0; }
.rec-src code { font-family: var(--mono); font-size: 11px; }
.strike { text-decoration: line-through; text-decoration-thickness: 1.5px;
          text-decoration-color: var(--danger); }

.callout {
  border: 1px solid var(--memory); background: var(--memory-soft);
  border-radius: var(--radius); padding: 15px 18px; margin: 0 0 20px;
  font-family: var(--serif); font-size: 14.5px; line-height: 1.6;
  color: var(--ink-soft); max-width: 76ch;
}
.callout b { font-family: var(--sans); font-weight: 620; color: var(--ink); }

footer {
  margin-top: 64px; padding-top: 20px; border-top: 1px solid var(--line);
  font-size: 12.5px; color: var(--muted); display: flex; flex-wrap: wrap;
  gap: 8px 24px;
}
footer code { font-family: var(--mono); font-size: 11.5px; }

@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
}
"""

JS = """
(function () {
  var body = document.body;
  var tasks = Array.prototype.slice.call(document.querySelectorAll('.task'));
  var byId = {};
  tasks.forEach(function (el) { byId[el.dataset.id] = el; });

  function clear() {
    body.classList.remove('has-selection');
    tasks.forEach(function (el) { el.classList.remove('sel', 'up', 'down', 'open'); });
  }

  // Walk the edges transitively, so selecting a task shows everything actually
  // in its way — not just its immediate neighbours, which is the number people
  // mistake for the answer.
  function walk(id, key, seen) {
    (JSON.parse(byId[id].dataset[key] || '[]')).forEach(function (n) {
      if (seen[n]) return;
      seen[n] = true;
      walk(n, key, seen);
    });
    return seen;
  }

  function select(el) {
    var id = el.dataset.id;
    if (el.classList.contains('sel')) { clear(); return; }
    clear();
    body.classList.add('has-selection');
    el.classList.add('sel', 'open');
    Object.keys(walk(id, 'deps', {})).forEach(function (n) {
      if (byId[n]) byId[n].classList.add('up');
    });
    Object.keys(walk(id, 'dependents', {})).forEach(function (n) {
      if (byId[n]) byId[n].classList.add('down');
    });
    var n = document.querySelectorAll('.task.up').length;
    var m = document.querySelectorAll('.task.down').length;
    document.getElementById('legend-text').textContent =
      el.dataset.id + ' — ' + n + ' blocking it, ' + m + ' waiting on it';
  }

  tasks.forEach(function (el) {
    el.addEventListener('click', function () { select(el); });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') clear();
  });
  var clearBtn = document.getElementById('clear-sel');
  if (clearBtn) clearBtn.addEventListener('click', clear);

  var provBtn = document.getElementById('toggle-prov');
  if (provBtn) {
    provBtn.addEventListener('click', function () {
      var hidden = provBtn.getAttribute('aria-pressed') === 'true';
      provBtn.setAttribute('aria-pressed', hidden ? 'false' : 'true');
      provBtn.textContent = hidden ? 'Hide provisional' : 'Show provisional';
      Array.prototype.forEach.call(
        document.querySelectorAll('.phase.provisional'),
        function (p) { p.hidden = !hidden ? true : false; }
      );
    });
  }
})();
"""


def _task_card(t, deps_json: str, dependents_json: str, frees: int) -> str:
    chips = []
    for dep in t.depends_on:
        chips.append(f'<span class="chip dep">after {_E(dep)}</span>')
    if frees:
        chips.append(f'<span class="chip frees">frees {frees}</span>')
    for spec in t.produces[:3]:
        kind, _, rest = spec.partition(":")
        label = rest or kind
        chips.append(f'<span class="chip path">{_E(label.split("/")[-1])}</span>')

    intent = f'<p class="task-intent">{_E(t.intent)}</p>' if t.intent else ""
    meta = f'<div class="task-meta">{"".join(chips)}</div>' if chips else ""
    return (
        f'<button class="task" type="button" data-id="{_E(t.id)}" '
        f"data-deps='{deps_json}' data-dependents='{dependents_json}'>"
        f'<span class="task-top"><span class="tid">{_E(t.id)}</span>'
        f'<span class="task-label">{_E(t.label)}</span></span>'
        f"{intent}{meta}</button>"
    )


def _records_section(title: str, items: list[str], note: str = "") -> str:
    if not items:
        return ""
    callout = f'<p class="callout">{note}</p>' if note else ""
    return (f'<h2 class="section">{_E(title)}</h2>{callout}'
            f'<div class="mem-grid">{"".join(items)}</div>')


def to_html(plan, records: list[dict], rejections: list[dict], *,
            now: datetime, project: str = "") -> str:
    """Render the whole roadmap as one self-contained page. Pure: no I/O, no clock."""
    from roadmapify.journal import memory_state
    memory = memory_state(records)
    superseded = memory["superseded"]
    local = memory["records"]

    def of(kind: str) -> list[dict]:
        return [r for r in (records if kind == "decision" else local)
                if r.get("kind") == kind and r["id"] in memory["admitted"]]

    dependents = _dependents(plan)
    frees = _unblocks(plan, dependents)
    import json as _json

    phases_html = []
    for p in plan.ordered_phases():
        cards = "".join(
            _task_card(t, _json.dumps(list(t.depends_on)),
                       _json.dumps(dependents.get(t.id, [])), frees.get(t.id, 0))
            for t in sorted(plan.tasks_of(p.id), key=lambda t: t.id)
        )
        prov = " provisional" if p.provisional else ""
        tag = '<span class="tag-prov">provisional</span>' if p.provisional else ""
        ships = (f'<div class="ships"><b>ships</b><span>{_E(p.ships)}</span></div>'
                 if p.ships else "")
        intent = f'<p class="intent">{_E(p.intent)}</p>' if p.intent else ""
        phases_html.append(
            f'<section class="phase{prov}"><div class="phase-head">'
            f'<div class="phase-num">{_E(p.id)}</div><div>'
            f"<h3>{_E(p.label)}{tag}</h3>{intent}{ships}</div></div>"
            f'<div class="tasks">{cards}</div></section>'
        )

    live_rejections = memory["rejections"]
    rej_items = [
        f'<div class="rec rejection"><div class="rec-head">'
        f'<span class="rid">{_E(x["id"])}</span>'
        f'<span class="rec-text strike">{_E(x["alt"])}</span></div>'
        + (f'<p class="rec-why">{_E(x["reason"])}</p>' if x.get("reason") else "")
        + f'<p class="rec-src">ruled out by <code>{_E(x["parent"])}</code> — '
          f'{_E(x["parent_text"])}</p></div>'
        for x in live_rejections
    ]

    con_items = [
        f'<div class="rec constraint"><div class="rec-head">'
        f'<span class="rid">{_E(r["id"])}</span>'
        f'<span class="rec-text">{_E(r["text"])}</span></div>'
        + (f'<p class="rec-why">{_E(r["why"])}</p>' if r.get("why") else "") + "</div>"
        for r in of("constraint")
    ]

    dec_items = []
    for r in of("decision"):
        reversed_note = ""
        if r["id"] in superseded:
            killer = next((o["id"] for o in records if o.get("supersedes") == r["id"]), "")
            reversed_note = (f'<p class="rec-src">reversed by <code>{_E(killer)}</code> — '
                             f"that decision is the one in force</p>")
        dec_items.append(
            f'<div class="rec"><div class="rec-head">'
            f'<span class="rid">{_E(r["id"])}</span>'
            f'<span class="rec-text{" strike" if r["id"] in superseded else ""}">'
            f'{_E(r["text"])}</span></div>'
            + (f'<p class="rec-why">{_E(r["why"])}</p>' if r.get("why") else "")
            + reversed_note + "</div>"
        )

    risk_items = [
        f'<div class="rec risk"><div class="rec-head">'
        f'<span class="rid">{_E(r["id"])}</span>'
        f'<span class="rec-text">{_E(r["text"])}</span></div>'
        + (f'<p class="rec-why">{_E(r["why"])}</p>' if r.get("why") else "") + "</div>"
        for r in of("risk")
    ]
    q_items = [
        f'<div class="rec question"><div class="rec-head">'
        f'<span class="rid">{_E(r["id"])}</span>'
        f'<span class="rec-text">{_E(r["text"])}</span></div>'
        + (f'<p class="rec-why">{_E(r["why"])}</p>' if r.get("why") else "") + "</div>"
        for r in of("question")
    ]

    active = [t for t in plan.tasks if not t.provisional]
    criteria = "".join(f"<li><span>{_E(c)}</span></li>" for c in plan.goal.success_criteria)
    criteria_html = f'<ul class="criteria">{criteria}</ul>' if criteria else ""
    why_html = (f'<p class="why"><b>Why this exists.</b> {_E(plan.goal.why)}</p>'
                if plan.goal.why else "")
    statement = (f'<p class="statement">{_E(plan.goal.statement)}</p>'
                 if plan.goal.statement else "")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_E(plan.goal.label or project or "Roadmap")}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header class="masthead">
  <p class="eyebrow">Roadmap{" · " + _E(project) if project else ""}</p>
  <h1>{_E(plan.goal.label)}</h1>
  {statement}
  {why_html}
  {criteria_html}
</header>

<div class="state">
  <div class="stat plan"><span class="n">{len(plan.phases)}</span><span class="l">phases</span></div>
  <div class="stat plan"><span class="n">{len(active)}</span><span class="l">active tasks</span></div>
  <div class="stat"><span class="n">{len(plan.tasks) - len(active)}</span><span class="l">provisional</span></div>
  <div class="stat memory"><span class="n">{len(live_rejections)}</span><span class="l">ruled out</span></div>
  <div class="stat memory"><span class="n">{len(con_items)}</span><span class="l">constraints</span></div>
  <div class="stat risk"><span class="n">{len(risk_items)}</span><span class="l">risks</span></div>
  <div class="controls">
    <button class="ctl" id="toggle-prov" type="button" aria-pressed="false">Hide provisional</button>
  </div>
</div>

<h2 class="section">The path</h2>
{"".join(phases_html)}

<div class="legend">
  <span><span class="sw up-sw"></span>blocking it</span>
  <span><span class="sw down-sw"></span>waiting on it</span>
  <span id="legend-text"></span>
  <button class="ctl" id="clear-sel" type="button">Clear</button>
</div>

{_records_section("Already ruled out", rej_items,
  "These are not open questions. Each was considered, rejected, and the reason recorded — "
  "so nobody spends a second afternoon rediscovering it.")}
{_records_section("Constraints", con_items)}
{_records_section("Decisions", dec_items)}
{_records_section("Risks", risk_items)}
{_records_section("Open questions", q_items)}

<footer>
  <span>Derived from <code>roadmap.toml</code> and <code>roadmap-out/journal.jsonl</code></span>
  <span>{len(records)} records · {len(plan.tasks)} tasks</span>
  <span>Generated {now.strftime("%Y-%m-%d")} by <code>roadmap export</code></span>
  <span>No network requests — this page opens offline.</span>
</footer>
</div>
<script>{JS}</script>
</body>
</html>
"""
