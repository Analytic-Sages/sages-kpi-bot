from openpyxl import Workbook

from scripts.import_sageops_workbook import load, report


def make_workbook(path):
    workbook = Workbook()
    teams = workbook.active
    teams.title = "Teams"
    teams.append(["Team ID", "Team Name", "Team Lead ID"])
    teams.append(["TM001", "Team Alpha", "P001"])
    teams.append(["TM002", None, None])  # prefilled IDs are not a real row

    people = workbook.create_sheet("People Directory")
    people.append(["Person ID", "Full Name", "Team", "Role", "Email", "Telegram Chat ID",
                   "Reports To ID", "Status", "Notify?", "Start Date", "End Date",
                   "Replacement For ID"])
    people.append(["P001", "Ada Example", "Team Alpha", "Team Lead", "ada@example.com",
                   "10001", "P001", "Active", "Yes", None, None, None])
    people.append(["P002", None, "Team Alpha", "Staff", None, None, None, None, None, None, None, None])

    kpis = workbook.create_sheet("KPI Register")
    kpis.append(["KPI ID", "Team", "Role", "KPI", "Definition / Measurement", "Direction",
                 "Frequency", "Owner Person ID", "Target", "Unit", "Data Source", "Active?"])
    kpis.append(["KPI001", "Team Alpha", "Team Lead", "Delivery", "Definition", "Increase",
                 "Weekly", "P001", 90, "%", "Manual", "Yes"])
    kpis.append(["KPI002", "Team Alpha", "Staff", "Quality", "Definition", "Increase",
                 "Weekly", None, 95, "%", "Manual", "Yes"])

    tasks = workbook.create_sheet("Master Task Tracker")
    tasks.append(["Task ID", "Task Name", "Task Type", "Team", "Assigned To ID", "Assigned By ID",
                  "Reviewer ID", "Priority", "Date Assigned", "Deadline", "Status", "% Complete"])
    tasks.append(["T0001", "Prepare report", "General", "Team Alpha", "P001", "P001",
                  None, "High", None, None, "In Progress", 0])
    tasks.append(["T0002", None, None, None, None, None, None, None, None, None, None, None])

    weekly = workbook.create_sheet("Weekly KPI Log")
    weekly.append(["Week Start", "KPI", "KPI ID", "Team", "Owner Person ID", "Direction",
                   "Target Override", "Target", "Actual", "Achievement %", "Status", "Comment",
                   "Next Action"])
    reviews = workbook.create_sheet("Weekly Review")
    reviews.append(["Week Start", "Team", "Tasks Due", "Completed", "Completion %",
                    "Key Achievement", "Challenge / Blocker", "Decision / Next Step",
                    "Action Owner", "Action Due Date", "Action Status", "Action Owner ID"])
    config = workbook.create_sheet("Config")
    config.append(["Setting Key", "Value", "What it does"])
    config.append(["Cfg_BotEnabled", "Yes", "Global switch"])
    workbook.save(path)


def test_importer_ignores_prefilled_ids_and_preserves_unassigned_team_kpis(tmp_path):
    path = tmp_path / "tracker.xlsx"
    make_workbook(path)
    data, _ = load(path)
    result = report(data)

    assert result["counts"] == {
        "teams": 1,
        "people": 1,
        "kpis": 2,
        "tasks": 1,
        "weekly_kpi_results": 0,
        "weekly_reviews": 0,
    }
    assert result["ready"] is True
    assert len(result["warnings"]) == 1
    assert result["warnings"][0]["kpi_id"] == "KPI002"
    assert result["issues"] == []
