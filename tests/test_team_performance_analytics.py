from datetime import date

from app.analytics.team_performance import build_team_performance


def test_team_and_member_metrics_and_followup_queue():
    people = [
        {"Person ID": "p1", "Full Name": "Ada", "Team": "Research", "Status": "Active"},
        {"Person ID": "p2", "Full Name": "Ben", "Team": "Engineering", "Status": "Active"},
        {"Person ID": "p3", "Full Name": "Inactive", "Team": "Research", "Status": "Inactive"},
    ]
    tasks = [
        {
            "Task ID": "t1", "Task Name": "Ship report", "Assigned To ID": "p1",
            "Status": "Completed", "Due Date": "2026-10-10",
        },
        {
            "Task ID": "t2", "Task Name": "Review data", "Assigned To ID": "p1",
            "Status": "In Progress", "Due Date": "2026-10-11", "Last Updated": "2026-10-09",
        },
        {
            "Task ID": "t3", "Task Name": "Fix pipeline", "Assigned To ID": "p2",
            "Status": "Blocked", "Due Date": "2026-10-20",
        },
        {"Task ID": "t4", "Task Name": "No date", "Assigned To ID": "p2", "Status": "Open"},
    ]
    kpi_register = [
        {
            "KPI ID": "k1", "KPI": "Reports", "Owner Person ID": "p1",
            "Target": "10", "Team": "Research",
        },
    ]
    kpi_log = [
        {
            "Week Start": "2026-10-12", "KPI ID": "k1", "Owner Person ID": "p1",
            "Target": "10", "Actual": "7",
        },
    ]
    result = build_team_performance(
        people, tasks, kpi_register, kpi_log, today=date(2026, 10, 17),
        start_date=date(2026, 10, 1), end_date=date(2026, 10, 31),
    )
    assert result["summary"]["active_members"] == 2
    assert result["summary"]["tasks"] == 4
    assert result["summary"]["completed"] == 1
    assert result["summary"]["overdue"] == 1
    assert result["summary"]["blocked"] == 1
    assert result["summary"]["completion_rate"] == 25.0
    assert {item["id"] for item in result["tasks"]} == {"t2", "t3"}
    assert result["kpis"][0]["attainment"] == 70.0
    assert result["summary"]["kpis_below_target"] == 1


def test_missing_and_ambiguous_records_are_counted_not_hidden():
    result = build_team_performance(
        [{"Full Name": "No ID", "Team": "Ops"}],
        [{"Task Name": "No ID or owner", "Status": "Open", "Due Date": "not-a-date"}],
        [{"KPI": "No key"}],
        [{"KPI ID": "unknown", "Actual": "3"}],
        today=date(2026, 10, 17), start_date=date(2026, 10, 1), end_date=date(2026, 10, 31),
    )
    assert result["data_quality"]["counts"]["people_missing_id"] == 1
    assert result["data_quality"]["counts"]["tasks_missing_id"] == 1
    assert result["data_quality"]["counts"]["tasks_missing_owner"] == 1
    assert result["data_quality"]["counts"]["tasks_unparseable_due_date"] == 1
    assert result["data_quality"]["counts"]["kpis_missing_id"] == 1
    assert result["data_quality"]["counts"]["kpi_log_unmatched_register"] == 1


def test_filters_are_applied_to_team_member_and_status():
    people = [{"Person ID": "p1", "Full Name": "Ada", "Team": "Research", "Status": "Active"}]
    tasks = [
        {"Task ID": "t1", "Assigned To ID": "p1", "Status": "Open"},
        {"Task ID": "t2", "Assigned To ID": "p1", "Status": "Completed"},
    ]
    result = build_team_performance(
        people, tasks, [], [], today=date(2026, 10, 17),
        start_date=date(2026, 10, 1), end_date=date(2026, 10, 31),
        team_filter="research", person_filter="p1", status_filter="completed",
    )
    assert result["summary"]["tasks"] == 1
    assert result["summary"]["completed"] == 1
