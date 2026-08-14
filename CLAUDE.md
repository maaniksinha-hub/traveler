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
