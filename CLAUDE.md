# House rules: Ponytail (lazy senior dev mode)

Source: [`dietrichgebert/ponytail`](https://github.com/dietrichgebert/ponytail),
its `AGENTS.md` and `skills/ponytail/SKILL.md`, fetched directly. This
project runs at **Full** intensity by default: enforce the ladder below,
stdlib/native first, shortest diff and explanation.

## The ladder — stop at the first rung that holds

1. Does it need to exist? (YAGNI)
2. Already in this codebase?
3. Stdlib does it?
4. Native platform feature covers it?
5. Already-installed dependency solves it?
6. Can it be one line?
7. Only then: minimum working code.

Reject abstractions that weren't explicitly requested, new dependencies if
avoidable, and boilerplate nobody asked for. Favor deletion over addition,
boring over clever.

## Never simplify away

Input validation at trust boundaries, error handling that prevents data
loss, security measures, accessibility basics, and anything explicitly
requested. "The smallest change in the wrong place isn't lazy, it's a
second bug" — trace the full problem and every caller before editing.

## Bug fixes

Fix root causes in shared functions rather than patching individual
callers, so one guard prevents multiple failure paths.

## Testing

Non-trivial logic gets one runnable check behind it (an assert-based demo
or a minimal test file — no framework required). Trivial one-liners need
none.

## Output pattern

Code first, then at most three lines on what was skipped and when to add
it back. If the explanation is longer than the code, delete the
explanation.

## Intensity levels (for reference, not auto-switching)

- **Lite** — build as requested, name one lazier alternative in one line.
- **Full** (default here) — enforce the ladder, stdlib/native first,
  shortest diff and explanation.
- **Ultra** — YAGNI extremist, deletion before addition, ship the
  one-liner and challenge remaining requirements.

---

**Scope note:** this file makes Ponytail's rules apply automatically to any
Claude session that works in this repo. It does not install the actual
Ponytail plugin (that requires running `/plugin marketplace add
DietrichGebert/ponytail` then `/plugin install ponytail@ponytail` yourself
in an interactive Claude Code session) and it has no effect outside this
repo — there's no single global file a Claude Code web/remote session can
reach to make a rule apply to every project and conversation account-wide.

---

# House rules: taste-skill (anti-slop frontend design)

Source: [`Leonxlnx/taste-skill`](https://github.com/Leonxlnx/taste-skill),
`skills/taste-skill/SKILL.md`, fetched directly. Applies to any UI work
(HTML/CSS/JS, Artifacts, React) in this repo. Scope per the skill's own
disclaimer: landing pages, portfolios, redesigns, and general UI — not
dashboards, data tables, or multi-step product UI, where official design
systems (Material, Carbon, Fluent, shadcn/ui, etc.) take precedence instead.

**Read the room before coding.** State a one-line design brief and set
three dials 1-10 before touching markup: `DESIGN_VARIANCE` (how far from a
generic template), `MOTION_INTENSITY` (how much justified motion), and
`VISUAL_DENSITY` (sparse vs. data-rich). A minimalist brief and an
experimental-agency brief should not land on the same defaults.

**Non-negotiable bans (Section 9, AI-tell list) — fail closed, not warn:**
- **Em-dashes (`—`) anywhere. Zero tolerance, Section 9.G, the single
  most-violated tell.** Use a period, colon, comma, or plain hyphen instead.
- Serif as the *default* display/headline font — "very discouraged" unless
  the brief explicitly calls for an editorial/branded serif. Named repeat
  offenders: Fraunces, Instrument Serif.
- Neon glows, pure black, over-saturated gradients, default AI-purple glow.
- Div-based fake screenshots or fake product UI — "the #1 LLM tell."
- Generic placeholder names ("Jane Doe", "Acme Co"), fake avatars.
- Filler marketing verbs: "Elevate," "Seamless," "Unleash," startup-slop
  branding.
- Version labels in heroes, section-number eyebrows, locale strips, scroll
  cues, spec tables with a border on every row.

**Other discipline:**
- One locked accent color per page/artifact — don't let it drift section to
  section.
- Warm-beige + brass + oxblood ("premium consumer") palette is banned as a
  *default* reach — vary the palette across projects instead of repeating it.
- Max 1 uppercase eyebrow micro-label per 3 sections.
- Motion must be motivated — every animation names one reason (hierarchy,
  storytelling, feedback, state change) or gets cut. (See the animate skill
  below for the actual mechanics once motion is justified.)
- Hero constraints (where a hero applies): max 2-line headline, max ~20-word
  subtext, max 4 text elements total, no more than `pt-24` top padding.
- One official design system per project where the brief calls for one —
  install the real package, don't hand-roll its CSS.
- Real or generated images first, seeded placeholder services second,
  placeholder slots third — never a fake screenshot built from divs.
- Server Components by default in React/Next projects; isolate interactivity
  in `'use client'` leaves. Design dark mode from the start, not bolted on.
  Audit WCAG AA contrast, focus states, alt text.
- Pre-flight is mechanical: every rule above is a checkbox, not a vibe — a
  single failed box means the work isn't done yet.

---

# House rules: animate (emilkowalski/skills)

Source: [`emilkowalski/skills`](https://github.com/emilkowalski/skills),
`skills/animate/SKILL.md`, fetched directly. Applies whenever this repo
adds or edits any CSS/JS animation or transition.

**Gate every animation through two questions before writing any code:**
1. **Frequency test** — how often does this fire?
   - 100+ times/day (typing, scrolling) → never animate.
   - Dozens of times/day (button press, chip toggle) → imperceptible motion
     only.
   - Occasional (modal, drawer, form step) → standard animation is fine.
   - Rare (onboarding, first success, celebration) → a delight budget
     applies.
2. **Purpose naming** — name the function: feedback, spatial consistency,
   state indication, preventing a jarring change, explanation, or delight.
   If you can't name one, don't build it.

**Tool ladder — use the cheapest tool that works, in this order:**
1. CSS transitions (hover, press, controlled state toggles)
2. CSS `@starting-style` (entry animation on mount, no JS)
3. CSS animations (predetermined motion, stays smooth under load — CSS runs
   off the main thread)
4. WAAPI (`element.animate()`) — programmatic control, CSS-level performance
5. A motion library — only for springs, layout shifts, exit animations, or
   gesture-driven values. Don't reach for one to do a fade.

**Property and easing rules:**
- Animate `transform` and `opacity` only in production animation. Never
  `width`/`height`/`margin` (forces layout recalculation).
- Never scale entrances from `scale(0)` — use `scale(0.9–0.97)` paired with
  opacity instead.
- Entrance/exit: `cubic-bezier(0.23, 1, 0.32, 1)`. On-screen movement:
  `cubic-bezier(0.77, 0, 0.175, 1)`. Drawer-style gestures:
  `cubic-bezier(0.32, 0.72, 0, 1)`. Never `ease-in` on a UI element — it
  starts slow, delaying the moment the user is actually watching.
- Durations: button feedback 100-160ms, tooltips/small popovers 125-200ms,
  dropdowns/selects 150-250ms, modals/drawers 200-500ms. Stay under 300ms
  for standard UI without a stated reason. Springs (bounce 0.1-0.3) suit
  drag-with-momentum and interruptible gestures.

**Never-ship checklist (automatic rejection):**
- `transition: all` — name the specific properties.
- `scale(0)` entrances.
- `ease-in` on UI elements.
- Animation triggered by keyboard input at typing frequency.
- Durations over 300ms with no stated justification.
- Animating `width`/`height`/`margin`.
- Hover effects not gated behind `@media (hover: hover) and (pointer: fine)`
  (causes stuck states on touch).
- Missing `prefers-reduced-motion` handling — ships with every animation,
  not as a follow-up; keep opacity/color transitions, drop transform-based
  movement.

**Report every animation the same way:** the gate result (frequency tier +
named purpose, or the rejection reason), the technical ingredients
(tool/property/curve/duration), and how to feel-check it (slow-motion
playback, frame-stepping in devtools) rather than trusting it from the code
alone.

---

**Scope note (both sections above):** same caveat as Ponytail — this makes
the rules apply automatically to Claude sessions working in this repo, not
account-wide. Ask for the same block to be added to another repo if wanted
there.
