# Accessibility verification

Date: 2026-07-18  
Target: WCAG 2.2 Level AA  
Surface: standalone Build Week demo

## Automated audit

Independent tooling from the existing review environment:

- axe-core 4.12.1
- Playwright 1.61.1
- Chromium 147.0.7727.116
- tags: `wcag2a`, `wcag2aa`, `wcag21aa`, `wcag22aa`

| State | Viewport | Violations | Result |
| --- | ---: | ---: | --- |
| Blocked run | 1440 × 1000 | 0 | PASS |
| Intervention dialog | 1440 × 1000 | 0 | PASS |
| Command receipt | 1440 × 1000 | 0 | PASS |
| Blocked run | 390 × 844 | 0 | PASS |
| Intervention dialog | 390 × 520 | 0 | PASS |

axe marked gradient-backed contrast nodes as `incomplete`, which means manual
evaluation is required rather than that a violation exists. The repository's
static contrast test calculates the full text palette against both the brightest
regular surface and the brightest diagnosis tint. The minimum measured ratio is
4.58:1, above the 4.5:1 small-text threshold. Explicit axe contrast violations are
zero.

## Keyboard and focus

The dependency-free Chromium journey proves:

- graph selection moves with `ArrowLeft`/`ArrowRight` and `Home`/`End` while focus
  remains on the newly rendered node;
- keyboard activation opens the intervention with zero motion and places focus in
  the required reason field;
- Escape closes the modal and restores focus to the exact invoking control;
- `Tab` reaches the acknowledgement, Cancel, and Authorize controls in order;
- Space checks the acknowledgement and Enter applies the legal command;
- the completed receipt receives focus after navigation;
- every button exposed in the Chromium accessibility tree has a non-empty name;
- invalid fixture and local-import failures move focus to their error heading;
- a skip link targets the main analysis surface;
- `:focus-visible` is present globally with a high-contrast signal outline.

The detailed check results are in
[`browser-verification.json`](browser-verification.json).

## Motion, layout, and touch

- `prefers-reduced-motion: reduce` collapses UI transition durations to 1 ms.
- No document-level horizontal overflow occurs at 1440 px or 390 px. The causal
  graph itself is an intentional, labeled horizontal inspection region on narrow
  screens.
- Mobile navigation remains fixed and reachable; inspector and modal remain inside
  the 390 px viewport.
- At 390 × 520, the dialog has one bounded scroll body above a non-overlapping
  action footer. Keyboard focus scrolls the visible acknowledgement control fully
  into that body before the actions.
- Interactive controls exceed the WCAG 2.2 24 × 24 CSS-pixel target minimum.
- Pointer hover effects are gated behind fine-pointer/hover media queries.

## Honest remaining manual check

No physical VoiceOver, NVDA, TalkBack, or Orca session was available in this build
environment. Names, roles, dialog modality, landmarks, and focus order were checked
through axe and Chromium's platform accessibility tree, but a release beyond this
Build Week demo should still run one physical screen-reader pass. This limitation is
not hidden or counted as a completed live-device proof.
