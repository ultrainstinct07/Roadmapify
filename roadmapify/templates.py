"""Offline phase skeletons. No API key, no network, no model call.

`roadmap init` has to produce a real, complete, phased path in under a second on
a machine with no provider configured — that is a fixed constraint, and it is
also the honest default: an LLM asked to decompose "build a CLI that syncs X"
mostly reproduces the same six phases every competent engineer would write, so
the template gets you those for free and `roadmap expand --llm` spends tokens
only on the part that is actually project-specific.

Each archetype emits the COMPLETE path — every phase to a shippable app — with
tasks beyond the second phase marked ``provisional``. Provisional tasks are
visible in ``roadmap tree`` (the user asked for a path to a finished app, and a
path that stops after phase two is not one) but are excluded from ready lists,
from verify's denominators, and from both health denominators, so a guess made
on day one can never make the roadmap lie about itself on day thirty. A
provisional task that claims done with an absent deliverable is still
contradicted — that is the plan being behind the work, not a guess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from roadmapify.plan import Goal, Plan, PhaseSpec, TaskSpec


@dataclass(frozen=True)
class Archetype:
    name: str
    summary: str
    #: (label, intent, ships, exit_criteria, demo)
    phases: tuple[tuple[str, str, str, str, str], ...]
    #: phase_index (0-based) -> ((label, intent, produces...), ...)
    tasks: tuple[tuple[int, str, str, tuple[str, ...]], ...]
    constraints: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()


_GENERIC = Archetype(
    name="generic",
    summary="any project — five phases from skeleton to shipped",
    phases=(
        ("Foundations",
         "Get a skeleton that runs and a test that proves it",
         "the project starts and one test passes",
         "the entry point runs and CI is green on an empty test suite",
         "run it and see it do nothing, on purpose"),
        ("Core",
         "Build the one thing this project exists to do",
         "the core path works end to end for the happy case",
         "a real input produces a real output without manual steps",
         "the smallest real example, start to finish"),
        ("Persistence and state",
         "Make the work survive a restart",
         "state written by one run is read by the next",
         "kill the process mid-run and the next run recovers",
         "stop it, start it, nothing is lost"),
        ("Edges and failure",
         "Handle the inputs that break the happy path",
         "bad input fails with a message a human can act on",
         "every failure mode has a test and a message",
         "feed it garbage and watch it explain itself"),
        ("Ship",
         "Make it installable and understandable by someone else",
         "a stranger can install and use it from the README alone",
         "install from a clean machine following only the docs",
         "hand it to someone and say nothing"),
    ),
    tasks=(
        (0, "Project skeleton and entry point", "", ("file:README.md",)),
        (0, "Test harness and first passing test", "", ()),
        (1, "Model the core domain", "the nouns this project is about", ()),
        (1, "Implement the main path", "input to output, happy case only", ()),
        (1, "Tests for the main path", "", ()),
        (2, "Choose and document the storage format", "", ()),
        (2, "Write state", "", ()),
        (2, "Read state back and reconcile", "", ()),
        (3, "Input validation and error messages", "", ()),
        (3, "Failure-mode tests", "", ()),
        (4, "README with install and first run", "", ("file:README.md",)),
        (4, "Packaging and a release check", "", ()),
    ),
    constraints=(),
    risks=("the core path turns out to be harder than the skeleton suggested",),
)


_CLI = Archetype(
    name="cli",
    summary="a command-line tool — six phases from `--help` to installable",
    phases=(
        ("Skeleton",
         "One command that runs, parses args, and prints something",
         "`<tool> --help` prints real usage",
         "the entry point is installed and --help is accurate",
         "run --help"),
        ("Core engine",
         "The library underneath, callable without the CLI",
         "the core function returns a real result from a real input",
         "the engine is importable and tested with no CLI involved",
         "call it from a Python REPL"),
        ("Persistence",
         "Write results somewhere and read them back",
         "a second run reuses the first run's output",
         "output format is documented and round-trips byte-identically",
         "run twice, diff the output"),
        ("Command surface",
         "The rest of the subcommands, flags and exit codes",
         "every documented subcommand works and exits correctly",
         "each subcommand has a test asserting its exit code",
         "walk the whole surface in one terminal session"),
        ("Hardening",
         "Bad input, missing files, no network, no permissions",
         "no traceback ever reaches the user",
         "every error path prints one actionable line and exits non-zero",
         "run it in a directory where nothing is where it should be"),
        ("Ship",
         "Install, document, release",
         "`pip install` from a clean machine, then the README's first example",
         "packaging metadata is correct and the docs match the surface",
         "install from the built artifact and follow the README"),
    ),
    tasks=(
        (0, "Package layout and console entry point", "", ("file:pyproject.toml",)),
        (0, "Argument dispatch and --help", "", ()),
        (0, "Test harness and a --help test", "", ()),
        (1, "Core data model", "the types the engine passes around", ()),
        (1, "The main engine function", "no I/O, no argv, pure and testable", ()),
        (1, "Engine unit tests", "", ()),
        (2, "Output format and writer", "", ()),
        (2, "Reader and round-trip test", "", ()),
        (3, "Remaining subcommands", "", ()),
        (3, "Exit-code contract and tests", "", ()),
        (4, "Error handling and messages", "", ()),
        (4, "Failure-path tests", "", ()),
        (5, "README with install and examples", "", ("file:README.md",)),
        (5, "Packaging metadata and release check", "", ()),
    ),
    constraints=(),
    risks=("the CLI surface grows faster than the engine that backs it",),
)


ARCHETYPES: dict[str, Archetype] = {a.name: a for a in (_GENERIC, _CLI)}


def detect_archetype(root: "str | Path | None" = None) -> str:
    """Guess the archetype from what is already in the directory.

    Conservative on purpose: a wrong guess produces a plan the user has to
    rewrite, which is worse than the generic skeleton they were going to adjust
    anyway. Only signals that are near-unambiguous vote.
    """
    d = Path(root or Path.cwd())
    pyproject = d / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "[project.scripts]" in text or "console_scripts" in text:
            return "cli"
    pkg = d / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict) and data.get("bin"):
            return "cli"
    if (d / "Cargo.toml").is_file() and (d / "src" / "main.rs").is_file():
        return "cli"
    return "generic"


#: Phases beyond this index are emitted provisional. Two is the working window:
#: the phase you are in and the one you are about to enter.
ACTIVE_PHASE_WINDOW = 2


def skeleton(archetype: str, goal: Goal) -> Plan:
    """Build a complete :class:`Plan` from an archetype and a goal. Pure and deterministic."""
    arch = ARCHETYPES.get(archetype) or ARCHETYPES["generic"]
    phases: list[PhaseSpec] = []
    for i, (label, intent, ships, exit_criteria, demo) in enumerate(arch.phases, start=1):
        phases.append(PhaseSpec(
            id=f"P-{i}",
            label=label,
            order=i,
            intent=intent,
            ships=ships,
            exit_criteria=exit_criteria,
            demo=demo,
            provisional=i > ACTIVE_PHASE_WINDOW,
        ))

    tasks: list[TaskSpec] = []
    prev_in_phase: dict[int, str] = {}
    for n, (pidx, label, intent, produces) in enumerate(arch.tasks, start=1):
        tid = f"T-{n:02d}"
        # Tasks inside a phase are chained in listed order. It is the weakest
        # honest dependency claim: the template knows the sequence it wrote, and
        # it does not know anything about cross-phase coupling, so it does not
        # invent any.
        deps = (prev_in_phase[pidx],) if pidx in prev_in_phase else ()
        prev_in_phase[pidx] = tid
        tasks.append(TaskSpec(
            id=tid,
            label=label,
            phase=f"P-{pidx + 1}",
            intent=intent,
            depends_on=deps,
            produces=tuple(produces),
            provisional=(pidx + 1) > ACTIVE_PHASE_WINDOW,
            origin="template",
        ))

    return Plan(goal=goal, phases=tuple(phases), tasks=tuple(tasks))
