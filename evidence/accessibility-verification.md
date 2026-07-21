# Accessibility verification

Target: WCAG 2.2 AA for the Build Week control loop.

Automated/static evidence:

- semantic header, navigation, main, sections, ordered progress, dialog, form labels, and
  definition lists;
- skip link and visible `:focus-visible` treatment;
- every button has an explicit type and accessible name;
- polite operation/toast announcements and assertive error feedback;
- every dialog opens on its close control, traps forward and reverse Tab
  traversal, closes on Escape, and restores focus to its opener;
- native required/min/max constraints on approval fields;
- no dynamic HTML parsing sink; server data enters through text nodes;
- computed contrast audit across all thirteen workbenches in light and dark: 4,310
  local and 4,318 deployed visible text samples, minimum 4.70:1, zero WCAG AA
  failures;
- no `transition: all`, no zero-scale entrances, transitions ≤300 ms;
- `prefers-reduced-motion` collapses animation/transition durations;
- 390 × 844 Chromium reload has no document overflow, preserves the proven
  state, keeps rendered Flow nodes at 196 px, and bounds the minimap to 112 px;
- Chromium verifies every rendered button has visible text or an accessible
  label.

The browser evidence is in `browser/agent-studio-report.json` and
`live/agent-studio-report.json`.

Not yet verified: physical VoiceOver, NVDA, JAWS, or TalkBack operation. That remains a named
gap and is not counted as a pass.
