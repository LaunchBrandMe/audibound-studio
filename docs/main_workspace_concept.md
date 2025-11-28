# Main Workspace Concept

## Goal
Give writers a split experience where they can paste entire scripts into a distraction-free editor while keeping project controls, history, and render actions docked within a contextual sidebar. The editor should feel like a long-form document canvas (auto-expanding, infinite scroll) and the sidebar should remain visible for quick iteration.

## Layout Overview
1. **Two-panel shell** using CSS grid or flex:
   - **Left: Script canvas (70%)**
     - Sticky header with project title, word count, and action buttons (save, clear, import).
     - Full-height scrollable `<textarea>` (or Draft.js/TipTap) that expands with content.
     - Optional inline markers for scenes/characters detected from direction step.
   - **Right: Utility sidebar (30%)**
     - Tabs for `Project`, `Voices`, `History`.
     - Each tab exposes cards with relevant actions (e.g., assign voices, fire "Direct Script", view render queue, view last validation report).
     - Sidebar remains fixed using `position: sticky; top: 24px;` while main canvas scrolls.
2. **Global footer audio player** reused from existing code so renders appear immediately playable.

## Data & Interaction Model
- **State store:** Continue using current `projectStore` but expose `selectedProjectId` so the editor binds to whichever project is in focus.
- **Autosave:** Debounce `textarea` input (e.g., 750 ms) and PUT to `/projects/{id}` to update `raw_text` before direction.
- **Context badges:** Sidebar can read from `project.validation_summary`, `render_history`, and `voice_overrides` that already exist in API responses.
- **Voice assignments:** Move existing character list UI into a sidebar tab with accordion rows so it remains accessible while editing text.
- **History timeline:** Display from `project.render_history` with inline play/download buttons (reuse `playAudio`).

## Implementation Steps (Suggested for next branch)
1. **Refactor HTML structure**
   - Wrap page inside `.workspace-grid` with columns `minmax(0, 2fr)` and `minmax(280px, 1fr)`.
   - Move flash/messages inside sidebar top for visibility.
2. **Script canvas**
   - Replace `<textarea id="text">` with `<div contenteditable>` or Monaco/CodeMirror for better large-text handling.
   - Add scene markers by parsing `project.manifest` once available (optional step).
   - Provide toolbar (import, clear, word count) pinned to top.
3. **Sidebar**
   - Create tabbed component: `Overview`, `Voices`, `Activity`.
   - Reuse character override markup inside `Voices` tab (scrollable area inside sidebar with max height + overflow auto).
   - Add `Recent Activity` log & `Render History` into `Activity` tab.
4. **Responsive behavior**
   - Collapse sidebar below `960px` into drawer triggered by button to maintain usability on tablets.
5. **Scripting API hooks**
   - When text changes -> update store + enable `Direct Script` button.
   - Buttons (`Direct Script`, `Produce Audio`) remain but move into sidebar header to stay visible while scrolling long script.
6. **Progress indicators**
   - Show status badges (e.g., `Directed`, `Producing`) near top of sidebar with spinner, reusing `project.loadingAction`.

## Technical Notes
- Use CSS `scrollbar-gutter: stable;` inside editor to prevent layout shift when scrollbars appear.
- For huge scripts, consider virtualization if scenes list grows; start simple with lazy rendering.
- Storing the script text in localStorage (per project) can provide offline safety; clear after successful save.
- Accessibility: ensure sidebar controls remain reachable via keyboard (use `tabindex="0"` on tab buttons, ARIA labels for role tabs/panels).

This structure keeps the editor immersive while ensuring production controls are always within one click, matching the desired "sidebar + scrolling editable section" workflow.
