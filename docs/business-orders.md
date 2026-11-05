# Project-backed business orders

The additive 0009 migration is committed together with its migration-ledger row.
Concurrent startup rechecks the version under the SQLite writer lock; a failed
migration rolls back and can be retried. Apply pending migrations outside any
caller-owned transaction. If older interrupted tooling left an unrecorded Orders
table, stop and inspect a consistent backup instead of deleting data or manually
stamping the migration as successful.

Orders use the same stable project identity as Projects, including legacy project identifiers. If an explicit project identity shadows an older numeric identifier, the older order remains listed with an identifier-conflict notice rather than linking to the other project. Resolve that identity conflict through a separately reviewed recovery operation; KCD does not reassign its history or prices automatically.

On a SQL-only installation, select **Business** in **Settings → Other → Application Mode**. Open **Orders** from the main navigation or **Business orders** from Projects. Choose an existing project and save an order with a Draft, Active or Complete status.
Customer name, description, notes, due date and priority are optional business
context. Completing an order records its completion time. Reopening it clears
that time so a later completion can be recorded.
Saving an already Complete order retains its original completion time. Status
changes can reopen completed work through Active or Draft.

Assign tracked jobs, record manual work and manage plans in the linked project.
Each project has at most one order. Removing a draft order preserves its project
and jobs. Project deletion is refused while an order exists.

Enter USD amounts to the nearest cent. Blank amounts are unknown. Enter zero
when revenue, expenses or another input is confirmed to be zero.

- Estimated total cost is the explicit comparison baseline. Active plan costs
  remain separately visible as a projection.
- Quote and selling price describe commercial terms. Recorded revenue is entered
  separately.
- Actual production cost combines saved tracked prices or explicit project-only
  snapshots with the project's manual work. Saved job prices are preserved.
- Additional expenses are entered as a single actual total. Actual total cost
  adds those expenses to actual production cost.
- Profit is recorded revenue minus actual total cost. Margin divides profit by
  positive recorded revenue. Both use known inputs, and incomplete inputs stay
  unknown.

The display reconciles time, material and signed adjustment to the saved total
at cent precision. Projected plans are excluded from actual costs. Simultaneous
edits require reloading the order after another save.

Migration `0009_business_orders` creates the project-linked order table. Back up
the existing database and archive objects before deploying and verify restoration
using the prior image or an isolated copy.

Advanced and Personal hide the Orders links and block order edits. Switching
back to Business restores the saved orders. Application mode is a presentation
preference, and access to the KCD service should be configured separately.
