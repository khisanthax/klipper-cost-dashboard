# Dashboard workflow guide

## Primary filament measurement

Open **Settings → Other → Filament measurement** and choose **Weight (g/kg)**
(the default) or **Length (m)**. This installation-wide display preference is
stored by the active backend, including canonical SQL settings in SQL-only mode,
and applies across navigation, browsers and restarts. It does not change pricing
units, profiles, raw measurements or any recorded cost.

Ordinary filament cells show one primary quantity. Weight uses grams below
1,000 g and kilograms at or above 1,000 g, with two decimal places. For example,
145.45 m at a verified 3 g/m displays **436.35 g**. Open **Measurement details**
beside the value to inspect original length, recorded or estimated weight,
conversion factor, source and material profile. Details begin closed.

Table filenames use a two-line preview so long names do not turn an ordinary row
into a tall block of wrapped text. Open **Full filename** to read and select the
entire original name. The preview does not rename the job or remove it from
search, selection labels or sorting.

Length is unknown when an entry has weight but no verified conversion. A missing
factor is never replaced with a generic material assumption. Manual and planned
items can use their reviewed estimate conversion or an explicit project-material
snapshot. Mixed-material totals add each item's known consumption separately and
identify missing measurements. Inventory stays stored as integer milligrams;
length balances require that physical spool's verified factor. Stock-entry and
pricing forms retain their explicitly labeled units regardless of this preference.

## Project JSON download

On SQL-only installations, open a project and choose **Download project JSON**. The [versioned schema](project-export-schema.md) separates recorded job prices, recoverable original pricing, explicit overrides, manual work, planned items and applied project-price snapshots. Unknown stored values remain JSON null. The document uses one consistent SQL read snapshot and preserves stored numeric precision and original units. The download uses the dashboard's normal access boundary and contains project data, so store it appropriately. This export is separate from a coordinated backup and does not change the project or its history.

Consult root `LOGBOOK.md` for verified release/deployment state.

## Prices, consumption and weight

In Settings → Profiles, create a filament profile using **Spool price** and **net
filament per spool**, initially 1,000 g. For a $15 PETG spool enter 15 and 1,000;
choose density plus input filament diameter (for example, verified PETG density
1.25 g/cm³ and 1.75 mm filament) or a verified g/m factor. Nozzle diameter is not
the input filament diameter. Different materials/manufacturers can use different
profiles. Explicit $/kg, $/g and $/m remain available. Existing prices keep their
original units; choosing a new unit is an explicit change.

Assign filament and hourly profiles in Printer Settings. Zero hourly rates are
valid. Preview historical changes in Recalculate Center: choosing/saving a
profile for a printer affects future pricing, not existing saved job prices.

Weight is shown before supplementary length. Conversion source/factor is shown
in rows, tooltips and previews. Recorded weight takes precedence; otherwise the
job's factor/density, then its recorded profile as a labelled current fallback,
is used. Unknown or invalid measurements are shown as unknown. A known-mass
subtotal excludes unknown measurements and identifies their count; it does not
declare them zero. Saved financial costs are not recomputed merely to show weight.

## Project costs

- **Saved Historical Cost:** current persisted tracked-job costs; first original
  prices are separately recoverable after a deliberate historical recalculation.
- **Project-Adjusted Cost:** a reviewed/applied project-only snapshot. It does not
  modify tracked historical jobs; changing project defaults requires a new preview
  and apply to replace that snapshot.
- **Projected Cost:** hypothetical active planned items, excluded from actual costs.

A project can select a filament profile or custom explicit-unit price. Selected
profile pricing takes precedence on Save and is copied into project defaults.
Manual/planned costs use current saved project defaults; an exact total override
stays exact, with a signed adjustment reconciling its time/material components.
Clear the optional project prices to return to defaults. Profile changes elsewhere
require selecting/saving again; existing project snapshots stay unchanged.

Upload a planned G-code for metadata or supply explicit estimates. Editing a
default-priced plan recomputes its estimate; an explicit total stays an override.
Convert → Manual moves the estimate into actual manual work and excludes that
plan from the active projection. Deleting the converted plan does not delete its
manual job. Deleting a whole project removes its manual jobs, plans and project
pricing snapshots, but unassigns and retains tracked print history. Review the
confirmation and retain backups before destructive operations.

## Historical recalculation

Select jobs and the intended profiles/overrides, choose Preview changes and
inspect saved versus proposed costs, weight, conversion and units. Applying
changes updates current saved historical prices and retains original snapshots.
Changed selections, prices, profiles or jobs invalidate the review. Repeated runs
distinguish changed from already matching records; a matched SQL update count
alone is not reported as verified success. See [pricing recovery](pricing-and-thumbnail-recovery.md)
for original-price restoration. Live tracking is not a substitute for a finalized
historical job.

## History selection and filters

Selection belongs to one results page in one browser tab. It survives a refresh
of the same URL during that tab's session. Sorting the displayed rows preserves
their selected job IDs. Changing filters, page or page size clears selection;
hidden pages are never silently included. Clear Selection removes every selected
row on the page. A successful form submission clears remembered selection.
Closing the tab ends the session; there is no permanent cross-session selection.

Select All means selectable rows on the current page, not the whole database.
Complete Selected, Recalculate cost (selected), and Delete Selected submit stable
job IDs; deletion requires confirmation. Recalculation opens a preview first.
The selection column stays visible during horizontal scrolling. Focus the table
and use arrow keys to reach other columns. Printer-card Today totals are independent
of History filters; Summary/Reports explicitly follow their selected range.

## Live observations versus retained tracking

Cards poll read-only Moonraker telemetry without reloading the page or wiping
forms/selections. Disconnected/invalid telemetry is unverified, not idle. Loaded
KCD identities are checked when reported; a mismatch stops treating that service
as this printer. If identity is unavailable, verify the instance configuration.
This is configuration identity validation, not cryptographic device attestation.

Retained KCD events survive server restart. If Moonraker now reports a different
filename or a terminal state, the UI explains the disagreement instead of
continuing to present stale elapsed cost as current. It does not automatically
clear tracking, merge jobs, cancel a printer or fabricate a completion record.
Clear removes retained KCD tracking only and does not log history or send a printer
command. Use it deliberately after reviewing the discrepancy.

Live estimates use current printer pricing and verified current consumption;
pause policy determines whether paused time is billed. The estimate includes
material plus smoothly accumulating machine time; final logged billing retains
the established minimum-hour rule. Missing pricing is reported unavailable,
not free. Telemetry polling does not automatically refresh historical totals:
reload the dashboard when you need newly completed history or changed defaults.

## Individual printer pages

Open a printer card from the dashboard to see that printer's current job,
history, and operating trends. The printer's loaded KCD identity must match
before the page shows live measurements. Elapsed print time comes from Moonraker.
Remaining time extrapolates active print time from the reported progress and
is unavailable when Moonraker does not supply enough information.
Missing elapsed time or filament readings stay unavailable. A reported zero is
shown as zero. Pagination retains the selected printer, dates and comparison.

History defaults to the last 30 local calendar dates. Choose inclusive start
and end dates for a range of up to 366 days. Enable **Compare previous period**
to see totals for the immediately preceding range with the same number of dates.
Workload uses recorded hours of jobs in the selected range divided by elapsed
calendar hours. Missing records and jobs crossing a date boundary affect this
estimate. Use the outcome chart to see the counts for completed, failed,
canceled, and other jobs.

The page displays saved prices and the known subtotal for each cost component.
Missing prices appear as **Unpriced**. Weight uses the saved job measurement or
its saved conversion data, with unknown measurements counted separately.

## Accessibility and administration

Use Skip to main content, named form controls, visible focus indicators and
keyboard-focusable scrollable tables. Native dialogs confirm destructive actions;
project edit dialogs support Escape and labelled titles. Small navigation targets
have been enlarged and contrast corrected while retaining KCD's Bootstrap styling.

Settings → Other stores shared display preferences and provides Backup now.
SQL-only automatic backup scheduling remains intentionally disabled. Backups
coordinate a consistent SQLite snapshot with permanent thumbnail objects. Test
restores in isolation; never overwrite newer production jobs to recover an image.
See [recovery](installation-recovery.md) and the [audit evidence](dashboard-audit-v0.7.3.md).
