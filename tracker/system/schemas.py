import datetime

from fastapi import Form
from pydantic import NonNegativeInt, field_validator
from pydantic_core.core_schema import ValidationInfo

from tracker.common.schemas import CustomBaseModel
from tracker.models import enums


class GetSpanReportRequest(CustomBaseModel):
    start: datetime.date
    stop: datetime.date

    def __init__(
        self,
        start: datetime.date = Form(...),
        stop: datetime.date | None = Form(None),
    ) -> None:
        # way to check Form value is None
        if not isinstance(stop, str):
            stop = datetime.datetime.now(tz=datetime.UTC).replace(tzinfo=None).date()

        super().__init__(start=start, stop=stop)

    @field_validator("stop")
    def validate_start_less_than_stop(
        cls,
        stop: datetime.date,
        info: ValidationInfo,
    ) -> datetime.date:
        start = info.data["start"]
        assert stop > start, "Start must be less than stop"

        return stop

    @property
    def size(self) -> int:
        return (self.stop - self.start).days + 1

    def create_span_ago(self, ago: int) -> "GetSpanReportRequest":
        if ago <= 0:
            raise ValueError(f"Ago must be > 0, {ago!r} found")

        return self.__class__(
            start=self.start - datetime.timedelta(days=ago),
            stop=self.stop - datetime.timedelta(days=ago),
        )


class _SpanStats(CustomBaseModel):
    total: int
    median: float
    mean: float
    lost_count: int
    zero_days: int
    would_be_total: int


class GetSpanReportResponse(CustomBaseModel):
    completed_materials: dict[enums.MaterialTypesEnum, int]
    total_materials_completed: int

    read_items: dict[enums.MaterialTypesEnum, int]
    reading: _SpanStats
    notes: _SpanStats

    repeats_total: int
    repeat_materials_count: int


class BackupResponse(CustomBaseModel):
    materials_count: NonNegativeInt
    reading_log_count: NonNegativeInt
    statuses_count: NonNegativeInt
    notes_count: NonNegativeInt
    cards_count: NonNegativeInt
    repeats_count: NonNegativeInt
    note_repeats_history_count: NonNegativeInt
