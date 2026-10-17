# Team Performance Analytics

## What is implemented

The authenticated admin control centre at `/control/` includes a read-only Team Performance Analytics section backed by `GET /api/v1/admin/team-performance`. It reads four operational Google Sheets worksheets on demand:

- `People Directory`
- `Master Task Tracker`
- `KPI Register`
- `Weekly KPI Log`

The API is protected by the same Google OIDC admin session and allowlist as the other admin endpoints. It returns aggregate metrics and admin-visible task/KPI details, with `Cache-Control: no-store`. It does not mutate the workbook, send messages, or enable scheduled workflows.

## Dashboard areas

- Summary metrics: active members, tasks, completion rate, overdue, in-progress, blocked, stale updates, KPI observations, below-target observations, and due-date coverage.
- Team scorecard and completion-rate comparison.
- Individual workload and accountability signals.
- Follow-up queue for overdue, blocked, and stale-update tasks.
- KPI target-versus-actual observations.
- Six-week task activity visualization.
- Data-quality counters and interpretation notes.
- Filters for date range, team, member, and task status.

## Header mapping

The service matches recognized headers case-insensitively while ignoring spaces and punctuation. Core mappings are:

- People: `Person ID`, `Full Name`, `Team`, `Status`.
- Tasks: `Task ID`, `Task Name`, `Assigned To ID`, `Status`; optional due-date aliases include `Due Date`, `Deadline`, `Target Date`; optional update aliases include `Last Updated`, `Updated At`, `Last Check-in`.
- KPI register: `KPI ID`, `KPI`, `Owner Person ID`, `Target`, with optional direction aliases.
- Weekly KPI log: `Week Start`, `KPI ID`, `Owner Person ID`, `Target`, `Actual`.

Missing or non-unique IDs and unmatched KPI records are counted in data quality rather than silently repaired. The API reads rows as supplied by the Google Sheets client; it never creates or writes headers.

## Calculation semantics and limitations

- Completed statuses include common values such as Completed, Done, Closed, Resolved, and Finished. In-progress, open, blocked, and cancelled states are normalized for reporting; original status text is retained in the follow-up queue.
- An overdue task has a recognized due date before the current UTC date and is not completed. Outstanding overdue tasks remain visible even when their due date predates the selected reporting window.
- Tasks without a due date remain in current workload/status totals but cannot be classified as overdue.
- A stale-update signal is shown only when a recognized last-update date exists and is more than seven days old. It is a prompt to check in, not a judgment about the employee.
- KPI attainment requires numeric target and actual values. If a recognized direction field explicitly indicates lower-is-better, the comparison is reversed; otherwise the default interpretation is higher-is-better. Confirm this assumption for every KPI before using the dashboard for formal performance decisions.
- The six-week trend groups current task records by due date, or update date when no due date exists. It is **not** a historical snapshot of task state at each past date.
- Date filters use task due date, then update date. Undated tasks remain in current status totals. Outstanding overdue tasks remain visible regardless of their due date.
- Member/task results depend on valid owner IDs that match the People Directory. Unmatched owners are surfaced as data-quality findings, not guessed from names.
- KPI summaries reflect observations in the selected period, not necessarily the latest observation per KPI. Review cadence and duplicate observation policies before making formal scorecards.

## Security and operational posture

- Authentication and authorization remain server-side.
- The browser does not call Google APIs directly and does not receive OAuth credentials.
- This is read-only; no task/KPI edit actions or reminder sends are exposed.
- Existing safety flags remain unchanged: `DRY_RUN=true`, team writes, scheduled workflows, analytics, webhooks, safe Telegram replies, dry-run team replies, and team workflows remain disabled unless separately approved.
- The dashboard is only proven to show live data after the deployed API can authenticate to Google Sheets and the four expected worksheets are readable. A successful CI run alone does not prove Render runtime connectivity.
- Before using this dashboard for performance decisions, compare counts and several representative records with the workbook and confirm status values, date formats, KPI direction semantics, and active/inactive person statuses with the Operations Manager.
