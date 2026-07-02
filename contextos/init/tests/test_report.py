"""Task 5 (Block 2): InitReport / StepResult schema tests.

Design intent:
  - report.py is the structured result of `contextos init`, consumed by run_init
    (Task 6), the CLI (Task 7), and the e2e smoke (Task 8). Field names and Literal
    value sets are a contract those depend on, so they must be locked by tests.

Scoring / pass criteria:
  1. test_init_report_verdict_ready_all_ok: a well-formed report round-trips —
     verdict is preserved and nested StepResult.counts are accessible.
  2. test_step_result_rejects_unknown_status: an invalid status value is rejected
     at construction (Literal enforcement via pydantic ValidationError).

Test logic (automated):
  - Pure schema construction; no engine/IO. Test 2 passes an intentionally invalid
    Literal value to confirm runtime validation fires.
"""
import pytest
from pydantic import ValidationError

from contextos.init.report import InitReport, StepResult


def test_init_report_verdict_ready_all_ok():
    r = InitReport(steps=[StepResult(dimension="code", status="ok", counts={}),
                          StepResult(dimension="database", status="ok", counts={"edges": 3})],
                   verdict="ready", reasons=[])
    assert r.verdict == "ready" and r.steps[1].counts["edges"] == 3


def test_step_result_rejects_unknown_status():
    with pytest.raises(ValidationError):
        # "bogus" is intentionally invalid to trigger the Literal ValidationError;
        # the ignore comment silences the checkers' correct compile-time flag.
        StepResult(dimension="code", status="bogus", counts={})  # type: ignore[arg-type]
