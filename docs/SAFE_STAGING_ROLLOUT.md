# Polaris safe staging and rollout runbook

This runbook is the required acceptance sequence before enabling team or scheduled workflows.
Google Sheets remains the source of truth. The bot must fail closed if identity or KPI setup is
ambiguous. Do not put Telegram IDs, employee names, KPI values, or secrets in public issues,
chat, or logs.

## 0. Keep the production safety gate closed

Until every gate below is approved, keep these Render variables:

```dotenv
APP_ENV=production
DRY_RUN=true
ENABLE_SAFE_TELEGRAM_REPLIES=false
ENABLE_DRY_RUN_TEAM_REPLIES=false
ENABLE_TEAM_WORKFLOWS=false
ENABLE_TEAM_WRITES=false
ENABLE_SCHEDULED_WORKFLOWS=false
ENABLE_ANALYTICS=false
ENABLE_WEBHOOKS=false
```

Do not enable several integrations at once. A passing health endpoint is not proof that Telegram
routing, workbook formulas, or live writes are safe.

## 1. Verify personal Telegram identity mapping

Polaris team commands are supported only in a **private chat** with
`@AnalyticSagesPolarisBot`. In a group or supergroup, `/status`, `/goal`, `/blocker`, and
`/jd` are rejected before the workflow service is called. This prevents the shared group chat
ID from being mistaken for a person and prevents private status replies from being posted in a
group.

1. Each team member opens the bot in Telegram and sends `/start` privately.
2. The member sends `/whoami` privately. The bot returns that member's own Telegram user ID.
   The command is intentionally not allowed to reveal the ID in a group.
3. The member sends that ID to the workbook administrator through an approved private channel.
   Never post a list of IDs in a public group or repository issue.
4. In the People Directory, set **Telegram Chat ID** to that person's positive numeric private
   chat/user ID. Do not put the shared group ID (`-5356375243` or `-5097316051`) in a person row.
5. Check that every Active person has a nonblank, unique, positive numeric ID. Keep former staff
   rows for history but mark them inactive.
6. Run the protected read-only `/admin/preflight` endpoint. The identity check must pass.

The bot resolves a private team command using the sender's ID and requires it to equal the private
chat ID. A group command must never fall back to the group chat ID. If a member cannot start a
private chat with the bot, resolve that onboarding issue before enabling workflows.

## 2. Configure a small pilot set of real KPIs

Do not treat preallocated KPI IDs as configured KPIs. A row is considered configured only when
its human-entered **KPI** field is nonblank. Formula-derived cells on unused rows are expected to
remain blank.

In the KPI Register, agree on one to three pilot KPIs with the relevant team lead and owners. Fill
the human-entry fields using the workbook's existing dropdowns and validation:

- `Team`
- `Role`
- `KPI`
- `Definition / Measurement`
- `Direction`
- `Frequency`
- `Target`
- `Unit`
- `Owner`
- `Data Source`
- `Active?`

Use the existing dropdowns for `Owner`, `Direction`, `Frequency`, and `Active?` where
provided. Do not type over formula-driven columns such as `KPI ID`, `Owner Person ID`,
`Owner Name`, `Period Start`, `Check-in Due Date`, `Check-in Status`, or `KPI Label`.
After selecting an owner, confirm the formula-derived `Owner Person ID` matches the selected
person's Person ID. If the formula stays blank, check the Owner dropdown value and lookup formulas;
do not manually type a guessed ID into the formula column.

Use a measurable, auditable definition. For example, a KPI could measure the number of qualified
leads accepted by a CRM in a week, with a clear counting rule, owner, data source, target, unit,
direction, and frequency. This is an example only; the team lead must approve the actual KPI and
target.

Then verify:
- At least one configured KPI exists.
- Every configured KPI has all required setup fields populated.
- Each configured KPI's formula-derived Owner Person ID resolves to exactly one known Person ID.
- KPI IDs remain unique and stable.
- Weekly KPI Log entries (when added) reference configured KPI IDs and valid people.
- A deliberately blank owner on a configured KPI is reported as a blocker; blank template rows are
  ignored.
- Run preflight again. A zero-configured-KPI workbook is now intentionally blocked from staging.

## 3. Test commands without live team writes

### Automated tests

From the local repository, pull the merged code and install development dependencies if needed:

```bash
git switch main
git pull origin main
pip install -e '.[dev]'
ruff check .
pytest
```

The tests should verify that:
- a private `/status` command is resolved using the sender's personal ID;
- a team command in a group never invokes the workflow service;
- a mismatched sender/chat ID is rejected;
- `/whoami` only returns the user's ID in a private chat;
- `/whoami` does not disclose that ID in a group;
- a configured KPI with missing required setup or owner data blocks preflight;
- unused KPI template rows do not block preflight.

### Telegram smoke test

With `DRY_RUN=true` and `ENABLE_SAFE_TELEGRAM_REPLIES=false`, no Telegram reply is sent.
Automated tests cover command dispatch at this stage. To obtain a personal ID without disabling
dry-run, an administrator may temporarily set `ENABLE_SAFE_TELEGRAM_REPLIES=true` in Render. This
explicitly permits only `/start`, `/help`, and `/whoami` replies. Restore this flag to false after
onboarding.

After identity mapping and KPI configuration both pass preflight, an administrator may temporarily
set `ENABLE_TEAM_WORKFLOWS=true` and `ENABLE_DRY_RUN_TEAM_REPLIES=true` while keeping
`DRY_RUN=true`. This permits private team-command replies so testers can inspect read results and
dry-run previews; writes remain suppressed by the workflow service's dry-run guard. Group commands
remain rejected before workflow dispatch and do not receive team replies. Keep scheduled workflows,
analytics, and webhooks disabled. Restore `ENABLE_DRY_RUN_TEAM_REPLIES=false` after the test window.
Do not disable dry-run or approve live writes until the separate write-approval gate is complete.

For a later read-only live-reply stage, after the private dry-run tests pass, keep
`ENABLE_TEAM_WRITES=false`. You may set `DRY_RUN=false` only after approving live message delivery,
while the separate write flag remains false. The app then continues to force `/goal` and `/blocker`
into dry-run mode even though private replies are delivered. Only set `ENABLE_TEAM_WRITES=true`
after a separate approval of spreadsheet mutation targets, permissions, auditability, and rollback.

## 4. Gate team workflows separately

Before setting `ENABLE_TEAM_WORKFLOWS=true`, all of the following must be true:

- Latest deployed code matches the merged `main` commit and CI is green.
- Protected preflight returns `status: passed`, including identity and KPI configuration checks.
- Pilot KPIs are approved and their Owner dropdowns resolve through formulas.
- Private identity matching is confirmed; group commands are rejected.
- The blocker destination worksheet and its headers are verified if blocker capture is to be used.
- The Blocker Log worksheet includes `Blocker ID`, `Task ID`, `Person ID`, `Full Name`,
  `Description`, `Reported At`, and `Status` headers.
- Each `/blocker` command uses `/blocker <Task ID> <description>`; Polaris must find exactly
  one matching `Task ID` in the Master Task Tracker before previewing or recording a blocker.
- The authoritative Job Descriptions worksheet and its headers are verified if `/jd` is to be used.
- A dedicated Google account has only the required spreadsheet permissions. Prefer Viewer until a
  specific approved write workflow needs Editor.
- A rollback path and the person responsible for monitoring are known.

Keep `DRY_RUN=true` initially even after enabling the team workflow object. Validate read paths and
dry-run previews. Only approve real writes after reviewing each write target and testing it against
a non-production copy or explicitly approved pilot rows. Because `/goal` and `/blocker` can mutate Sheets, `ENABLE_TEAM_WRITES` must remain false until
those writes are separately accepted. The application forces team workflows into dry-run mode when
this flag is false, even if `DRY_RUN=false` for approved reply delivery.

## 5. Gate scheduled workflows

Keep `ENABLE_SCHEDULED_WORKFLOWS=false` until the following have been verified independently:

- `TELEGRAM_CORE_CHAT_ID` is the intended private/core destination and not a public group.
- Task flags such as `Notify?`, `Reachable?`, `Remind Now?`, and `Escalate?` reflect approved
  workbook rules and recipient routing.
- Reminder receipts/cooldowns use persistent storage; the deployment is a single instance.
- The task reminder and escalation message templates and maximum daily limits are reviewed.
- A dry-run observation window shows expected recipients and no duplicate reminders.

Do not enable scheduled workflows at the same time as the first live team-command test.

## 6. Gate analytics and webhooks independently

Analytics remain disabled until GA4 property and YouTube channel/API credentials are verified and
privacy access is tested. Webhooks remain disabled until the sender's signature contract, HTTPS,
secret rotation, persistent SQLite storage, idempotency, and monitoring are verified. These are
separate acceptance tracks and are not made safe by a successful team-data preflight.

## Acceptance record

For each stage, record the deployed commit, date, reviewer, preflight result, test result, and
rollback decision in the team's internal operations log. Do not include secrets or personal
Telegram IDs in the record.
