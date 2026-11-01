# Pricing, historical evidence and permanent thumbnails

## Filament prices and conversion

New profiles default to spool price plus **net filament weight**, initially
1,000 g. A $15 / 1,000 g spool means $15/kg, not $15/m. Explicit $/kg, $/g and
$/m inputs remain available. Existing profiles retain their original unit and
numeric value; migration never reinterprets a legacy per-meter price.

Use a verified grams-per-meter factor or material density and **input filament
diameter**. Nozzle diameter does not determine the mass of input filament.

`grams per meter = π × diameter_mm² / 4 × density_g/cm³`

For PETG at 1.25 g/cm³ and 1.75 mm, the factor is approximately 3.0066023443 g/m.
Each manufacturer's profile can carry different conversion information.
Recorded per-job weight takes precedence over inferred weight. Otherwise use
the job's recorded factor/density, then its own profile as a clearly identified
current fallback. An explicitly selected replacement profile or conversion
override is shown in the preview. Unknown conversion blocks weight-based
recalculation; it does not silently assume that meters are grams.

Different jobs can use different materials/factors. A job reporting only one
aggregate length cannot reveal separate consumption of multiple materials:
supply its measured total weight or a verified effective g/m factor. Do not
infer material proportions from nozzle size or printer address.

## Three price views

* **Saved historical:** persisted job prices shown in History and Reports.
* **Project-adjusted snapshot:** an explicitly previewed/applied overlay for
  tracked jobs in that project. It leaves the jobs table unchanged. Manual jobs
  use project defaults and recorded grams; planned items remain hypothetical.
* **Hypothetical projected:** active planned items, excluded from actual costs.

Project settings do not automatically overwrite tracked-job costs. Open
**Preview project-adjusted pricing**, review every conversion and before/after
component, then apply the snapshot. **Use saved historical costs** removes the
overlay. A later default change needs another preview/apply. An applied snapshot
remains a snapshot; History/Reports continue to show the independently saved
job prices.

835.02 m × 3 g/m = 2,505.06 g = 2.50506 kg. At $15/kg, material is $37.5759.
With $142.63 time and zero adjustment, the displayed total is **$180.21**.
Signed residual adjustments and exact total overrides are retained and shown.
Displayed cents are allocated across component rows so displayed totals agree
with displayed job components. Full precision remains in the stored snapshot.

## Recalculate Center

This operation changes the current saved prices of selected historical jobs.
Selecting profiles must submit their current IDs **and** enabled Apply flags.
The actual controls belong to the POST form; obsolete hidden copies are gone.
The reported 15-job failure came from separate GET-filter and POST-action
forms: the latter submitted stale plan values despite newly selected controls.
The selected $0/hour and $15/kg production profile IDs were valid.

1. Select historical jobs and the intended pricing plan.
2. Choose **Preview changes**. Inspect saved/adjusted time, material, signed
   adjustment, total, grams, g/m, conversion source and explicit price unit.
3. Choose **Apply previewed prices**. Changed selection, profile prices or job
   data invalidates the preview. Active jobs are excluded.

History/Project bulk recalculation now enters this preview flow too. SQL apply
holds a write transaction, preserves the first original pricing snapshot,
writes only calculated fields, reloads and validates them, and commits the
before/after audit event in the same transaction. An ignored update or failed
audit write rolls everything back. Counts distinguish changed, validated,
already-matching and missing jobs. A matched SQL UPDATE alone is not success.

Recover original saved prices with the SQL CLI (dry run first):

```bash
python -m kcd pricing restore-originals --job-uid JOB_UID
python -m kcd pricing restore-originals --job-uid JOB_UID --apply
```

Repeat `--job-uid` for multiple jobs. Back up first. CSV compatibility retains a
private timestamped pre-recalculation CSV backup and uses an atomic replacement;
dual storage still cannot provide one transaction across CSV and SQLite.

## Permanent thumbnail archive

SQL-only print-start captures an available slicer image into
`data/thumbnails/objects/<sha256>.<extension>`. Live SQL metadata retains that
asset across restart. Completion/cancellation associates it with a stable job
UID in the same history transaction. Completion also attempts capture if start
had no image. Images live outside SQLite; SQLite stores references/provenance.

Dashboard, History, Reports and Projects prefer archived objects. The original
G-code, printer name, IP, Moonraker URL and temporary cache TTL are no longer
needed for an archived image. Historical remote retrieval is lazy so an offline
printer cannot block an entire project page. Failures distinguish missing
source, absent slicer thumbnail, changed source, authentication/network failures
and missing archived objects. Missing images cannot be reconstructed from
prices, lengths or a matching basename.

Recover older jobs (dry run by default):

```bash
python -m kcd thumbnails backfill --limit 2000
python -m kcd thumbnails backfill --limit 2000 --offline --apply
python -m kcd thumbnails backfill --limit 2000 --apply
# Optional: --printer Ender5P
```

Offline recovery requires a unique explicit saved job/cache token and timing
evidence. Reused filename tokens, conflicting images and caches refreshed after
the job are skipped. Online recovery requires the exact source path and rejects
a G-code modified after the historical job. This deliberately favors missing
images over assigning the wrong model to historical work. The result lists
counts by recovery/failure reason. The limit is bounded (1–10,000).

Cache eviction never visits permanent objects. Deleting a job removes its
reference, not shared objects. Administrative pruning is dry-run by default,
excludes live-job assets and uses a 24-hour grace period:

```bash
python -m kcd thumbnails prune
python -m kcd thumbnails prune --apply
```

Backups coordinate archive writes/pruning with the consistent SQLite snapshot
and include the complete object directory. Restore the database and its objects
together into an isolated data directory first; verify image bytes and database
integrity before a production restore. Never restore a SQLite file alone over
newer production jobs merely to recover an image.

## Deployment boundary

The dashboard remains a trusted-LAN administrative UI. Browser cross-origin
writes are rejected; printer APIs still require the rotated API credential.
Use an authenticated reverse proxy for access beyond that trusted network.
Static Bootstrap assets are pinned, integrity-verified and served locally so
LAN styling and controls do not depend on public CDN connectivity.

Production outcome and exact release/backup evidence are recorded in the
[completed audit](production-audit-2026-11.md#completed-release-and-production-verification).
