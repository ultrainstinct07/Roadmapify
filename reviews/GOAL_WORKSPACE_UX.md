# Goal workspace UI/UX update

Implemented in `roadmapify/workspace.html` on 2026-09-09. This updates the packaged workspace, not the historical prototype under `reviews/`.

The desktop workspace keeps the phase navigation, diagram, execution controls and selected-node inspector on screen together. The overall page no longer requires scrolling to find the run controls or node details. The palette, typography, spacing and component states now share one visual system, using local CSS and system fonts.

## Interactions

- Global search opens matching tasks, phases and effective decisions. `/` focuses search; arrow keys navigate results; Enter selects; Escape dismisses.
- Dependency views filter by phase, status or remaining path. Selecting a task highlights its prerequisite and dependent paths; the inspector links to prerequisites and lists deliverables, current observation counts and uncertainty.
- Goal diagrams initially fit the desktop canvas. Narrow screens use a readable vertical roadmap. Zoom preserves the viewport center; Fit, background dragging and arrow-key panning provide diagram navigation. Expand temporarily gives the diagram the full workspace width.
- Mobile navigation and node details use panels with keyboard focus handling. The task list provides a textual route to every task.
- Code links have a task selector, a Graphify import empty state, exact source matching, source-root settings and paginated results. Decisions are paginated too, so large record sets do not force unreadably small nodes.
- Run setup exposes step and time limits. The execution strip distinguishes queued work, active work, pause requests, paused work and cancellation. Run activity shows the underlying event history and host connection guidance. Opening the workspace does not launch an agent.
- Context preview uses the current queued/active step packet when applicable and otherwise prepares a snapshot of an eligible task. Cancelled run packets are not presented as upcoming work.
- Diagram export provides a rendered SVG preview, source text and download controls.

## Validation

Browser checks covered task search and selection, dependency filters, phase details, decision pagination, Graphify import, SVG rendering, execution setup and queued/pause/cancel transitions. The synthetic Graphify fixture yielded two exact source matches and excluded the same filename under an unrelated directory. Mobile review used a 390 × 844 viewport: document width and height matched the viewport, and the vertical roadmap remained readable. Temporary viewport overrides were reset.

The full Python suite passed 434 tests. JavaScript syntax validation and `git diff --check` passed. `python3 -m roadmapify verify --json` found 46 distinct declared artifacts present, zero missing, zero unverifiable and zero unresolvable. It found zero completion claims; these artifact findings do not imply completed roadmap tasks or accepted goal criteria.

The existing host execution API is unchanged. A compatible external agent host is required to perform repository work. Imported code graphs remain local to the page; reloading requires importing the file again. SVG rendering was verified; download completion was not independently confirmed in the in-app browser.
