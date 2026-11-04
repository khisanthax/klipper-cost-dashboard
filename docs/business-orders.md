# Project-backed business orders

On a SQL-only installation, select **Business** in **Settings → Other → Application Mode**. Open **Orders** from the main navigation or **Business orders** from Projects. Choose an existing project and save an order with a Draft, Active or Complete status.
Customer name, description, notes, due date and priority are optional business
context. Completing an order records its completion time. Reopening it clears
that time so a later completion can be recorded.

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
