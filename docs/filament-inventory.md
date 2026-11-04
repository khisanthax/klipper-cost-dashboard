# Filament inventory

Inventory records physical spools independently of filament pricing profiles and print history. It is available in SQL-only mode.

1. Open **Inventory**, add a spool, and enter the net weight and its measured opening balance in grams. Purchase date, purchase price, and verified grams per meter are optional. A low-stock threshold shows a badge when the balance reaches it.
2. Assign a spool to a registered printer if useful. This assignment identifies where the spool is kept. A job may use several physical spools.
3. For a completed, failed, or canceled history job, open **Record verified job use** on the relevant spool. Select or enter the job UID, enter the measured grams used from that spool, and confirm the measurement. The list suggests the 100 most recent terminal jobs. Older jobs can be entered by UID.
4. Use **Adjust stock** for a measured correction. Enter a signed quantity and the reason.

Balances and changes are stored to the nearest supported 0.001 g. A transaction updates the spool and its stock ledger together. Repeating the same measured deduction for a spool and job is a no-op. A different quantity for the same spool and job requires an explicit adjustment. Adjustments and spool creation also protect against replay of the same submitted form.

The history job and its costs are unaffected by inventory changes. Job deletion preserves previously recorded stock events and their captured job UID and filename. Estimates, unknown usage and ordinary print events do not deduct stock. The stock history shows the last 100 events, including opening balance, adjustments and verified job deductions. Each spool retains its own purchase record.

SQLite backups contain both spool balances and stock events. Migration `0010_filament_inventory` creates the new tables without modifying existing history. Use the standard backup and isolated restore procedure before upgrading.
