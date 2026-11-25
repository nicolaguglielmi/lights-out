# 0106 — Retire deprecated legacy_tag column on orders table

- Status: open
- Labels: migrations, schema

## Problem

The `orders` table still holds the `legacy_tag` column. We want to retire it without breaking rolling canary deployments or rollback safety.

## Acceptance criteria

Given a rolling canary deployment where the previous revision still selects and inserts `legacy_tag`
When the migration runs
Then any schema change remains rollback-safe for the previous revision (expand-only unless a prior expand cleanup is explicitly declared with `Contract-After:` and no previous revision still uses the column).

## Perimeter

- `migrations/0002_drop_legacy_tag.sql`
