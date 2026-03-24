# Tree Drag And Drop Reliability

## Summary

On the `/tree` concept taxonomy UI in the tested macOS browser environment, native HTML5 `drop` events were not reliable for modifier-assisted tag-to-concept drag and drop.

Observed debug results during repeated Alt/Ctrl drag tests:

- `dragStarts = 3`
- `nativeDrops = 0`
- `fallbackDrops = 3`

Observed event sequence:

- `dragstart`
- one or more `dragover-concept`
- `dragend`
- fallback execution based on last hovered concept target

## Practical Guidance

- Do not rely on native `drop` alone for modifier-assisted drag interactions on this surface.
- Treat `dragover` as reliable for hover feedback and target tracking.
- Preserve the last valid hovered drop target during the drag session.
- Do not clear that remembered target in `dragleave` if `dragend` fallback depends on it.
- Use a `dragend` fallback or an explicit alternate-drop affordance when the action must be committed reliably.

## Current Production Approach

The concept tree keeps a small drag session state for tag drags:

- current dragged tag id
- last dragged tag id
- last hovered concept id and level
- alternate-mode state
- whether the drop was already handled

If native `drop` fires, the action completes there. If native `drop` does not fire, the `dragend` handler uses the last hovered concept target to complete the same association logic.

## Implication For Future Code Generation

When generating future UI drag-and-drop code for this project:

- prefer resilient commit paths over assuming browser-perfect native `drop` delivery
- consider explicit alternate-drop affordances for advanced interactions
- keep browser-event ordering in mind, especially `dragleave` occurring before `dragend`