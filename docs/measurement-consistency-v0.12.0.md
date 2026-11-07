# Measurement consistency and price-review acceptance

## Scope and storage contract

Settings → Other → Filament measurement selects Weight (g/kg, the default) or
Length (m) for the whole installation. SQL-only stores `filament_measurement`
inside `user_settings.display_settings`. CSV/dual retain their display JSON
compatibility. No schema migration, raw-quantity rewrite, pricing-mode change or
historical recalculation accompanies this preference. Missing legacy preferences
read as Weight. Existing explicit table-column selections are retained; fresh
defaults use one primary consumption column with raw columns opt-in.

`core/measurement_display.py` is a read-only presentation layer. Saved recorded
and estimated weights take precedence over subsequent profile changes. Length
and weight can be derived only from associated saved factors, recorded geometry,
or explicitly associated project/reviewed-estimate conversion. Mixed subtotals
are summed per item, with missing coverage counted. Unknown is distinct from
zero. Physical stock stays integer milligrams; a spool without a conversion has
unknown length. Currency and canonical calculation/reconciliation remain in the
existing pricing engine.

All ordinary quantities have one primary value and closed native details.
Kilograms start at 1,000 g, with two-decimal half-up presentation. Full precision,
original millimeters, material/profile, factor and recorded/estimated provenance
remain in details. Long filenames have bounded previews and expandable selectable
full text. Explicit pricing and stock-entry units remain labelled independently.

## Recalculate Center

Select jobs and a plan, choose Preview Changes, review Original Total / Change /
New Total and the four-column price comparison, then Apply New Prices. Original
means the current recorded price before this operation, not a prior first-original
snapshot. Full precision and before/after components remain expandable.
Unpriced originals show Unpriced and an unavailable change; mixed originals have
an explicit known subtotal. Zero-cost originals remain $0.00. A zero monetary
change can still update different profile IDs. Editing inputs invalidates the
button immediately; server-side job/profile/selection fingerprints remain
authoritative. SQL transactions, audit events and original-price restoration
remain intact.

The old Recalculate table-column controls were removed because the compact picker
and price comparison now have fixed primary columns with expandable details.
Legacy stored column settings are retained for backward compatibility. Other
table column controls still work.

## Reproducible acceptance

- `python -m unittest discover -s tests -p 'test_measurement*.py'`: formatting,
  saved estimates/material geometry, mixed/unknown coverage, inventory factors,
  SQL/CSV/dual persistence, mode switching, restart, backup restore and exact jobs.
- `python -m unittest discover -s tests -p 'test_recalculate*.py'`: pricing safety,
  current/first-original distinctions, NULL/zero originals and recoverable apply.
- `python tools/browser_measurement_regression.py`: real Flask/SQLite/Moonraker
  fixtures with 525 jobs; both preferences across seven routes and desktop,
  tablet, mobile and 200% CSS zoom, manual/planned/estimate quantities, independent
  browser context and restart persistence, closed keyboard details, bounded
  filenames and exact business-data preservation. Normal negative-thumbnail-cache
  refreshes and report caches are excluded; permanent image associations remain
  compared exactly. Failures retain screenshots, traces and SQL differences under
  ignored `browser-artifacts/measurement`.
- `python tools/browser_pricing_regression.py`: real selection/preview/apply,
  stale plans, selected profiles, $0/hour + $15/kg, original snapshots/recovery,
  equal-cost profile replacement, component reconciliation and responsive review.
- Existing whole-dashboard/axe, history selection, thumbnail, isolated demo,
  installer/SSH/systemd/Docker and seeded upgrade/rollback gates remain required
  in Release Validation. Display summaries do not change inventory deductions.

Native browser-toolbar zoom, screen-reader certification, physical printer
commands and failure injection against production are not claimed. CSS zoom,
responsive viewports, automated accessibility and disposable failure fixtures are
the automated scope. Production checks must be read-only against saved history
after the established backup/restore/rollback and inactive-printer guards.

## Delivery status

Implementation and focused local tests are complete; final integrated browser
and Linux release-validation evidence belongs in LOGBOOK and GitHub checks.
Production was independently read-only verified on November 7 at v0.11.0 with
1,052 jobs, 318 objects and 438 archived references. No v0.12.0 deployment is
claimed by this candidate record. The actual deployment report must record its
fresh baseline, immutable image, backup, preservation and rollback evidence.
