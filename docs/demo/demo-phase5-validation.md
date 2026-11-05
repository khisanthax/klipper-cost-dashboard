# Phase 5 validation record

Status: deterministic Basic Demo content gate passed for Phase 6. This is not a public-deployment approval.

The tested code commit is `f8ff34d`. No binary seed database is committed; every test generates temporary seeds and workspaces.

## Content proof

- Two independent `basic-v1` builds produced identical SQLite bytes, manifests, and logical digests.
- The manifest and live database agree on all entity counts.
- SQLite integrity and foreign-key checks pass.
- All current migrations are recorded with deterministic timestamps.
- All three printers have null Moonraker URLs and external client IDs.
- Persisted settings contain no URL, Moonraker, API-key, or SSH fields.
- The seed is below every Phase 4 entity and workspace-size quota.
- Dashboard, reports, projects, and profile pages render from an isolated clone.
- Editing and resetting a workspace restores all 18 history records and original project content without changing the seed or session identity.
- Failed builds remove temporary content and never publish a partial seed.
- Existing destinations, unsafe seed names, relative/missing roots, and symlinked roots are rejected.

## Platform validation

- Windows 11, Python 3.13.3: `206 run, 203 passed, 0 failed, 3 skipped`. The skips are symlink operations unavailable under the Windows test privileges.
- Ubuntu 24.04.2 LTS under WSL2, kernel `6.18.33.2-microsoft-standard-WSL2`, Python 3.12.3, ext4-backed `/tmp`: `206 passed, 0 failed, 0 skipped`.
- Focused Linux Basic Demo seed suite: `9 passed`.
- Focused Linux Phase 4 security suite: `18 passed`.
- Focused Linux workspace concurrency suite: `11 passed`.
- SQL-only validation observed zero legacy runtime filesystem accesses on both platforms.

Commands:

```text
python -m unittest discover -s tests
python -m compileall -q app.py core installer kcd tools
python tools/validate_sql_only.py
git diff --check

python3 -m unittest discover -s tests
python3 -m compileall -q app.py core installer kcd tools
python3 tools/validate_sql_only.py
git diff --check
python3 -m unittest discover -s tests -p 'test_demo_basic_seed.py' -v
python3 -m unittest discover -s tests -p 'test_demo_security.py'
python3 -m unittest discover -s tests -p 'test_demo_workspace_concurrency.py'
```
