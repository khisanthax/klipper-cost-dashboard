# Measurement consistency and price-review acceptance

## Scope and storage contract

Settings → Display → Filament measurement selects Weight (g/kg, the default) or
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

## Project defaults and project-only pricing

Save Project Defaults saves pricing for this project's manual and planned work;
existing completed-print prices are not changed. Selected filament profiles supply
project-specific pricing without editing the reusable profile or printer defaults.

Preview New Project Costs submits the current pricing inputs without saving them.
Review Original Project Total / Change / New Project Total and the four-column
Print / Original / Change / New table. Original is saved historical pricing for
completed prints; new is a proposed project-only snapshot. Manual actual costs
remain on saved defaults and appear in both totals. Planned/Projected Cost is
hypothetical and excluded. Other print outcomes are not repriced: their existing
prices remain in both totals, and unknown prices make the subtotal explicitly
partial. Previously applied snapshots for non-completed jobs are retained until
Use Original Prices is chosen. No outcome is inferred from duration or filename.

Apply to Project writes only completed-print project snapshots and an audit event.
A signed 15-minute review binds the project, normalized unsaved inputs, profiles,
historical data, membership and manual actual costs. Changed sources require a new
preview. Project defaults and historical jobs remain unchanged. Projects identifies
the active Saved Historical Cost or Project-Adjusted Cost view and warns when an
applied snapshot still uses previously reviewed inputs. Use Original Prices clears
only the snapshots. Calculation Details retain raw components, signed adjustments,
measurements and pricing provenance. Displayed cents reconcile across tracked and
manual components; full precision remains in stored snapshots.

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

## Project defaults and project-only prices

Save Project Defaults changes the project's manual/planned pricing defaults;
it does not update completed historical prints, printer defaults or the global
reusable profile. Preview New Project Costs evaluates the form's current inputs
against assigned tracked prints without implicitly saving those defaults.
Original Project Total / Change / New Project Total include current-default
manual work unchanged; planned work remains separate and hypothetical.
Tracked comparisons have four primary columns with optional calculation details.

Apply to Project stores only the reviewed project-adjusted snapshot after an
expiring signed review and current source/input checks. The original SQL job
prices remain intact. Use Original Prices removes that project-only view.
Applied snapshots identify their active pricing view; later defaults changes
show that previously reviewed snapshot prices are still in use and offer a new
preview. Mixed/unknown original prices are clearly partial rather than zero.

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
- `python -m unittest discover -s tests -p 'test_project_pricing_review.py'`:
  unsaved inputs, signed/expired/cross-project reviews, unknown and zero originals,
  manual subtotals, completed-only scope, retained non-completed snapshots, stale
  sources and overlay-only apply/clear without historical or profile writes.
- `python tools/browser_project_pricing_regression.py`: real Project Save Defaults,
  unsaved preview, simple comparison, Apply to Project, stale notice, Use Original
  Prices, keyboard details and desktop/tablet/mobile layouts with exact SQL checks.
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
