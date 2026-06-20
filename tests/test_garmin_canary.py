"""Tests for the G11 Garmin schema-drift canary.

"The whole write path rides an undocumented API ... instead of
discovering it when an upload silently corrupts your calendar." These
exercise upload_garmin_workouts._verify()/garmin_contract_check() against
a fake Garmin client (no network, no real account) covering: the happy
path, a vanished workout, an extra (third-party) workout, and a step
that's silently degraded to lap.button (the exact Bug 7 regression
builders.py's own tests guard against, here checked on the *read* path
instead of the write path).

Also covers a real bug found while building this: _verify(deep=True)
used to return True as long as every *present* workout deep-checked
clean, even when a workout had vanished entirely (missing/extra were
logged but never folded into the boolean). Fixed alongside this ticket;
test_deep_verify_fails_on_missing_workout pins it.
"""
import copy

import pytest

import upload_garmin_workouts as ugw
from plan import build_plan
from upload_garmin_workouts import _verify


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    """api()'s ~0.5s/call rate-limit throttle is real and load-bearing
    against the actual Garmin API -- not something to weaken in
    production code. But these tests call it 1-3x each against a fake
    client with zero real rate-limit risk, so the throttle just makes
    the suite slower for no benefit. Zero it out for tests only."""
    monkeypatch.setattr(ugw, "MIN_INTERVAL", 0.0)

# api()'s real ~0.5s/call throttle still applies even to a fake client --
# deep-checking the full 93-workout plan would make this file alone take
# nearly a minute for no extra coverage. A handful of real workouts (not
# synthetic ones -- still exercising the real builders.py payload shapes)
# is enough to cover missing/extra/deep-check scenarios fast.
PLAN = build_plan()[:3]


class _FakeClient:
    """Mimics garminconnect.Garmin's .connectapi() just enough to drive
    list_remote()/api()'s two call shapes: the workouts list, and a
    single workout's full detail."""

    def __init__(self, remote_summaries, remote_details):
        self.remote_summaries = remote_summaries
        self.remote_details = remote_details

    def connectapi(self, path, method="GET", json=None):
        if path.startswith("/workout-service/workouts"):
            return self.remote_summaries
        if path.startswith("/workout-service/workout/"):
            wid = path.rsplit("/", 1)[-1]
            return self.remote_details[wid]
        raise AssertionError("unexpected path: %s" % path)


def _build_matching_remote(plan):
    """A fake 'remote' that exactly mirrors what build_plan() expects,
    so the happy-path tests aren't tautological -- the remote summaries
    and details are independently constructed dicts, not the same
    objects build_plan() produced."""
    summaries, details = [], {}
    for i, p in enumerate(plan):
        wid = str(1000 + i)
        summaries.append({"workoutName": p["name"], "workoutId": wid})
        details[wid] = {"workoutSegments": copy.deepcopy(p["payload"]["workoutSegments"])}
    return summaries, details


class TestVerifyShallow:
    def test_passes_when_remote_matches_plan_exactly(self):
        summaries, details = _build_matching_remote(PLAN)
        client = _FakeClient(summaries, details)
        assert _verify(client, PLAN, deep=False) is True

    def test_fails_when_a_workout_is_missing(self):
        summaries, details = _build_matching_remote(PLAN)
        summaries = summaries[1:]  # drop the first workout
        client = _FakeClient(summaries, details)
        assert _verify(client, PLAN, deep=False) is False

    def test_fails_on_an_unexpected_extra_workout(self):
        summaries, details = _build_matching_remote(PLAN)
        summaries = summaries + [{"workoutName": "W3 Tue Mystery Workout", "workoutId": "9999"}]
        client = _FakeClient(summaries, details)
        assert _verify(client, PLAN, deep=False) is False

    def test_ignores_third_party_runna_workouts(self):
        """is_plan_name() excludes Runna's ' - ' naming -- a Runna
        workout sitting alongside ours must never trip the canary."""
        summaries, details = _build_matching_remote(PLAN)
        summaries = summaries + [
            {"workoutName": "W3 Tue Hill Repeats - Runna", "workoutId": "8888"}]
        client = _FakeClient(summaries, details)
        assert _verify(client, PLAN, deep=False) is True


class TestVerifyDeep:
    def test_passes_when_everything_matches(self):
        summaries, details = _build_matching_remote(PLAN)
        client = _FakeClient(summaries, details)
        assert _verify(client, PLAN, deep=True) is True

    def test_fails_on_missing_workout_even_if_present_ones_are_fine(self):
        """Regression test for the bug found+fixed alongside this
        ticket: a vanished workout must fail the deep check too, not
        just get logged while the boolean stays True."""
        summaries, details = _build_matching_remote(PLAN)
        summaries = summaries[1:]
        client = _FakeClient(summaries, details)
        assert _verify(client, PLAN, deep=True) is False

    def test_fails_when_a_step_degrades_to_lap_button(self):
        """The exact Bug 7 shape: conditionTypeId 1 / lap.button where a
        real condition (distance/time/iterations) should be -- this is
        what 'Garmin changed their schema' would actually look like."""
        summaries, details = _build_matching_remote(PLAN)
        wid = summaries[0]["workoutId"]
        details[wid]["workoutSegments"][0]["workoutSteps"][0]["endCondition"] = {
            "conditionTypeId": 1, "conditionTypeKey": "lap.button"}
        client = _FakeClient(summaries, details)
        assert _verify(client, PLAN, deep=True) is False

    def test_fails_on_distance_drift(self):
        summaries, details = _build_matching_remote(PLAN)
        wid = summaries[0]["workoutId"]
        step = details[wid]["workoutSegments"][0]["workoutSteps"][0]
        step["endCondition"] = {"conditionTypeId": 3, "conditionTypeKey": "distance"}
        step["endConditionValue"] = (step.get("endConditionValue") or 1000) + 50000
        client = _FakeClient(summaries, details)
        assert _verify(client, PLAN, deep=True) is False


class TestGarminContractCheck:
    def test_raises_on_mismatch_instead_of_returning_false(self, monkeypatch):
        """garmin_contract_check() turns a False _verify() result into a
        RuntimeError -- that's the whole point: _run_resilient_loop
        (G10) only reacts to exceptions, so a real-but-not-exceptional
        'answer was no' has to become one for the existing log+push
        machinery to catch it."""
        monkeypatch.setattr(ugw, "get_client", lambda: object())
        monkeypatch.setattr(ugw, "build_plan", lambda: PLAN)
        monkeypatch.setattr(ugw, "_verify", lambda client, plan, deep: False)
        with pytest.raises(RuntimeError, match="mismatch"):
            ugw.garmin_contract_check(deep=True)

    def test_returns_true_on_a_clean_check(self, monkeypatch):
        monkeypatch.setattr(ugw, "get_client", lambda: object())
        monkeypatch.setattr(ugw, "build_plan", lambda: PLAN)
        monkeypatch.setattr(ugw, "_verify", lambda client, plan, deep: True)
        assert ugw.garmin_contract_check(deep=True) is True
