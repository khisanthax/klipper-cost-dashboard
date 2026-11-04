# Pre-print estimates

Open **Projects → Calculate a pre-print estimate**. Upload an OrcaSlicer-compatible `.gcode` file or enter duration and weight manually. The request limit is 16 MiB. Only comment metadata from bounded head and tail samples is parsed. Uploaded commands are never sent to a printer.

Enter a machine rate per hour and choose a saved material profile or enter an explicit price per kilogram. The calculator uses the planned-item one-hour minimum. Enter weight in grams if the slicer does not supply it. A slicer length in millimeters is converted only with verified grams per meter from the selected profile or the explicit conversion field. Missing weight leaves material and total cost unknown.

Try different input rates to compare scenarios. An estimate does not create actual History or change saved prices. To turn G-code into a projection use the existing planned-item upload in the target project. That workflow uses the project's own pricing settings.

For JSON clients, send `POST /api/estimates` with these explicit units.

```json
{
  "duration_seconds": 1800,
  "hourly_rate": 2,
  "filament_grams": 25,
  "filament_price_per_kg": 20
}
```

Send `filament_profile_id` instead of a manual material price to use a profile. A multipart request can also include a `gcode` upload and optional `grams_per_meter`. The response identifies itself as an estimate and includes the time, material and total breakdown. Unknown material or totals are JSON null. The display values sum the rounded components.
