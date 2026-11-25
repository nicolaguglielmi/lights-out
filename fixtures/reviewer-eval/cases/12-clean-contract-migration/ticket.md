# 0112 — Drop retired temp_code column in contract cleanup phase

- Status: open
- Labels: migrations, cleanup
- Contract-After: 0001_init.sql

## Problem

The expand phase (`migrations/0001_init.sql`) shipped in an earlier release, and all readers/writers stopped touching `invoices.temp_code` in prior commits. This contract cleanup drops `temp_code`.

## Contract migration

0001_init.sql

## Acceptance criteria

Given `migrations/0001_init.sql` exists before `BASE` and `Contract-After: 0001_init.sql` is explicitly declared
When `migrations/0004_drop_temp_code.sql` runs
Then it drops `invoices.temp_code` cleanly as an authorized contract cleanup within `## Perimeter`.

## Perimeter

- `migrations/0004_drop_temp_code.sql`

## Notes

- Explicit contract cleanup referencing prior migration `0001_init.sql`; no new ADR is requested by this ticket (`RISK_REQUIRE_ADR=off`).
