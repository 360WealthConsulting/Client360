<!-- Keep this concise. Fill what applies; delete sections that don't. -->

## Summary
<!-- What and why, in a sentence or two. -->

## Scope
<!-- Code / docs / infrastructure / governance / register. -->

## Documentation impact
<!-- Which docs/areas change? Run: python3 scripts/docs/check_documentation_dod.py -->

## Canonical-source impact
<!-- New/changed canonical home (Git vs Confluence)? One home per page. -->

## Publication Register impact
<!-- pages.yml rows added/changed? Regenerate: python3 scripts/registers/gen_crosswalk.py -->

## Confluence impact
<!-- Any Confluence change is separately authorized — describe or state "none". -->

## Taxonomy impact
<!-- Area codes / D10 taxonomy touched? Only 26 codes + SHARED + GOV are valid. -->

## Security & client-data review
<!-- Confirm no secrets, keys, tokens, or client data (SSNs, account numbers) added. -->

## AD-5 / compliance impact
<!-- Any regulated (suitability / replacement-1035 / licensing / CE) content? It stays gated. -->

## Testing & validation
<!-- Tests run; DoD checker output; register validation. -->

## Rollback considerations
<!-- How to revert if needed. -->

## Reviewer checklist
<!-- Reviewer independent of author where possible. -->

---

### Definition-of-Done (advisory in Release 0.11.0)
- [ ] Canonical home identified (Git *or* Confluence — exactly one)
- [ ] `docs/registers/pages.yml` updated when register content changed
- [ ] Generated crosswalk refreshed (`python3 scripts/registers/gen_crosswalk.py`)
- [ ] DoD checker run (`python3 scripts/docs/check_documentation_dod.py`)
- [ ] No secrets or client data added
- [ ] Confluence changes (if any) were **separately authorized**
- [ ] No regulated content published while a `compliance_gate` is active
- [ ] Michael Shelton's business approval is **not** represented as regulatory certification
- [ ] Legacy unresolved (360OS/Atlas) pages remain non-canonical unless reconciliation was separately approved

> The documentation DoD is **advisory** in Release 0.11.0 (decision D6) — findings inform review but
> do not block merge. Blocking enforcement is deferred to a later authorized phase.
