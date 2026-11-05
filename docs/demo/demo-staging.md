# Stage the interactive demo

**Owner decision, November 5, 2026: keep the demo isolated for now.** The current
delivery includes code, isolated browser/container acceptance and release
packaging only. Do not publish a demo endpoint or activate demo mode on the
production Master. Public hosting is not a pending completion blocker for this
program; it requires a separate future request and target-specific security
approval. The public staging checklist below is retained for that future task.

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

New workspace creation is limited to five requests per ten minutes per client IP by default. Reset uses a separate per-workspace budget with the same configured limit. A rejected request returns 429 and `Retry-After`; it does not allocate a workspace. Configure the trusted proxy correctly so visitors use their actual source addresses, and account for shared NAT traffic when tuning the limits for an approved target.

Run `python tools/browser_demo_regression.py` after installing the Playwright requirements, Chromium and OpenSSL. The test generates a short-lived local certificate in its private temporary directory and starts a loopback-only TLS proxy and backend. It exercises actual HTTPS desktop and mobile sessions, confirms Secure/HttpOnly/SameSite cookies, HSTS, host enforcement, stripped spoofed proxy headers, project isolation and reset, and checks that printer, installer and export endpoints are denied. It stores screenshots and a trace in `browser-artifacts/demo/`. The browser trusts this test certificate only inside its disposable contexts; this is not a public certificate or deployment.

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
- Enable TLS and set `KCD_DEMO_EXPECT_HTTPS=1` and `KCD_DEMO_COOKIE_SECURE=1`. Set `KCD_DEMO_ALLOWED_HOSTS` to the exact approved public hostname. Configure the proxy to accept that host and overwrite forwarded headers. Enable `KCD_DEMO_TRUST_PROXY=1` only behind that proxy and set `KCD_DEMO_TRUSTED_PROXY_ADDRESSES` to its exact socket-peer IPs. The application ignores forwarded headers from other peers. Keep direct app access blocked.
- Bound proxy request body, concurrency and timeouts. Use disk and memory limits and monitor capacity, error rate and expired cleanup. Active requests retain cross-process leases; interrupted creating/resetting/deleting/error rows and their recognized staging directories are reclaimed after their absolute expiry.
- Rerun the browser and isolation suites, including concurrent users, HTTPS session headers, expiry, reset and blocked real-printer/administration routes. A public hostname and hosting target require a separate decision.

This recipe prepares local staging. Public TLS, network isolation and host capacity still require validation on the selected target.

New application surfaces remain denied until separately reviewed for demo use. Integrated templates should hide unavailable controls with `not kcd_demo_mode or demo_endpoint_available(endpoint, method, action)`. The helper uses the same default-deny policy as request handling; it does not grant access or replace the server boundary. Existing sample Projects, profiles and pricing retain their permitted actions.
