"""Tests for job serialisation in ValidationJobService.

Each job fans out to `pipeline.concurrent_requests` in-flight LLM calls, so jobs are
run one at a time to keep that value the pipeline's absolute in-flight ceiling rather
than a per-upload multiplier. These tests run offline by substituting ValidationService.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from src.services.validation_job_service import ValidationJobService

JOB_DURATION = 0.2
TIMEOUT = 15.0


class OverlapRecorder:
    """Substitutes ValidationService and records how many jobs run concurrently.

    Raises once it has recorded its overlap, which drives the job to "failed" — we only
    care about slot acquisition and release here, not pipeline output.
    """

    max_in_flight = 0
    _in_flight = 0
    _lock = threading.Lock()

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls.max_in_flight = 0
            cls._in_flight = 0

    def __init__(self) -> None:
        cls = type(self)
        with cls._lock:
            cls._in_flight += 1
            cls.max_in_flight = max(cls.max_in_flight, cls._in_flight)
        time.sleep(JOB_DURATION)
        with cls._lock:
            cls._in_flight -= 1
        raise RuntimeError("stop here - the slot is what is under test")


def _wait_for(predicate, timeout: float = TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _start(service: ValidationJobService, name: str):
    return service.start_job(pdf_path=f"/tmp/does-not-exist-{name}.pdf", source_filename=f"{name}.pdf", rules_json_str=None)


def test_jobs_never_overlap() -> None:
    OverlapRecorder.reset()
    service = ValidationJobService()

    with patch("src.services.validation_job_service.ValidationService", OverlapRecorder):
        jobs = [_start(service, str(i)) for i in range(4)]
        assert _wait_for(
            lambda: all(service.get_job(j.job_id).status == "failed" for j in jobs)
        ), "jobs did not all finish"

    assert OverlapRecorder.max_in_flight == 1, (
        f"jobs ran concurrently (max_in_flight={OverlapRecorder.max_in_flight}); "
        "the configured concurrency would be multiplied by the number of uploads"
    )


def test_job_cancelled_while_queued_never_runs() -> None:
    OverlapRecorder.reset()
    service = ValidationJobService()

    with patch("src.services.validation_job_service.ValidationService", OverlapRecorder):
        first = _start(service, "first")
        second = _start(service, "second")
        # `second` is still waiting for the slot held by `first`.
        service.cancel_job(second.job_id)

        assert _wait_for(lambda: service.get_job(second.job_id).status == "cancelled")
        assert _wait_for(lambda: service.get_job(first.job_id).status == "failed")


def test_slot_is_released_after_a_cancelled_job() -> None:
    """The early return for a cancelled job must not leak the slot."""
    OverlapRecorder.reset()
    service = ValidationJobService()

    with patch("src.services.validation_job_service.ValidationService", OverlapRecorder):
        first = _start(service, "a")
        queued = _start(service, "b")
        service.cancel_job(queued.job_id)
        assert _wait_for(lambda: service.get_job(queued.job_id).status == "cancelled")
        assert _wait_for(lambda: service.get_job(first.job_id).status == "failed")

        # If either previous job leaked its slot, this one would never start.
        third = _start(service, "c")
        assert _wait_for(
            lambda: service.get_job(third.job_id).status == "failed"
        ), "slot leaked - a later job could not acquire it"
