# Stage the interactive demo

The demo starts as a separate KCD process with a dedicated data root and synthetic seed. Normal KCD keeps its existing runtime and SQL data path. Use a staging host or a disposable container with no production data, configuration, credentials, SSH agent, Docker socket, or printer network access.

## Local acceptance

In a disposable shell, create an absolute staging directory and run the following commands. Replace the paths with directories dedicated to the demo.

```bash
mkdir -p /absolute/staging/demo-data
python tools/create_demo_seed.py --data-root /absolute/staging/demo-data
export KCD_RUNTIME_MODE=demo KCD_STORAGE_BACKEND=sql KCD_REPORTS_BACKEND=sql
export KCD_DEMO_DATA_ROOT=/absolute/staging/demo-data
export KCD_DATA_DIR=/absolute/staging/normal-data-sentinel
export KCD_DEMO_COOKIE_SECURE=0
python -m kcd.server --host 127.0.0.1 --port 6061
```

Open `http://127.0.0.1:6061/` and start the demo. Use a private browser session to see a second independent workspace. Changes to prices or projects are local to a workspace. Reset restores the synthetic sample data for the selected session. The cards show time-based simulated progress and current-price estimates from workspace profiles. Simulated observations never insert job history.

The server sweeps expired workspaces every minute, with registry leases protecting active requests. Workspace creation has a transactionally enforced limit of 64 registry records by default. Individual workspace storage is capped at 25 MiB by default. Lower these settings to match available capacity. When full, the service returns a retryable 503 before cloning the seed. Use `python -m kcd demo cleanup --dry-run` to inspect due workspaces and `python -m kcd demo validate` to check the registry and current seed.

Run `python tools/browser_demo_regression.py` after installing the Playwright requirements and Chromium. The test starts a loopback server, exercises desktop and mobile sessions, confirms project isolation and reset, and checks that printer, installer and export endpoints are denied. It stores screenshots and a trace in `browser-artifacts/demo/`.

## Isolated image acceptance

Use the same sandbox as Release Validation on Linux from a non-root operator account with Docker access.

```bash
docker build --tag kcd-demo-staging .
python tools/demo_container_smoke.py kcd-demo-staging
```

The test creates a private temporary volume and generates a synthetic seed from the image. It launches as the operator UID with a read-only image, dropped capabilities, bounded temporary storage and Docker's `none` network. It mounts only the temporary demo directory and does not publish a port. The HTTP probe runs on loopback inside the container and verifies a fresh workspace, simulated telemetry, blocked installer registration, absent normal data and denied outbound connectivity. The test removes its container and temporary directory afterward.

## Public staging gate

- Build the exact reviewed commit and pin its image digest. Use an isolated workload with read-only application files and a dedicated writable demo volume. Generate the seed from that image and use a new seed name when changing its content.
- Block demo egress to printers, production systems and cloud metadata at the network layer. Expose only the public HTTP reverse proxy. Run a negative connection probe from the actual workload before exposing it.
- Enable TLS and set `KCD_DEMO_EXPECT_HTTPS=1` and `KCD_DEMO_COOKIE_SECURE=1`. Configure the proxy to accept only the approved public host and overwrite forwarded headers. Enable `KCD_DEMO_TRUST_PROXY=1` only behind that proxy. Keep direct app access blocked.
- Bound proxy request body, concurrency and timeouts. Use disk and memory limits and monitor capacity, error rate and expired cleanup. Interrupted lifecycle operations may need operator investigation before reclaiming slots.
- Rerun the browser and isolation suites, including concurrent users, HTTPS session headers, expiry, reset and blocked real-printer/administration routes. A public hostname and hosting target require a separate decision.

This recipe prepares local staging. Public TLS, network isolation and host capacity still require validation on the selected target.
