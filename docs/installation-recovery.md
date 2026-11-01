# Install, repair, relocate and recover clients

Upgrade the Master before using the registration API. The installer is Python
3.11+ with standard-library client dependencies; a source Master also installs
the pinned requirements. Docker supports amd64 and Raspberry Pi architectures.
Keep one Master process per data directory. The host installer recognizes a
running Docker Master by its `/app/data` bind mount; an explicit conflicting
storage mode fails. `KCD_DATA_DIR` selects a custom persistent directory.

## Discover and preview

```bash
python3 install.py discover
python3 install.py discover --config-dir /custom/printer/config
python3 install.py macro-plan --config-dir /home/pi/printer_data/config
```

Discovery marks process-backed installations active and other candidates
unverified. Old copies/backups are never automatically selected. Port discovery
uses actual Moonraker configuration, not the assumption that every instance is
on 7125. Aliases deduplicate only when host and instance identity are proven.
Missing instance metadata leaves endpoints separate for manual selection.

Repair is a dry run unless `--apply` is supplied. It verifies authentication,
Moonraker's config root and the Master network path. The Master initializes SQL
pricing, identity and operational metadata together. Repeating the operation
preserves identity, prices and jobs. Client files have private backups.

```bash
python3 install.py repair-client \
  --config-dir /home/pi/printer_data/config \
  --printer Ender5P --moonraker http://printer-lan-address:7125 \
  --master http://master-lan-address:6060
# Review output, then repeat with --apply.
```

The credential prompt is hidden. Alternatively use `KCD_API_KEY` or a private
`--key-file` JSON file containing `api_key`. Do not put credentials in shell
arguments, screenshots, logs or Git. Client scripts are owner-only. Their fixed
`--kcd-self-check` diagnostic verifies authentication without emitting job events.

Use `--relocate` only for the SAME historical printer changing host/IP/port;
use a new `--printer` name for cloned hardware. `--reactivate` explicitly restores
a retired identity. Rename/merge remain separate deliberate dashboard actions;
the installer never merges jobs. Do not use IP addresses as identities.

The interactive installer offers local selection and remote SSH repair using
the same workflow. SSH repair sends credentials over stdin, stages only source
code privately and removes staging afterward. It does not require Flask on a
printer host. If files fail after registration, SQL registration remains and the
same repair can be retried; no history is moved. If SQL registration fails, no
client files are changed. A missing old `install_state.json` does not erase SQL
identities. Clients without location metadata are shown as recovered/unknown;
select their active config to finish operational recovery.

## Macro integration

```bash
python3 install.py macro-plan --config-dir /home/pi/printer_data/config \
  --file print_start_manual.cfg --macro PRINT_START --event start
# Review the diff, then repeat with --apply.
```

Events: start, end, cancel, pause, resume. Select existing active user macros.
Inactive alternatives, missing includes, cycles and duplicate macro definitions
block patching. Existing hooks are retained. Motion and thermal commands are
preserved. Reload Klipper configuration only while idle if a config changed.
Updating shell scripts alone needs no Klipper restart. Older custom
`print_cost.cfg` files without a baked `_KCD_VARS` identity require explicit
migration; the repair command refuses to replace them wholesale.

## SQL startup, upgrade and rollback

`python -m kcd.server` initializes pause policy only for a brand-new SQL database.
Existing databases retain strict readiness checks. Use `python -m kcd db
readiness` before and after installation. Back up before migrating CSV/dual:
run the existing explicit `python -m kcd db import`, verify parity, resolve
readiness, then set `KCD_STORAGE_BACKEND=sql`. Do not reimport stale JSON into a
running SQL-only Master. SQL printer cards require SQL pricing; registration
initializes it before reporting success.

Before deployment, create `python tools/kcd_backup.py`, retain the previous
image ID and back up the private Compose configuration. Test a restored copy in
an isolated data directory and port. Recreate only the KCD service. On failure,
recreate with the previous image and current data (this release adds no schema
migration); restore the consistent backup only if data verification requires it.
Do not overwrite unrelated services or discard new jobs during rollback.

## Credential rotation

Run on the Docker Master host from the new release checkout, while all configured
printers are idle and reachable:

```bash
python3 tools/rotate_api_key.py \
  --data-dir /home/khisanthax/klipper-cost-dashboard/data \
  --master http://192.168.2.55:6060 --container print-cost-dashboard
```

The tool inventories SQL clients, verifies loaded identities, backs up scripts
privately, stages a new key with a 24-hour old-key overlap and restarts only KCD.
It propagates updated scripts using the trusted Moonraker file API, verifies
their bytes and runs only the fixed diagnostic through the existing shell
command. It revokes the old key only after every active client verifies. A
partial failure retains overlap and reports recovery instructions; rerun with
`--resume`. Never rotate again over an unfinished rotation. Environment-managed
keys must be rotated at their deployment authority instead.

Moonraker's config/file API is a trusted administrative boundary: protect it
with network/access controls. File permissions do not prevent the owning
Moonraker service from reading client scripts. Archived old keys must remain
private even after revocation. Retired/offline clients need explicit repair with
the current key before reactivation.
