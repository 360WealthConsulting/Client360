# Relationship Intelligence Engine

The Relationship Intelligence Engine models Client360 as a typed graph rather
than a collection of isolated client records.

## Data model

`relationship_entities` is the graph-node registry. Person and household nodes
retain foreign keys to canonical records; businesses, trusts, estates, and
professionals are named entities with extensible JSON details.

`relationship_types` defines directed or symmetric edge semantics, inverse
labels, and categories. `relationships` stores typed edges with effective and
inactive dates, notes, confidence, provenance (`manual`, `imported`, or
`ai_inferred`), and active state.

This node registry avoids polymorphic relationship IDs without foreign keys and
allows every edge endpoint to have referential integrity.

## Household intelligence

`household_relationships` remains the membership source of truth and supports
multiple memberships. `is_primary_household` identifies one primary household
per person while `is_primary` continues to identify the primary contact within a
household. The legacy `people.household_id` is updated only for the primary
household for compatibility.

This supports divorced and blended families, adult children, shared ownership,
and business-oriented households without forcing a person into one household.

## Services and workflows

- `app.services.relationships` owns node creation, edge CRUD, graph generation,
  household expansion, and relationship search.
- Relationship additions and deactivations publish timeline events.
- `/people/{id}?tab=relationships` displays the graph and creation workflow.
- `/relationships/search` supports type and related-name queries for CPAs,
  owners, beneficiaries, relatives, and other connections.
- Advisor recommendations consume graph codes and entity types for estate,
  beneficiary, business, trustee, and CPA guidance.

## Architectural boundaries

- The graph stores facts and provenance; it does not make legal determinations.
- AI-inferred edges use the same model but should carry lower confidence and be
  reviewed before operational use.
- Symmetric relationships are displayed inversely but stored once.
- Named entity profiles are intentionally minimal in Sprint 10 and provide the
  extension point for future business, trust, estate, and professional modules.

## Migration sequencing

This sprint branches from `main`. If Sprint 8 or Sprint 9 migrations merge first,
rebase this branch and update its migration parent before merging so Alembic
retains one head.
