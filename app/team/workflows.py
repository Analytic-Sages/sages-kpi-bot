"""Schema-guarded team workflows for Polaris commands."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from app.sheets.client import GoogleSheetsClient, GoogleSheetsError

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "y", "active", "current"}
_ACTIVE_VALUES = {"active", "current", "employed"}
MAX_JD_CHARS = 3000


def _kpi_label(row: dict[str, str]) -> str:
    """Prefer the manually maintained KPI name over formula-generated helper text."""
    return row.get("KPI", "").strip() or row.get("KPI Label", "").strip() or "Unnamed KPI"


class TeamWorkflowError(RuntimeError):
    """Raised when team data cannot be safely used for a workflow."""


class TeamWorkflowService:
    """Handle team commands using header-driven Sheets access and fail-closed checks.

    The service is intentionally opt-in. It must not be enabled until the live workbook's
    identity fields, KPI schema, blocker destination, and authoritative JD source are verified.
    """

    def __init__(
        self,
        sheets: GoogleSheetsClient,
        *,
        job_descriptions_worksheet: str | None = None,
        blocker_worksheet: str | None = None,
        dry_run: bool = True,
    ) -> None:
        self.sheets = sheets
        self.job_descriptions_worksheet = job_descriptions_worksheet
        self.blocker_worksheet = blocker_worksheet
        self.dry_run = dry_run

    async def handle(self, command: str, args: str, chat_id: int | str) -> str:
        """Dispatch one team command and return a safe user-facing response."""
        try:
            person = await self._person_for_chat(chat_id)
            if person is None:
                return (
                    "I couldn't match this Telegram chat to one active team member. "
                    "Ask an administrator to verify the People Directory identity fields."
                )
            if command == "/status":
                return await self._status(person)
            if command == "/goal":
                return await self._goal(person, args)
            if command == "/blocker":
                return await self._blocker(person, args)
            if command == "/jd":
                return await self._job_description(person, args)
            return "That team command isn't supported."
        except (GoogleSheetsError, TeamWorkflowError) as exc:
            logger.warning("team_workflow_blocked command=%s reason=%s", command, exc)
            return (
                "I couldn't safely complete that request because the required team-sheet "
                "schema or data is unavailable. No changes were made. Ask an administrator "
                "to verify the workbook configuration."
            )

    async def _person_for_chat(self, chat_id: int | str) -> dict[str, str] | None:
        people = await self.sheets.read_records("People Directory")
        required = {"Person ID", "Full Name", "Role", "Status", "Telegram Chat ID"}
        if not people or not required.issubset(people[0]):
            raise TeamWorkflowError("People Directory is missing identity headers")
        matches = [
            row for row in people
            if row.get("Telegram Chat ID", "").strip() == str(chat_id).strip()
        ]
        if len(matches) != 1:
            return None
        person = matches[0]
        if person.get("Status", "").strip().casefold() not in _ACTIVE_VALUES:
            return None
        return person

    async def _status(self, person: dict[str, str]) -> str:
        kpis = await self.sheets.read_records("KPI Register")
        logs = await self.sheets.read_records("Weekly KPI Log")
        required_kpis = {"KPI ID", "KPI", "Owner Person ID", "Target", "Active?"}
        required_logs = {"Week Start", "KPI ID", "Actual", "Status"}
        if (kpis and not required_kpis.issubset(kpis[0])) or (
            logs and not required_logs.issubset(logs[0])
        ):
            raise TeamWorkflowError("KPI sheets do not match the supported schema")
        if not kpis:
            raise TeamWorkflowError("KPI Register has no readable records")
        person_kpis = [
            kpi for kpi in kpis
            if kpi.get("Owner Person ID", "").strip() == person["Person ID"]
            and kpi.get("Active?", "").strip().casefold() in _TRUE_VALUES
        ]
        if not person_kpis:
            return f"Status for {person['Full Name']}: no active KPIs are assigned in the register."
        lines = [f"**Status — {person['Full Name']}**"]
        for kpi in person_kpis[:5]:
            label = _kpi_label(kpi)
            observations = [
                log for log in logs
                if log.get("KPI ID", "").strip() == kpi.get("KPI ID", "").strip()
            ]
            latest = max(observations, key=lambda item: item.get("Week Start", ""), default=None)
            if latest:
                lines.append(
                    f"• {label}: {latest.get('Actual', '—')} "
                    f"(target {kpi.get('Target', '—')}; "
                    f"{latest.get('Status', 'status unavailable')})"
                )
            else:
                lines.append(
                    f"• {label}: no weekly result recorded "
                    f"(target {kpi.get('Target', '—')})"
                )
        if len(person_kpis) > 5:
            lines.append(f"Showing 5 of {len(person_kpis)} active KPIs.")
        return "\n".join(lines)

    async def _goal(self, person: dict[str, str], args: str) -> str:
        parts = args.strip().rsplit(maxsplit=1)
        if len(parts) != 2:
            return "Usage: /goal <KPI name> <numeric target>"
        metric, target = parts
        try:
            float(target.replace(",", ""))
        except ValueError:
            return "The target must be numeric. Example: /goal Qualified Leads 20"
        kpis = await self.sheets.read_records("KPI Register")
        required = {"KPI ID", "KPI", "Owner Person ID", "Target", "Active?"}
        if not kpis or not required.issubset(kpis[0]):
            raise TeamWorkflowError("KPI Register is missing required fields")
        matches = [
            row for row in kpis
            if _kpi_label(row).casefold() == metric.casefold()
            and row.get("Owner Person ID", "").strip() == person["Person ID"]
            and row.get("Active?", "").strip().casefold() in _TRUE_VALUES
        ]
        if len(matches) != 1:
            return (
                "I couldn't find exactly one active KPI with that name assigned to you. "
                "Check the KPI Register or ask an administrator to resolve duplicates."
            )
        kpi_id = matches[0].get("KPI ID", "").strip()
        if not kpi_id:
            raise TeamWorkflowError("KPI has no stable KPI ID")
        if self.dry_run:
            return (
                f"DRY RUN: I would update {_kpi_label(matches[0])} target to "
                f"{target.replace(',', '')}. No spreadsheet changes were made."
            )
        await self.sheets.update_record(
            "KPI Register",
            key_header="KPI ID",
            key_value=kpi_id,
            values={"Target": target.replace(",", "")},
        )
        return f"Target updated for {_kpi_label(matches[0])}: {target}."

    async def _blocker(self, person: dict[str, str], args: str) -> str:
        parts = args.strip().split(maxsplit=1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            return "Usage: /blocker <Task ID> <describe the blocker>"

        task_id, description = parts[0].strip(), parts[1].strip()
        if not self.blocker_worksheet:
            return (
                "Blocker capture is not enabled yet because no verified blocker log is "
                "configured. Your message was not stored; please report it to your team lead."
            )

        required = {
            "Blocker ID",
            "Task ID",
            "Person ID",
            "Full Name",
            "Description",
            "Reported At",
            "Status",
        }
        headers = await self.sheets.read_headers(self.blocker_worksheet)
        if not required.issubset(headers):
            raise TeamWorkflowError(
                "Configured blocker worksheet is missing required headers"
            )

        task_headers = await self.sheets.read_headers("Master Task Tracker")
        if "Task ID" not in task_headers:
            raise TeamWorkflowError("Master Task Tracker is missing the Task ID header")
        tasks = await self.sheets.read_records("Master Task Tracker")
        task_matches = [
            row for row in tasks
            if row.get("Task ID", "").strip().casefold() == task_id.casefold()
        ]
        if len(task_matches) != 1:
            return (
                f"I couldn't find exactly one task with ID {task_id} in the Master Task Tracker. "
                "Check the task ID and try again. No changes were made."
            )

        if self.dry_run:
            return (
                f"DRY RUN: I would record a blocker for task {task_id} "
                f"for {person['Full Name']}. No spreadsheet changes were made."
            )
        await self.sheets.append_record(
            self.blocker_worksheet,
            {
                "Blocker ID": str(uuid4()),
                "Task ID": task_id,
                "Person ID": person["Person ID"],
                "Full Name": person["Full Name"],
                "Description": description,
                "Reported At": datetime.now(UTC).isoformat(timespec="seconds"),
                "Status": "Open",
            },
        )
        return f"Blocker recorded for task {task_id} and marked Open."

    async def _job_description(self, person: dict[str, str], args: str) -> str:
        if not self.job_descriptions_worksheet:
            return (
                "Role intelligence is not enabled yet because the authoritative Job "
                "Descriptions source has not been configured. No notes or guessed role "
                "descriptions were used."
            )
        target = args.strip()
        people = await self.sheets.read_records("People Directory")
        if target:
            candidates = [
                row for row in people
                if row.get("Full Name", "").strip().casefold() == target.casefold()
                or row.get("Telegram Handle", "").strip().lstrip("@").casefold()
                == target.lstrip("@").casefold()
            ]
            if len(candidates) != 1:
                return "I couldn't identify exactly one person by that name or Telegram handle."
            selected = candidates[0]
            if selected.get("Status", "").strip().casefold() not in _ACTIVE_VALUES:
                return "That person is not marked active in the People Directory."
        else:
            selected = person
        role = selected.get("Role", "").strip()
        if not role:
            return "No role is recorded for that team member."
        descriptions = await self.sheets.read_records(self.job_descriptions_worksheet)
        required = {"Role", "Job Description"}
        if not descriptions or not required.issubset(descriptions[0]):
            raise TeamWorkflowError("Job Descriptions source is missing required headers")
        matches = [
            row for row in descriptions
            if row.get("Role", "").strip().casefold() == role.casefold()
        ]
        if len(matches) != 1:
            return "I couldn't find exactly one authoritative job description for this role."
        description = matches[0].get("Job Description", "").strip()
        if not description:
            return "The authoritative job description for this role is empty."
        heading = f"**{role} — {selected.get('Full Name', 'Team member')}**"
        return f"{heading}\n{description[:MAX_JD_CHARS]}"
