# Pre-print estimates

Open **Projects → Calculate a pre-print estimate**. Upload an OrcaSlicer-compatible `.gcode` file or enter duration and weight manually. The request limit is 16 MiB. Only comment metadata from bounded head and tail samples is parsed. Uploaded commands are never sent to a printer.

Enter a machine rate per hour and choose a saved material profile or enter a spool price and net weight (default 1,000 g). Explicit prices per kilogram, gram, or meter are also available. The preview shows the effective price per kilogram and conversion factor. The calculator uses the planned-item one-hour minimum. Enter weight in grams if the slicer does not supply it. A slicer length in millimeters is converted only with verified grams per meter from the selected profile or the explicit conversion field. Missing weight leaves material and total cost unknown. Multi-extruder lists and malformed measurements are not treated as a partial total; enter a reviewed total weight instead.

Try different input rates to compare scenarios. An estimate does not create actual History or change saved prices. In SQL-only mode, a complete preview offers **Save reviewed estimate as planned item**. Choose the destination project and title. This explicitly saves the displayed total as the plan's cost override, rounds duration up to whole seconds, and retains raw components, units and assumptions in its notes. Project component pricing can differ; its signed adjustment reconciles to the reviewed total. The review expires after 15 minutes. Repeating the same save is idempotent; changing its destination or values requires a fresh review. A database failure rolls back the insert and permits retry. Incomplete estimates cannot be saved through this action.

The existing project G-code upload remains available and uses that project's pricing settings rather than the calculator's reviewed override.

For JSON clients, send `POST /api/estimates` with these explicit units.

```json
{
  "duration_seconds": 1800,
  "hourly_rate": 2,
  "filament_grams": 25,
  "pricing_mode": "spool",
  "pricing_amount": 20,
  "net_spool_grams": 1000
}
```

Send `filament_profile_id` instead of a manual material price to use a profile. The legacy explicit `filament_price_per_kg` API field retains its original units. A multipart request can also include a `gcode` upload and optional `grams_per_meter`. JSON requests are limited to 16 KiB. The response identifies itself as an estimate and includes the time, material and total breakdown. Unknown material or totals are JSON null. The display values sum the rounded components; raw precision remains available separately. A complete SQL-only response includes a signed `review_token` for the explicit `/estimates/plan` form submission. Editing preview fields cannot change that signed snapshot.
