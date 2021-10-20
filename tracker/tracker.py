#!/usr/bin/env python3
import datetime
import statistics
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable, Iterator, Optional, Union

from tracker.common import database, settings
from tracker.common.log import logger


INDENT = 2


def fmt(value: Any) -> str:
    return ''


@dataclass(frozen=True)
class MinMax:
    date: datetime.date
    count: int
    material_id: int
    material_title: Optional[str] = None

    def dict(self,
             *,
             exclude: Iterable[str] = None) -> dict:
        exclude = exclude or ()

        return {
            field: getattr(self, field, None)
            for field in self.__annotations__.keys()
            if field not in exclude
        }

    def __str__(self) -> str:
        date = fmt(self.date)

        material = f"Material id: {self.material_id}"
        if material_title := self.material_title:
            material = f"Title: «{material_title}»"

        return f"Date: {date}\n" \
               f"Count: {self.count} pages\n" \
               f"{material}"


@dataclass(frozen=True)
class MaterialStatistics:
    material: database.Material
    started: datetime.date
    duration: int
    lost_time: int
    total: int
    min: Optional[MinMax]
    max: Optional[MinMax]
    average: int
    remaining_pages: Optional[int] = None
    remaining_days: Optional[int] = None
    completed: Optional[datetime.date] = None
    # date when the material would be completed
    # according to average read pages count
    would_be_completed: Optional[datetime.date] = None

    def dict(self,
             *,
             exclude: Iterable[str] = None) -> dict:
        exclude = exclude or ()

        return {
            field: getattr(self, field, None)
            for field in self.__annotations__.keys()
            if field not in exclude
        }

    def __str__(self) -> str:
        if completed := self.completed:
            completed = f"Completed at: {fmt(completed)}\n"
        else:
            completed = ''

        if would_be_completed := self.would_be_completed:
            would_be_completed = f"\nWould be completed at: " \
                                 f"{fmt(would_be_completed)}"
        else:
            would_be_completed = ''
        remaining_pages = (f"Remaining pages: {self.remaining_pages}\n" *
                           bool(self.remaining_pages))
        remaining_days = (f"Remaining days: {self.remaining_days}\n" *
                          bool(self.remaining_days))

        if min_ := self.min:
            min_ = f"Min:\n\tDate: {fmt(min_.date)}\n" \
                   f"\tCount: {min_.count} pages\n"
        else:
            min_ = ''

        if max_ := self.max:
            max_ = f"Max:\n\tDate: {fmt(max_.date)}\n" \
                   f"\tCount: {max_.count} pages\n"
        else:
            max_ = ''

        return f"Material: «{self.material.title}»\n" \
               f"Pages: {self.material.pages}\n" \
               f"Started at: {fmt(self.started)}\n" \
               f"{completed}" \
               f"Duration: {time_span(self.duration)}\n" \
               f"Lost time: {time_span(self.lost_time)}\n" \
               f"Total: {self.total} pages\n" \
               f"{remaining_pages}" \
               f"{remaining_days}" \
               f"{min_}" \
               f"{max_}" \
               f"Average: {self.average} pages per day" \
               f"{would_be_completed}"


@dataclass(frozen=True)
class TrackerStatistics:
    pass


@dataclass(frozen=True)
class MaterialEstimate:
    material: database.Material
    will_be_started: datetime.date
    will_be_completed: datetime.date
    expected_duration: int

    def dict(self,
             *,
             exclude: Iterable[str] = None) -> dict:
        exclude = exclude or ()

        return {
            field: getattr(self, field, None)
            for field in self.__annotations__.keys()
            if field not in exclude
        }

    def __str__(self) -> str:
        return f"Material: «{self.material.title}»\n" \
               f"Pages: {self.material.pages}\n" \
               f"Will be started: {fmt(self.will_be_started)}\n" \
               f"Will be completed: {fmt(self.will_be_completed)}\n" \
               f"Expected duration: {time_span(self.expected_duration)}"


def to_datetime(date) -> Optional[datetime.date]:
    """
    :param date: str or date or datetime.

    :exception ValueError: if date format is wrong.
    :exception TypeError: if the param type is wrong.
    """
    if date is None:
        return

    if isinstance(date, str):
        try:
            date = datetime.datetime.strptime(date, settings.DATE_FORMAT)
            date = date.date()
        except ValueError as e:
            raise ValueError(f"Wrong str format\n{e}:{date}")
        else:
            return date
    elif isinstance(date, datetime.datetime):
        return date.date()
    elif isinstance(date, datetime.date):
        return date
    else:
        raise TypeError(f"Str or datetime expected, {type(date)} found")


def time_span(span: Union[timedelta, int]) -> str:
    days: int = span
    if isinstance(days, timedelta):
        days = days.days

    res = ''
    if years := days // 365:
        res += f"{years} years, "
    if month := days % 365 // 30:
        res += f"{month} months, "
    if days := days % 365 % 30:
        res += f"{days} days"

    return res


class Log:
    __slots__ = '__log'

    def __init__(self) -> None:
        self.__log = {}

    def m_min(self,
              material_id: int) -> MinMax:
        """ Get info of the record with
        the min number of read pages of the material.

        :exception NoMaterialInLog:
        """
        logger.debug(f"Calculating min for material {material_id=}")

        if material_id not in self:
            raise ValueError

        sample = [
            (date, info)
            for date, info in self.log.items()
            if info.material_id == material_id
        ]

        date, info = min(
            sample,
            key=lambda item: item[1].count
        )
        return MinMax(
            date=date,
            **info.dict()
        )

    def m_max(self,
              material_id: int) -> MinMax:
        """ Get info of the record with
        the max number of read pages of the material.

        :exception NoMaterialInLog:
        """
        logger.debug(f"Calculating max for material {material_id=}")

        if material_id not in self:
            raise ValueError

        sample = [
            (date, info)
            for date, info in self.log.items()
            if info.material_id == material_id
        ]

        date, info = max(
            sample,
            key=lambda item: item[1].count
        )
        return MinMax(
            date=date,
            **info.dict()
        )

    def __getitem__(self,
                    date: Union[datetime.date, str, slice]):
        """
        Get log record by date (datetime.date or str)
        of by slice of dates.

        If slice get new Log object with [start; stop).
        """
        logger.debug(f"Getting item {date=} from the log")

        if not self.log:
            raise ValueError

        if not isinstance(date, (datetime.date, slice, str)):
            raise TypeError(f"Date or slice of dates expected, "
                            f"but {type(date)} found")

        if isinstance(date, (datetime.date, str)):
            return self.log[to_datetime(date)]

        start, stop, step = date.start, date.stop, date.step

        assert start is None or isinstance(start, (datetime.date, str))
        assert stop is None or isinstance(stop, (datetime.date, str))
        assert step is None or isinstance(step, int)

        assert not (start and stop) or start <= stop

        start = to_datetime(start or self.start)
        stop = to_datetime(stop or self.stop)

        step = timedelta(days=(step or 1))

        inside_if = lambda _start, _iter, _stop: _start <= _iter <= _stop
        if step.days < 0:
            start, stop = stop, start
            inside_if = lambda _start, _iter, _stop: _start >= _iter >= _stop

        iter_ = start
        new_log_content = {}
        new_log = self.copy()

        while True:
            if inside_if(start, iter_, stop):
                if iter_ in new_log.log:
                    new_log_content[iter_] = new_log.log[iter_]
            else:
                break
            iter_ += step
        new_log.__log = new_log_content
        return new_log

    def __str__(self) -> str:
        """
        If there are the same material on several days,
        add title of it in the first day and '...' to next
        ones instead of printing out the title every time.
        """
        res, is_first = '', True
        last_material_id, last_material_title = -1, ''
        new_line = '\n'

        try:
            material_titles = await database.get_material_titles()
        except database.DatabaseError as e:
            logger.error(str(e))
            raise

        for date, info in self.log.items():
            if (material_id := info.material_id) != last_material_id:
                last_material_id = material_id
                try:
                    title = (info.material_title or
                             f"«{material_titles.get(material_id, '')}»")
                except database.DatabaseError as e:
                    logger.error(str(e))
                    title = 'None'

                last_material_title = title
            else:
                last_material_title = '...'

            item = f"{new_line * (not is_first)}{fmt(date)}: " \
                   f"{info.count}, {last_material_title}"

            is_first = False
            res = f"{res}{item}"

        return res


class Tracker:
    __slots__ = '__log',

    def __init__(self,
                 log: Log) -> None:
        self.__log = log

    @property
    def queue(self) -> list[database.Material]:
        """
        Get list of uncompleted materials:
        assigned but not completed and not assigned too

        :exception DatabaseError:
        """
        try:
            return database.get_free_materials()
        except database.DatabaseError as e:
            logger.error(str(e))
            raise

    @property
    def processed(self) -> database.MATERIAL_STATUS:
        """ Get list of completed Materials.

        :exception DatabaseError:
        """
        try:
            return database.get_completed_materials()
        except database.DatabaseError as e:
            logger.error(str(e))
            raise

    @property
    def reading(self) -> database.MATERIAL_STATUS:
        """ Get reading materials and their statuses

        :exception DatabaseError:
        """
        try:
            return database.get_reading_materials()
        except database.DatabaseError as e:
            logger.error(str(e))
            raise

    @staticmethod
    def does_material_exist(material_id: int) -> bool:
        try:
            return database.does_material_exist(material_id)
        except database.DatabaseError as e:
            logger.error(f"Error checking {material_id=} exists:\n{e}")
            return False

    def get_material_statistics(self,
                                material_id: int,
                                *,
                                material: Optional[database.Material] = None,
                                status: Optional[database.Status] = None
                                ) -> MaterialStatistics:
        """ Calculate statistics for reading or completed material """
        logger.debug(f"Calculating material statistics for {material_id=}")

        material = material or self.get_material(material_id)
        status = status or self.get_status(material_id)

        assert material.material_id == status.material_id == material_id
        material_exists = material_id in self.log

        if material_exists:
            avg = self.log.m_average(material_id)
            total = self.log.m_total(material_id)
            duration = self.log.m_duration(material_id)
            max_ = self.log.m_max(material_id)
            min_ = self.log.m_min(material_id)
            lost_time = self.log.m_lost_time(material_id)
        else:
            avg = self.log.average
            total = duration = lost_time = 0
            max_ = min_ = None

        if status.end is None:
            remaining_pages = material.pages - total
            remaining_days = round(remaining_pages / avg)
            would_be_completed = database.today() + timedelta(days=remaining_days)
        else:
            would_be_completed = remaining_days = remaining_pages = None

        return MaterialStatistics(
            material=material,
            started=status.begin,
            completed=status.end,
            duration=duration,
            lost_time=lost_time,
            total=total,
            min=min_,
            max=max_,
            average=avg,
            remaining_pages=remaining_pages,
            remaining_days=remaining_days,
            would_be_completed=would_be_completed
        )

    def statistics(self,
                   materials: list[database.MaterialStatus]
                   ) -> list[MaterialStatistics]:
        return [
            self.get_material_statistics(
                ms.material.material_id, material=ms.material, status=ms.status
            )
            for ms in materials
        ]

    def estimate(self) -> list[MaterialEstimate]:
        """ Get materials from queue with estimated time to read """
        a_day = timedelta(days=1)

        # start when all reading material will be completed
        start = self._end_of_reading()
        avg = self.log.average

        last_date = start + a_day
        forecasts = []

        for material in self.queue:
            expected_duration = round(material.pages / avg)
            expected_end = last_date + timedelta(days=expected_duration)

            forecast = MaterialEstimate(
                material=material,
                will_be_started=last_date,
                will_be_completed=expected_end,
                expected_duration=expected_duration
            )
            forecasts += [forecast]

            last_date = expected_end + a_day

        return forecasts

    def _end_of_reading(self) -> datetime.date:
        """ Calculate when all reading materials will be completed """
        remaining_days = sum(
            stat.remaining_days
            for stat in self.statistics(self.reading)
        )

        return database.today() + timedelta(days=remaining_days + 1)
