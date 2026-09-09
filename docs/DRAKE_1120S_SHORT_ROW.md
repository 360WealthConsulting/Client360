# The Drake 2021/2022 1120S short row

A runbook for the malformed export shape, the normalization rule that corrects it, why the fix needs a
migration as well as an importer change, and what happens to a short row the rule does not recognise.

## The defect

`csv.DictReader` maps values to header names **by position** and tolerates a row that is short without
warning. The Drake `CLIENT.CSV` exports for 2021 and 2022 emit **122 fields instead of 123 for every
1120S return**, so every value from the omission onward landed one column early.

| year | header cols | rows with 123 | rows with 122 | well-formed 1120S rows |
|---|---|---|---|---|
| 2021 | 123 | 691 | **52** | **0** |
| 2022 | 123 | 691 | **57** | **0** |
| 2023 | 123 | 855 | 0 | 73 |
| 2024 | 123 | 735 | 0 | 60 |
| 2025 | 123 | 609 | 0 | 51 |

52 + 57 = **109**, exactly the rows carrying `return_type IS NULL`. In 2021 and 2022 there is not one
well-formed 1120S row — the short row *is* the 1120S shape in those exports, and Drake fixed it from
2023 onward. That is why every affected row recovers the same form; it is not an assumption.

Header indices 49-54, one well-formed row and one malformed row from the same 2021 file:

```
idx           49        50        51       52       53       54
header      Misc5      AGI     Prep_Fee  Wh_Ral    Paid     Type

well-formed   ''     ' 9244 '  ' 850 '   ' 0 '    ' 0 '   '1065'    (123 fields)
malformed  ' 6294 '  ' 650 '   ' 0 '     ' 0 '   '1120S'    ''      (122 fields)
```

### It was never only `return_type`

Every column the importer reads from a header position at or after the omission took its neighbour's
value. Measured across the 109 rows:

| column | wrong on | column | wrong on |
|---|---|---|---|
| `return_type` | **109** | `federal_product` | 77 |
| `agi` | **102** | `federal_ack_date` | 77 |
| `preparer_fee` | **97** | `federal_ack_code` | 79 |
| `complete_date` | **109** | `state_product` | 79 |
| `prepare_date` / `review_date` / `approved_date` | 0 (shifted, both values empty) | `state_ack_date` / `state_ack_code` | 79 |

Columns **before** the omission — `FS`, the taxpayer and spouse name fields, `TP_DoB`, `Prep`, and
crucially `TP_Social` and `SP_Social` — were always correct, which is why every affected row still
carries the right identifier hashes and maps cleanly back to its source line.

## The normalization rule

`app/importers/drake_client_csv.py`, `normalize_row(header, values)`. It restores the single missing
structural slot on the **raw row, before any mapping to a column name**, so one rule corrects every
displaced field at once. It is structural — never a name, a year, or a per-column patch.

A row is normalized only when **all** of these hold:

1. it is short by exactly one field (`len(values) == len(header) - 1`);
2. the header carries `Paid` immediately followed by `Type` — the adjacency the shift exploits;
3. the value that landed in `Paid` is a recognised Drake return form
   (`1040`, `1040NR`, `1041`, `1065`, `1120`, `1120S`, `990`);
4. the value that landed in `Type` is blank;
5. there is at least one empty field before the displaced block to restore into.

### Where the missing field goes

The omitted field **cannot be located exactly**, and the module says so rather than pretending. It
lies in header band 36-49 (`Receipt` .. `Misc5`): indices 37-48 are empty on all 109 rows, and an
omitted empty field leaves no trace of itself.

That ambiguity is harmless, because every choice inside a run of empty fields produces the identical
canonical row. The rule scans left from `Paid` over the displaced values, then over the empty run
behind them, and restores at the start of that run. `Receipt` and `Fee` straddle the one position the
omission cannot be pinned to, and neither is parsed into a column, so the ambiguity reaches nothing.

## Fail closed

A short row that does **not** match the proven shape is:

* **not guessed at** — no realignment;
* **not dropped** — it is mapped exactly as it always was, so behaviour does not regress;
* **reported** — `read_client_rows` returns it in `anomalies` and the import prints
  `N short row(s) NOT NORMALIZED — shape not recognised, needing review`.

Silently normalizing an arbitrary 122-field row would be the same class of mistake as the defect being
fixed.

## Why a migration is required as well

`return_type` is an input to the row's identity:

```
return_identity_key = SHA-256( tax_year | taxpayer_hash | spouse_hash | return_type | filing_status )
```

and that key is the `ON CONFLICT` target of the returns upsert — documented in
`app/importers/drake_returns.py` as *"an identified row's key IS its conflict target, so it cannot
change."* So the two halves of the fix are inseparable:

| what you do | what the next import does |
|---|---|
| importer fix only | computes a key the stored rows do not have, matches nothing, **inserts 109 duplicates** |
| `return_type` fixed in the database only | matches the unchanged old key and **writes NULL back over the repair** |
| **both, in one release** | resolves each existing row through its new key and updates it in place |

Migration **`drake03`** (`dbi01` → `drake03`) re-reads the 109 rows from their untouched `raw_data`,
using `CLIENT_EXPORT_HEADER` to recover the original value order (JSONB sorts its keys), runs the same
`normalize_row`, re-derives the affected columns with the same parse helpers, and writes
`return_type` **and** `return_identity_key` together.

* **Scope is frozen**: 109 exact primary keys, each paired with the identity key that row must still
  carry. There is no `WHERE return_type IS NULL` sweep.
* **Fail closed**: it refuses entirely if a row has drifted, if two rows would be given one key, if a
  target key is already owned, or if anything outside the cohort is in the wrong state. No row is
  inserted or deleted.
* **No-op where the cohort was never imported**: a fresh development database, CI and a restore
  rehearsal upgrade through the revision without ever having imported Drake. A *partial* cohort is
  drift and still fails closed.
* **No client data in version control**: the migration embeds only primary keys and SHA-256 identity
  keys. Every figure comes from `raw_data` at run time.
* **Timestamps**: `drake_client_returns` has no `updated_at`, no `imported_at`, no audit column and no
  trigger. `source_updated_at` is the export file's mtime and is neither re-read nor written. Nothing
  changes in either direction.
* **Downgrade is lossless**: it re-derives the pre-migration values by mapping the same untouched
  `raw_data` *without* normalization — which is precisely what produced them.

## Operating order

```
1. deploy the release (importer fix + drake03 together)
2. alembic upgrade head
3. re-import 2021 and 2022; expect inserted = 0
     .venv\Scripts\python -m scripts.import_drake_all_years --year 2021 --year 2022
4. separately authorized, later: drake_identity rebuild, DBI, ESL, person-side cleanup
```

Step 3 is year-scoped deliberately. The driver imports every discovered year when given no `--year`,
and re-importing 2023-2025 as a side effect of repairing 2021/2022 is a wider blast radius than the
operation calls for.

### The driver names its target before writing

`load_dotenv(r"C:\Client360\app\.env")` runs at module scope and fills in any variable the shell has
**not** set. Unsetting `DATABASE_URL` or `MICROSOFT_TOKEN_KEY` to make an invocation "safe" therefore
does the opposite — the production env file supplies both. The driver prints
`Target database: <name>` before it writes anything, so the target is never in doubt.

## Rollback after a re-import — read this before relying on `drake03.downgrade()`

`drake03` never rewrites `raw_data`, which is what lets its upgrade and downgrade both derive their
values from the preserved payload. **A subsequent Drake source re-import legitimately refreshes
`raw_data`**: the corrected importer writes the normalized mapping, so the payload no longer describes
the displaced original.

After such a re-import the historical values cannot be reconstructed from the live row, and both
directions of the migration refuse rather than invent them:

| direction | what it finds | what it does |
|---|---|---|
| `downgrade` | `return_type` reads back as `1120S`, not NULL | raises *"does not re-read as a NULL return_type"* |
| `upgrade` (re-run) | `Paid` no longer holds a form token | raises *"row does not match the proven short-row shape"* |

That refusal is deliberate and is asserted by
`tests/test_drake_1120s_short_row_repair.py::test_downgrade_refuses_once_a_re_import_has_refreshed_raw_data`.
**Do not relax it, and never synthesize historical values to make the downgrade succeed.** Once a
re-import has run, the **verified pre-migration backup is the authoritative rollback mechanism**, and a
restore is a separately authorized operation.

## Downstream

`is_personal_return_type` answers `False` for both `None` and `1120S`, so **person linking, document
ownership, filing and portal access are unchanged**. What does change, once identities are rebuilt:

* 13 identifiers whose every return was NULL-typed stop being `unknown` and classify as
  `business_entity`;
* one identifier that is both an 1120S taxpayer and a 1040 spouse stops being *confidently*
  mis-classified as a natural person and becomes `conflicting_subjects` with `requires_review = True`.

That last one is the correct answer — one identifier carrying both a business return and a personal
return is a real irregularity — and it is deliberately **not** auto-resolved. Normalization surfaces
it; a human decides it.

## Tests

* `tests/test_drake_client_csv_short_row.py` — the rule itself, against real 123-column shapes: every
  displaced column recovered, well-formed rows byte-identical, and seven distinct ways of failing
  closed.
* `tests/test_drake_1120s_short_row_repair.py` — migrate then re-import twice (`inserted = 0`, no
  duplicates), the un-migrated duplication demonstrated rather than asserted in prose, lossless
  downgrade, drift refused, mixed-year and legitimate election-change histories preserved, and the two
  production cases the fix was traced from.
