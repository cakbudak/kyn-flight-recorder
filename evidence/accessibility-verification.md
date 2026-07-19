# Accessibility verification

Target: WCAG 2.2 AA for the Build Week control loop.

Automated/static evidence:

- semantic header, navigation, main, sections, ordered progress, dialog, form labels, and
  definition lists;
- skip link and visible `:focus-visible` treatment;
- every button has an explicit type and accessible name;
- polite operation/toast announcements and assertive error feedback;
- dialog opens with focus on the operator field;
- native required/min/max constraints on approval fields;
- no dynamic HTML parsing sink; server data enters through text nodes;
- palette contrast gate ≥ 4.5:1 for normal text on the brightest regular surface;
- no `transition: all`, no zero-scale entrances, transitions ≤300 ms;
- `prefers-reduced-motion` collapses animation/transition durations;
- 390 × 844 Chromium reload has no document overflow and preserves the proven state;
- Chromium verifies every rendered button has visible text or an accessible
  label (86 buttons in the archived deterministic journey).

The browser evidence is in `browser/agent-studio-report.json` and
`live/agent-studio-report.json`.

Not yet verified: physical VoiceOver, NVDA, JAWS, or TalkBack operation. That remains a named
gap and is not counted as a pass.
