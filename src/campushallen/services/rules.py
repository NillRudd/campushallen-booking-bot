from datetime import time
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from campushallen.database.models import BookingRule
from campushallen.schedule import BookingRule as DomainRule


class RuleInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    activity: str = Field(min_length=1, max_length=200)
    product_name: str | None = Field(default=None, max_length=200)
    weekday: int = Field(ge=0, le=6)
    booking_type: Literal["group_activity", "service"] = "group_activity"
    web_category_id: int = Field(default=40, gt=0)
    facility_contains: list[str] = Field(default_factory=list, max_length=30)
    product_ids: list[int] = Field(default_factory=list)
    business_unit_ids: list[int] = Field(default_factory=list)
    product_name_contains: list[str] = Field(default_factory=list)
    start_time: time | None = None
    preferred_start_time: time | None = None
    earliest_allowed_time: time = time(17)
    latest_allowed_time: time = time(21)
    allow_waitlist: bool = False
    enabled: bool = True

    @model_validator(mode="after")
    def valid_window(self) -> "RuleInput":
        if not self.name.strip() or not self.activity.strip():
            raise ValueError("Name and activity must not be blank")
        if self.earliest_allowed_time > self.latest_allowed_time:
            raise ValueError("Earliest time must be before latest time")
        if self.booking_type == "service" and self.start_time is None:
            raise ValueError("Service rules require a fixed start time")
        for value in (self.start_time, self.preferred_start_time):
            if value and not self.earliest_allowed_time <= value <= self.latest_allowed_time:
                raise ValueError("Start time must be inside the allowed window")
        return self


def domain_rule(rule: BookingRule) -> DomainRule:
    return DomainRule(
        activity=rule.activity,
        weekday=rule.weekday,
        start_time=rule.start_time,
        booking_type=rule.booking_type,
        web_category_id=rule.web_category_id,
        name=rule.name,
        product_name=rule.product_name,
        product_ids=tuple(rule.product_ids),
        business_unit_ids=tuple(rule.business_unit_ids),
        product_name_contains=tuple(rule.product_name_contains),
        facility_contains=tuple(rule.facility_contains),
        preferred_start_time=rule.preferred_start_time,
        allowed_start_earliest=rule.earliest_allowed_time,
        allowed_start_latest=rule.latest_allowed_time,
    )
