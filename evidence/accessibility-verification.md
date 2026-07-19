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
- Chromium accessibility tree exposes 16 named buttons in the completed journey.

The browser evidence is in `browser/closed-loop-report.json` and
`real-model/closed-loop-report.json`.

Not yet verified: physical VoiceOver, NVDA, JAWS, or TalkBack operation. That remains a named
gap and is not counted as a pass.
