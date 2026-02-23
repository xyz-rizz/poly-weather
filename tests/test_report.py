from pathlib import Path

from weather_bot.report import build_report


def test_report_handles_missing_files(tmp_path: Path) -> None:
    report = build_report(tmp_path)
    assert report["files"]["scan_snapshots"] == 0
    assert report["planning_summary"]["accept_reject_counts"] == {}
