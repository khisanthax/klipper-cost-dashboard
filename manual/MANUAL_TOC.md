# Klipper Cost Dashboard Manual - Table of Contents

This document lives in the `manual/` directory for easy discovery when browsing the project documentation.

1. **Introduction**
   1. What is Klipper Cost Dashboard (KCD)?
   2. Core goals and use cases
      - Tracking real cost of prints
      - Managing a multi-printer farm
      - Estimating and comparing projects/orders
   3. Key concepts and terminology
      - Master vs Client
      - Printer vs Client Install
      - Job / Print / Project / Batch
      - Filament profile & Hourly rate profile
      - Time cost vs Material cost vs Total cost
   4. High-level feature overview
      - Live dashboard
      - Print history
      - Reports & summaries
      - Pricing and profile system
      - Multi-printer support

2. **System Architecture & Data Flow**
   1. High-level architecture
      - Web UI (dashboard)
      - Backend & storage (CSV / DB / settings files)
      - Master container / process
      - Client scripts and Klipper integration
   2. How KCD talks to Klipper
      - `gcode_shell_command` and shell scripts
      - `KCD_JOB_START` macro
      - Cost payload format (printer name, filename, estimated duration, filament, etc.)
      - Live cost updates vs final "job completed" records
   3. Cost calculation model
      - Hourly time cost (basic and rate profile–based)
      - Filament cost modes: per meter / per gram / per kg
      - Grams per meter and material density assumptions
      - How estimated vs actual costs are calculated
   4. Time & timezone handling
      - Where timestamps come from
      - Server time vs browser time
      - Fixing "history is 5 hours ahead" and other common offset issues

3. **Installation & Setup Overview**
   1. Supported platforms and prerequisites
      - Host OS, Docker/Python requirements
      - Network requirements & ports
      - Klipper prerequisites on each printer
   2. Common deployment scenarios
      - Single printer, same machine as Klipper
      - Multi-printer farm, one master, multiple clients
      - Remote Klipper hosts (e.g., different Pi's, Proxmox VMs)
   3. Installation flow at a glance
      - Install master
      - Install first client
      - Add additional local/remote clients
      - Configure pricing & profiles
      - Verify data is flowing

4. **Command-Line Installer**
   1. Starting the installer (CLI entry point)
      - Command syntax
      - Running as root vs non-root
   2. Master installation (server side)
      - Creating data directories
      - Docker / service setup (if applicable)
      - Generating secrets and initial config
      - Setting default currency & timezone
   3. Local client installation (same box as Klipper)
      - Selecting printer name
      - Printer data directory path
      - Generating `send_print_cost.sh` and `kcd_job_start.sh`
      - Creating/including `print_cost.cfg` in `printer.cfg`
   4. Remote client installation (SSH-based)
      - Requirements (SSH, paths, permissions)
      - Prompt flow: host, user, printer_data path, printer name
      - Copying scripts and config to the remote host
      - Ensuring `[include print_cost.cfg]` is added
      - How remote clients are tracked/updated (client registry)
   5. Re-running the installer wizard
      - When and why to re-run
      - What gets overwritten vs preserved
      - Planned "re-run wizard" button in the web UI
   6. Updating or uninstalling KCD
      - Update options (re-run installer vs pull new images)
      - Uninstall paths (master vs clients)
      - Cleaning up Docker containers / services / config

5. **Web-Based Installer (Planned Feature)**
   1. Goals for the web installer
      - Guided setup for non-technical users
      - Visualization of master + first client setup
   2. Installer UI flow
      - Step 1: Check environment & prerequisites
      - Step 2: Configure master (storage, timezone, currency)
      - Step 3: Configure first client/printer
      - Step 4: Confirm Klipper integration & test
   3. Visual progress indicator
      - Progress diagram showing: Master → First Client → Additional Clients
      - How progress is updated during install
   4. Re-running the web installer / wizard
      - Admin/Settings option to relaunch from scratch
      - "Add/update clients only" mode vs full re-install

6. **Configuring Klipper for KCD**
   1. The auto-generated `print_cost.cfg`
      - `gcode_shell_command` definitions (`send_print_cost`, `kcd_job_start`)
      - Shared macros created by the installer
   2. `KCD_JOB_START` macro and usage
      - Parameters passed from your `START_PRINT` macro
      - What data it sends to KCD (estimates & IDs)
      - How it's called from your existing start macro
   3. `PRINT_COST_TEST` macro
      - Purpose (sanity check)
      - Reading its output in the Klipper console
      - Verifying data reaches KCD
   4. Integrating with your START/END macros
      - Example: injecting `KCD_JOB_START` into `START_PRINT`
      - Handling job completion / failure / cancellation
      - Avoiding conflicts with other macros (e.g., EDDY-NG, brush, etc.)
   5. Troubleshooting Klipper integration
      - Template syntax errors (expected token ':')
      - `RUN_SHELL_COMMAND` failures
      - Shell script permissions and paths

7. **Using the Dashboard**
   1. Overview of the Web UI
      - Logging in / accessing the dashboard
      - Layout and navigation (menu, pages, icons)
      - Dark/light mode (if applicable)
   2. Live Dashboard (Printer Cards)
      - Printer card layout
        - Printer name and status
        - Current file, estimated duration, elapsed time
        - Estimated vs current cost
        - Active filament profile & active hourly rate profile display
      - Live cost tracking
        - How frequently in-progress jobs update
        - Behavior under 60 minutes vs longer prints
        - What happens if KCD restarts mid-print
      - Multi-printer behavior
        - Sorting and grouping printers
        - Offline/idle vs currently printing vs error states
      - Planned enhancements
        - Quick actions from cards (filter, jump to history, etc.)
        - Adding project/batch assignment on the card
   3. Print History Page
      - Columns and data fields
        - Printer, filename, project, start time, end time
        - Duration, filament (mm/grams), material cost, time cost, total
        - Notes / tags (if present)
      - Filtering and sorting
        - By printer
        - By date range
        - By project/batch (future)
      - Editing and correcting records
        - Manual overrides (filament used, price corrections)
        - Marking failed/canceled prints
        - Merging or splitting entries if needed
      - Exporting and backups
        - CSV export
        - Use cases for accounting / invoicing
   4. Reports Page
      - Summary views
        - Cost per day/week/month
        - Cost per printer
        - Cost per filament type / profile
      - Visualizations (if/when added)
        - Bar charts for cost over time
        - Printer utilization views
      - Use cases
        - Finding expensive printers or materials
        - Tracking ROI on a new machine
        - Understanding seasonal demand
   5. Settings Page
      - Global defaults
        - Default hourly rate
        - Default filament cost mode and rates
        - Default grams-per-meter
      - Printer defaults
        - Default profiles per printer
        - Printer naming and display options
      - Filament profiles
        - Fields: name, material, color, rate, grams-per-meter, notes
        - Adding new profiles
        - Editing and deleting profiles
        - When to use per-meter vs per-gram vs per-kg
      - Hourly rate profiles
        - Fields: name, hourly rate, notes (e.g., "overnight", "rush jobs")
        - Assigning rate profiles per printer
        - Fallback behavior when no profile is active
      - Saving and applying settings
        - "Save to defaults" behavior
        - How changes affect in-progress vs future jobs
   6. Projects / Batches Page (Planned Feature)
      - Concept: grouping prints into projects/batches
        - Single object printed in multiple pieces
        - Orders with repeated items (e.g., 10x same flexi)
      - Creating a project
        - Name, description, client/order reference
        - Optional due date / priority
      - Assigning jobs to a project
        - From live cards
        - From history list
        - Bulk assign/edit
      - Project-level views
        - Total cost per project
        - Cost per printer within a project
        - Status: in progress / completed
      - Use cases
        - Quoting clients
        - Summarizing large cosplay builds
        - Tracking multiple copies of the same product
   7. Admin / Maintenance Page (Planned)
      - Re-running the setup wizard
      - Viewing registered clients (local and remote)
      - Connection health and last-seen status
      - Logs and diagnostic info

8. **Advanced Usage**
   1. Using KCD with multiple masters (advanced farms)
   2. Integrating with slicers / external tools
      - Using exposed HTTP/API endpoints (if/when formalized)
      - Ideas for plugin integrations
   3. Custom pricing rules
      - Using different profiles for different times of day
      - Handling "friends & family" discounts or special cases
   4. Handling failed/canceled prints
      - Best practices (marking failures, partial costs)
      - Strategies for tracking wasted filament separately
   5. Scaling up
      - Performance considerations as history grows
      - Archiving old jobs

9. **Troubleshooting**
   1. Nothing appears in the dashboard
      - Checklist: scripts, macros, includes, network, logs
   2. Jobs appear but costs are wrong
      - Checking profiles
      - Verifying grams-per-meter and filament rates
      - Time vs material cost sanity checks
   3. Timestamp and timezone issues
      - Server timezone
      - Browser timezone
      - Adjusting via settings/OS
   4. Installer issues
      - SSH failures
      - Permission problems on remote machines
      - Docker/startup failures
   5. Klipper and template issues
      - Common error messages and fixes
      - How to safely edit macros and revert

10. **Security, Backups, and Maintenance**
    1. API keys / secret file
    2. Network exposure and reverse proxies
    3. Backup strategies
       - Data directories
       - Config / settings files
    4. Upgrading safely
       - Backing up before upgrades
       - Rolling back to previous versions

11. **Roadmap & Future Enhancements**
    1. Web-based installer progress
    2. Project/batch system
    3. Notifications & integrations
       - Discord / webhooks / email
    4. Enhanced reports and graphs
    5. Multi-user / permissions model

12. **Appendices**
    1. Glossary of terms
    2. Example Klipper configurations
       - Example: SV08
       - Example: Mercury One (Ender 5 Plus)
       - Example: Ender 3 variants
    3. Data format & API reference
       - Cost payloads from clients
       - Internal CSV field reference
    4. Changelog and version history

---

## Chapter Title Quick Reference
- What KCD Is and What It Does
- How KCD Calculates Cost (Time + Material)
- System Architecture and Data Flow (Master, Clients, Klipper, Dashboard)
- Installing the Master (Server Side)
- Installing a Local Client on a Klipper Host
- Installing a Remote Client over SSH
- Configuring Klipper for KCD (Macros, `print_cost.cfg`, Shell Commands)
- Using the Live Dashboard (Printer Cards & Active Jobs)
- Using the Print History Page
- Using the Reports Page
- Using the Settings Page (Filament Profiles & Hourly Rate Profiles)
- Projects/Batches: Grouping Prints into Orders/Builds
- Timezones, Timestamps, and Fixing "History Is 5 Hours Ahead"
- Troubleshooting Common Issues (Installer, Klipper, Data Not Showing)
- Backups, Updates, and Maintenance
- Advanced / Power User Setups (Multiple Printers, Proxmox, Homelab)
- Roadmap & Future Features (Web Installer, Projects Page, Admin Tools)
- Appendices (Example Configs, Glossary, Data Format/API Notes)
