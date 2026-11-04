# Browser installer administration

The browser installer runs as a separate, opt-in service on the operator's host. It uses the same preview, registration and file repair workflow as `install.py repair-client`. Start with a working KCD Master and a Klipper/Moonraker installation. Run the administrator service on a host with the required local file access or a preconfigured, key-authenticated SSH account.

Create a private JSON file listing the exact targets you intend to manage. For each Moonraker instance use its own URL, absolute configuration path and stable KCD identity.

```json
{
  "master": "http://192.168.2.10:6060",
  "targets": {
    "local": {
      "name": "Workshop",
      "config_dir": "/home/operator/printer_data/config",
      "moonraker": "http://192.168.2.10:7125"
    },
    "remote": {
      "name": "Studio",
      "ssh_alias": "studio-printer",
      "config_dir": "/home/operator/studio_data/config",
      "moonraker": "http://192.168.2.20:7126"
    }
  }
}
```

Verify the SSH host key and test the alias with BatchMode before using the browser. Use a dedicated account with access to the printer configuration and a Python 3.10 or newer interpreter on the SSH target. The service passes the Master key to the existing SSH workflow on standard input. The browser sees only configured target names and paths and the names of files that would change.

Supply the existing Master key as `KCD_API_KEY` and a separate strong password of at least 24 characters as `KCD_WEB_ADMIN_PASSWORD` through the process environment. Start the service from the checked-out KCD source with its Python dependencies installed.

```sh
python -m installer.web_admin --targets /path/to/private-targets.json
```

Open `http://127.0.0.1:6061/` with username `admin`. Use SSH local port forwarding for a remote operator session. Keep the loopback listener private. Set `--port` if the local port is occupied. Run a single service process under the account that owns the configuration.

1. Select **Verify Master and registered clients** to check the Master connection, registered identities and SQL readiness.
2. Select a configured printer and preview. A preview verifies the selected Moonraker instance and configuration root. Review the file list and the exact identity.
3. Apply while the printer is inactive. The review expires after five minutes and can be used once. A changed file, printer endpoint or registration requires a fresh preview.
4. Verify the Master registration. Use `install.py macro-plan` to review the active event hooks and follow the existing idle-window procedure if Klipper needs a configuration reload.

To move an existing identity, configure the new host and endpoint using the existing KCD name and set `"relocate": true` on that target. Set `"reactivate": true` only for an intentional restoration of a retired identity. The operator controls these options in the private allowlist.

The shared engine checks for an inactive, reachable printer before registration and again before writing. If registration succeeds and a later step fails, retain that identity and review a new preview. The existing writer keeps private per-file backups. Follow [installer recovery](installation-recovery.md) when a client is partly configured. A configuration include that resolves outside the allowed root requires the CLI workflow.

Initial Master bootstrap, unrestricted discovery, macro editing, retirement/uninstall, backups and service management still use the existing CLI or dashboard workflow. The browser service currently manages explicitly configured targets and installs or repairs their client files and registration. It checks inactivity at application time and does not issue firmware commands.
