# Localization Epic B — fr/de/es and Anti-Drift Tooling — Design

Date: 2026-08-25
Status: Approved (scope and A→B ordering confirmed during epic brainstorm; remaining
decisions recorded here)

## Problem

Port-Light ships four locales (en, zh-CN, zh-TW, ja) with parity enforced only as key-tree
equality. Nothing detects placeholders drifting out of sync ({time} present in en but
dropped in ja), keys that no code references anymore, or the toil of hand-syncing five-plus
files when copy changes. Users asked for French, German, and Spanish at the same quality
bar, plus an audit of the existing four.

## Decisions

1. Translations are written by the implementing agent against a per-language glossary in
   the task briefs — no machine-translation service (privacy: no source text leaves the
   machine).
2. The untranslated-copy report is informational tooling output, not a CI gate: some
   strings legitimately match English (proper nouns, protocol names).
3. Plural forms stay out of scope: current UI copy has no count-dependent strings; the
   interpolation mechanism ({var}) is unchanged.
4. Audit fixes are defect-driven only — no stylistic churn on healthy strings.

## Tooling (lands first, protects everything after)

New checks inside `tests/test_i18n.py`:

- **Placeholder parity**: for every leaf whose English value contains `{…}` tokens, every
  locale's value for that key must reference exactly the same token set. Catches silent
  truncation like a translator dropping `{time}`.
- **Unused-key detection**: every leaf key in en.json must be referenced somewhere in
  `frontend/` sources — via `data-i18n(-placeholder|-title|-aria)="…"`, `t('…')`,
  `tx('…prefix')` — or live under an allowlisted dynamic prefix
  (`choice.`, `settings.fields.`, `settings.groups.`, `localeName.`, `localeNative.`,
  `settings.editor.vars.`, `settings.source.`). Assertion: no orphans. Kills the
  dead-key accumulation seen in earlier epics.

New script `scripts/locale-scaffold.py`:

- Adds every en-only key to the other locale files in place (sorted, copying the English
  value so files stay valid), prints a per-file summary.
- `--check` mode: exit 1 when any locale is missing keys (CI-able companion to the
  parity test).
- `--untranslated`: lists keys whose value is byte-identical to English per locale
  (review aid only).
- Stdlib only; deterministic output.

`CONTRIBUTING.md` gains a short "Adding UI copy" section: edit en.json → run scaffold →
translate the copied values → tests enforce the rest.

## Translations

Three new files mirroring the full en tree (~450 keys each): `frontend/locales/fr.json`,
`de.json`, `es.json`. Per-language glossaries with fixed terminology live in the task
briefs (port/used/configured/free/occupancy/palette/lease/grid families). Rules baked
into every brief:

- `{placeholder}` tokens reproduced exactly; no reordering that drops one.
- Formal register throughout (vous / Sie / usted).
- Technical tokens unchanged: TCP, UDP, HTTP, JSON, YAML, Docker, Compose, MCP, API,
  SSE, TTL, hex, Port-Light.
- Punctuation follows each language's convention while matching the English
  sentence-shape (no added/missing sentences).

## Runtime wiring

- `backend/settings.py` locale choices += `fr`, `de`, `es`.
- `frontend/i18n.js` SUPPORTED += the three codes; `matchLocale` already does
  case-insensitive exact then prefix matching, so `fr-FR` resolves via existing logic.
- Each locale file gains its own endonym in `localeNative`
  (`Français` / `Deutsch` / `Español`) and exonym in `localeName`; all six files share
  both maps (existing test enforces identity).
- `tests/test_i18n.py` CODES tuple grows to the six codes — every existing check
  automatically extends.
- README.md + README.zh-CN.md language mention updated; CHANGELOG Unreleased bullet.

## Existing-locales audit

After tooling exists, run placeholder-parity and unused-key detection over the current
tree, then a defect-focused pass over zh-CN/zh-TW/ja/en values (wrong term, broken
markup entity, truncated sentence). Fix real defects only; record anything debatable in
the ledger instead of churning.

## Privacy

No external translation services; nothing leaves the machine. Locale files carry UI copy
only.

## Testing

Tooling lands with its own pytest coverage (parity catch, orphan catch, scaffold add +
check modes). Translation tasks make the extended CODES parity suite green. Wiring keeps
backend settings tests green (new choices accepted, unknown still rejected). Full gates:
ruff, pytest, node suite.

## Out of scope

RTL languages, plural-form infrastructure, runtime locale switching beyond the existing
picker, translating docs/ (README.zh-CN remains the only translated doc by precedent).
