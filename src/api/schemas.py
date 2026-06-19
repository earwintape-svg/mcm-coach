"""Pydantic request models for src/api/routes.py's mutating endpoints.

These replace the previous `body: dict` + manual `int()`/`str()` coercion
and bounds checks (T5) -- FastAPI now returns 422 on bad input instead of
a 500/raw KeyError from inside the handler.

Python 3.9 note (T6/RC-4): use Optional[X] / Union[X, Y] in every model
here, never `X | Y`. FastAPI calls get_type_hints() on these models the
same way it does on route signatures -- the newer union syntax would
raise at import time on 3.9, which is what production actually runs.
"""
from __future__ import annotations
from datetime import date as _date
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, Field, field_validator


def _iso_date(v: str) -> str:
    """Shared validator: must parse as an ISO yyyy-mm-dd date."""
    _date.fromisoformat(v)
    return v


class MoveBody(BaseModel):
    scheduleId: int
    workoutId: int
    date: str

    @field_validator("date")
    @classmethod
    def _check_date(cls, v):
        return _iso_date(v)


class ShiftRangeBody(BaseModel):
    model_config = {"populate_by_name": True}

    from_: str = Field(alias="from")
    to: str
    days: int = Field(..., ge=-90, le=90)

    @field_validator("from_", "to")
    @classmethod
    def _check_date(cls, v):
        return _iso_date(v)


class UnscheduleBody(BaseModel):
    scheduleId: int


class ImportBody(BaseModel):
    date: str
    source: str
    # Genuinely free-form: /api/import is a generic inbox for whatever a
    # third-party source (Apple Health via Health Auto Export, etc.) sends.
    # Model the envelope; the payload inside stays a dict by design.
    metrics: Optional[Dict[str, Any]] = None

    @field_validator("date")
    @classmethod
    def _check_date(cls, v):
        return _iso_date(v)


class GearBody(BaseModel):
    key: str
    display: Optional[str] = None
    startMi: Optional[float] = None
    thresholdMi: Optional[float] = None
    retired: Optional[bool] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    isDefault: Optional[bool] = None


class RunGearBody(BaseModel):
    gearId: Optional[str] = None


class CoachApplyBody(BaseModel):
    # Permissive by design, matching the handler's existing branching:
    # workoutId/to only matter when action == "move"; anything else falls
    # through to unschedule_workout(scheduleId). Not adding a stricter
    # conditional-required validation here since that would change
    # behavior, not just add validation.
    action: Optional[str] = None
    scheduleId: int
    workoutId: Optional[int] = None
    to: Optional[str] = None


class AnnotateBody(BaseModel):
    # Observed both Garmin (int) and intervals.icu ("i123", str) activity
    # ids from the frontend -- the handler stringifies+truncates either way.
    activityId: Union[int, str]
    rpe: Optional[int] = Field(None, ge=1, le=10)
    note: Optional[str] = None
    shoes: Optional[str] = None
