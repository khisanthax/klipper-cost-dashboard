# Demo application limits

All settings are validated as positive integers or explicit booleans at demo startup. They are ignored in normal mode.

| Setting | Default | Enforcement |
| --- | ---: | --- |
| `KCD_DEMO_MAX_REQUEST_BYTES` | 1 MiB | Flask and request guard; HTTP 413 |
| `KCD_DEMO_MAX_TEXT_LENGTH` | 256 characters | Form/JSON scalar validation; HTTP 422 |
| `KCD_DEMO_MAX_NOTES_LENGTH` | 4,096 characters | Notes/description validation; HTTP 422 |
| `KCD_DEMO_MAX_LIST_LENGTH` | 1,000 values | Form/JSON collection validation; HTTP 422 |
| `KCD_DEMO_RATE_GENERAL_LIMIT` / `WINDOW_SECONDS` | 300 / 300 s | Per workspace; HTTP 429 + `Retry-After` |
| `KCD_DEMO_RATE_MUTATION_LIMIT` / `WINDOW_SECONDS` | 60 / 300 s | Per workspace mutation |
| `KCD_DEMO_RATE_LIFECYCLE_LIMIT` / `WINDOW_SECONDS` | 5 / 600 s | Per workspace reset/lifecycle request |
| `KCD_DEMO_RATE_INVALID_SESSION_LIMIT` / `WINDOW_SECONDS` | 60 / 300 s | Direct source address unless proxy trust is explicit |
| `KCD_DEMO_MAX_PRINTERS` | 10 | No approved create-printer action yet; existing local records remain bounded by seed/reset |
| `KCD_DEMO_MAX_FILAMENT_PROFILES` | 50 | Transactional creation reservation |
| `KCD_DEMO_MAX_HOURLY_PROFILES` | 25 | Transactional creation reservation |
| `KCD_DEMO_MAX_PROJECTS` | 100 | Transactional creation reservation |
| `KCD_DEMO_MAX_HISTORY_RECORDS` | 1,000 | Printer-client/import creation is blocked; deletion/recalculation remain allowed |
| `KCD_DEMO_MAX_WORKSPACE_BYTES` | 25 MiB | Seed validation, contained pre-mutation measurement, and SQLite `max_page_count` |
| `KCD_DEMO_MAX_EXPORT_BYTES` | 10 MiB | Reserved; export remains blocked |
| Uploaded files | 0 | Multipart requests blocked before route execution |

Malformed requests use 400; prohibited network/upload/action inputs use 403; count quotas use 409; validation failures use 422; rate exhaustion uses 429. Limits never delete existing records. Reset and cleanup bypass entity creation reservations and remain available when a quota is reached.
