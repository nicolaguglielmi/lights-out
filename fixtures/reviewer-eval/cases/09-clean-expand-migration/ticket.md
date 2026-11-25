# 0109 — Add rollback-safe priority_tier column to orders

- Status: open
- Labels: migrations, schema

## Problem

Orders need an explicit `priority_tier` attribute with a backward-compatible default so older application revisions continue inserting rows safely during canary rollout and rollback.

## Acceptance criteria

Given `migrations/0001_init.sql` already defines `orders`
When `migrations/0003_add_priority_tier.sql` runs
Then it performs an expand-only addition (`ADD COLUMN priority_tier TEXT NOT NULL DEFAULT 'standard'` and `CREATE INDEX IF NOT EXISTS ...`) so the previous revision's `SELECT`/`INSERT` statements keep working.

## Perimeter

- `migrations/0003_add_priority_tier.sql`

## Notes

- Additive expand-only schema change; follows the existing expand/contract migration policy (`RISK_REQUIRE_ADR=off`), so no new ADR is requested by this ticket.
