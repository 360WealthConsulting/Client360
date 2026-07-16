# Ruff legacy lint backlog

Ruff was adopted in Release 0.9.13 (Phase 2). This codebase predates it, so it
carries a lint backlog. That backlog is **baselined, not fixed** — recorded once
and frozen — so the linter can gate new code without a large, risky reformat of
working code in a foundation release.

- **Config:** `pyproject.toml` (`[tool.ruff]`)
- **Machine baseline:** `docs/ruff-baseline.json` — counts per `(file, rule)`
- **Gate:** `scripts/ruff_gate.py`, run in CI. Fails only when a change **adds** a
  violation. See that file's docstring for the exact ratchet semantics.
- **Burndown:** this is the tracked debt. Fixing findings only lowers counts;
  after fixing, run `python scripts/ruff_gate.py --update` to lock in the win.

## Baseline snapshot (0.9.13 Phase 2)

**643 findings across 160 of 214 files.** None are fixed in 0.9.13.

| Rule | Count | Fix | What it is |
|---|---:|---|---|
| `I001` | 168 | auto | unsorted imports |
| `UP045` | 161 | auto | `Optional[X]` → `X \| None` (PEP 604) |
| `UP017` | 102 | auto | `datetime.timezone.utc` → `datetime.UTC` |
| `B904` | 73 | manual | `raise` without `from` inside `except` |
| `F401` | 48 | auto | unused import |
| `UP007` | 33 | auto | `Union[...]` → `X \| Y` |
| `UP035` | 18 | manual | deprecated import |
| `F841` | 9 | manual | unused local variable |
| `UP006` | 7 | auto | `List` → `list` (PEP 585) |
| `UP031` | 6 | manual | `%`-formatting → f-string/`.format` |
| `B007` | 5 | manual | unused loop variable |
| others | ~11 | mixed | `B905`, `E401`, `E402`, `E731`, `E741`, `UP033`, `UP012`, `UP009` |

Roughly **~85% are auto-fixable** (`ruff check --fix`); the rest (`B904`, `F841`,
`UP031`, some `UP035`) need judgement.

### Most-affected files (burndown targets)

| File | Findings |
|---|---:|
| `app/routes/benefits.py` | 41 |
| `app/routes/work.py` | 29 |
| `app/routes/exceptions.py` | 26 |
| `app/routes/workflows.py` | 18 |
| `app/routes/tax_returns.py` | 17 |
| `app/services/relationships.py` | 16 |

## Deliberately excluded rules

Not backlog — decisions, documented in `pyproject.toml`:

- **`E701`/`E702`** (573 occurrences) — the codebase's compact one-liner house
  style. A formatter's concern, not a lint bug; enforcing it would be a stylistic
  war, not a quality gate.
- **`B008`** (333) — function-call-in-default-argument. This is how FastAPI's
  `Depends()` / `Query()` are written; idiomatic here, not a defect.
- **`E501`** (line-too-long, ~3500) — not in Ruff's default select; left out to
  avoid fighting the codebase's long-line style. A future `ruff format` adoption
  is the right tool for line width.

## How to burn this down

Per module, in its own PR (never mixed with feature work):

```
ruff check app/routes/benefits.py --fix     # auto-fixable subset
# hand-fix the manual ones (B904, F841, ...)
python -m pytest -q                          # via scripts/test.sh run
python scripts/ruff_gate.py --update         # lower the baseline
```

Tracked in the Phase 2 backlog issue.
