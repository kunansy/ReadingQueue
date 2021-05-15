#!/usr/bin/env python3
import copy
import datetime
import logging
import random
import statistics
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Union, Optional, Iterator, Iterable, Any

import ujson
from environs import Env

import src.db_api as db


env = Env()

DATA_FOLDER = Path('data')
PAGES_PER_DAY = env.int('READ_PER_DAY', 50)
INDENT = 2

DATE_FORMAT = '%d-%m-%Y'
# max count of cards repeated per day
_MAX_PER_DAY = env.int('CARDS_PER_DAY', 25)


logger = logging.getLogger(env('LOGGER_NAME'))


class BaseTrackerError(Exception):
    def __repr__(self) -> str:
        args = '; '.join(
            str(arg)
            for arg in self.args
        )
        return f"{self.__class__.__name__}(" \
               f"{args}, {self.__cause__})"

    def __str__(self) -> str:
        return repr(self)


class LoadingLogError(BaseTrackerError):
    pass


class ReadingLogIsEmpty(BaseTrackerError):
    pass


class WrongLogParam(BaseTrackerError):
    pass


class NoMaterialInLog(BaseTrackerError):
    pass


class CardNotFound(BaseTrackerError):
    pass


class CardsLimitExceeded(BaseTrackerError):
    pass


class MaterialNotFound(BaseTrackerError):
    pass


class DatabaseError(BaseTrackerError):
    pass


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


@dataclass(unsafe_hash=True)
class LogRecord:
    count: int
    material_id: int
    material_title: Optional[str] = None

    def dict(self) -> dict:
        return {
            field: getattr(self, field, None)
            for field in self.__annotations__.keys()
        }

    def __str__(self) -> str:
        if title := self.material_title:
            title = f"\nTitle: «{title}»"
        else:
            title = ''

        return f"Count: {self.count}\n" \
               f"Material id: {self.material_id}" \
               f"{title}"

    def __setattr__(self,
                    key: str,
                    value) -> None:
        if getattr(self, key, None) is not None:
            raise NotImplementedError(
                f"You can't change {self.__class__.__name__} values, but "
                f"{key}={value} found, when {key}={getattr(self, key)}"
            )

        super().__setattr__(key, value)


@dataclass(frozen=True)
class LogStatistics:
    start_date: datetime.date
    stop_date: datetime.date
    duration: int
    # amount of empty days
    lost_time: int
    average: int
    total_pages_read: int
    # would be total if there were no empty days
    would_be_total: int
    min: MinMax
    max: MinMax
    median: int

    def dict(self) -> dict:
        return {
            field: getattr(self, field, None)
            for field in self.__annotations__.keys()
        }

    def __str__(self) -> str:
        min_ = '\n'.join(
            f"\t{field.capitalize()}: {value}"
            for field, value in self.min.dict(exclude={'material_id'}).items()
        )

        max_ = '\n'.join(
            f"\t{field.capitalize()}: {value}"
            for field, value in self.max.dict(exclude={'material_id'}).items()
        )

        return f"Start: {fmt(self.start_date)}\n" \
               f"Stop: {fmt(self.stop_date)}\n" \
               f"Duration: {time_span(self.duration)}\n" \
               f"Lost time: {time_span(self.lost_time)}\n" \
               f"Average: {self.average} pages per day\n" \
               f"Total pages read: {self.total_pages_read}\n" \
               f"Would be total: {self.would_be_total}\n" \
               f"Min:\n{min_}\n" \
               f"Max:\n{max_}\n" \
               f"Median: {self.median} pages"


@dataclass(frozen=True)
class MaterialStatistics:
    material: db.Material
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
    material: db.Material
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


@db.cache(update=False)
def today() -> datetime.date:
    return datetime.date.today()


@db.cache(update=False)
def yesterday() -> datetime.date:
    return today() - timedelta(days=1)


@db.cache(update=False)
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
            date = datetime.datetime.strptime(date, DATE_FORMAT)
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


@db.cache(update=False)
def time_span(span: Union[timedelta, int]) -> str:
    if isinstance(span, timedelta):
        days = timedelta.days
    else:
        days = span

    res = ''
    if years := days // 365:
        res += f"{years} years, "
    if month := days % 365 // 30:
        res += f"{month} months, "
    if days := days % 365 % 30:
        res += f"{days} days"

    return res


@db.cache(update=False)
def fmt(date: datetime.date) -> str:
    return date.strftime(DATE_FORMAT)


def safe_list_get(list_: list[Any],
                  index: int,
                  default: Any = None) -> Any:
    try:
        return list_[index]
    except IndexError:
        return default


class Log:
    __slots__ = '__log'

    LOG_PATH = DATA_FOLDER / 'log.json'

    def __init__(self,
                 *,
                 full_info: bool = False) -> None:
        """
        :param full_info: if True get titles to all materials.
         !!! there will be more queries to the database !!!
        """
        try:
            log = self._get_log(full_info=full_info)
        except Exception as e:
            logger.error(f"When load the log: {e}")
            log = {}
        self.__log = log

    @property
    def log(self) -> dict[datetime.date, LogRecord]:
        return self.__log

    @property
    def path(self) -> Path:
        return self.LOG_PATH

    @property
    def start(self) -> datetime.date:
        """ Get the date of the first logged day.

        :exception ReadingLogIsEmpty:
        """
        try:
            return list(self.log.keys())[0]
        except IndexError:
            msg = "Reading log is empty, no start date"
            logger.warning(msg)
            raise ReadingLogIsEmpty(msg)

    @property
    def stop(self) -> datetime.date:
        """ Get the date of the last logged day.

        :exception ReadingLogIsEmpty:
        """
        try:
            return list(self.log.keys())[-1]
        except IndexError:
            msg = "Reading log is empty, no stop date"
            logger.warning(msg)
            raise ReadingLogIsEmpty(msg)

    @property
    def reading_material(self) -> int:
        """ Get id of the reading material.

        :exception ReadingLogIsEmpty:
        """
        if not self.log:
            msg = "Reading log is empty, no materials reading"
            logger.warning(msg)
            raise ReadingLogIsEmpty(msg)

        last = 0
        for _, info in self.data():
            last = info.material_id

        return last

    def _get_log(self,
                 *,
                 full_info: bool = False) -> dict[datetime.date, LogRecord]:
        """
        Get log from JSON file and parse it.
        Convert keys to datetime.date, values to LogRecord.

        :param full_info: if True get titles to all materials.
         !!! there will be more queries to the database !!!

        :return: dict with the format.

        :exception DatabaseError:
        """
        with self.path.open(encoding='utf-8') as f:
            log = ujson.load(f)

        log_records = {}
        material_titles = {}

        if full_info:
            try:
                material_titles = db.get_material_titles(
                    reading=True, completed=True)
            except db.BaseDBError as e:
                logger.error(str(e))
                raise DatabaseError(e)

        for date, info in log.items():
            date = to_datetime(date)
            record = LogRecord(**info)

            if full_info:
                record.material_title = \
                    material_titles.get(record.material_id, '')

            log_records[date] = record
        return log_records

    def _set_log(self,
                 date: datetime.date,
                 count: int,
                 material_id: Optional[int] = None) -> None:
        """
        Set reading log for the day.
        Dump changes to the file.

        :param material_id: id of the learned material,
         by default id of the last material.

        :exception WrongLogParam: if count <= 0, the date
         is more than today, the date even exists in
         log, 'material_id' not given and log is empty.
        :exception DatabaseError:
        """
        logger.debug(f"Setting log for: {date=}, {count=}, {material_id=}")

        if count <= 0:
            raise WrongLogParam(f"Count must be > 0, but 0 <= {count}")
        if date <= self.stop:
            raise WrongLogParam(
                "The date must be less than the last "
                f"date, but {date=} < {self.stop=}"
            )
        if date in self.__log:
            raise WrongLogParam(f"The {date=} even exists in the log")
        if material_id is None and len(self.log) == 0:
            raise WrongLogParam(f"{material_id=} and log dict is empty")

        try:
            material_title = db.get_title(material_id)
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError(e)

        record = LogRecord(
            material_id=material_id or self.reading_material,
            count=count,
            material_title=material_title
        )
        self.__log[date] = record

        self.__log = dict(sorted(self.log.items(), key=lambda i: i[0]))

        self.dump()

    def set_today_log(self,
                      count: int,
                      material_id: Optional[int] = None) -> None:
        """
        Set today's reading log.

        :param count: count of pages read today.
        :param material_id: id of learned material.
         The last learned material_id by default.

        :exception WrongLogParam: if count <= 0, the date
         is more than today, the date even exists in
         log, 'material_id' not given and log is empty.
        """
        try:
            self._set_log(today(), count, material_id)
        except WrongLogParam as e:
            logger.error(str(e))
            raise

    def set_yesterday_log(self,
                          count: int,
                          material_id: Optional[int] = None) -> None:
        """
        Set yesterday's reading log.

        :param count: count of pages read yesterday.
        :param material_id: id of learned material.
         The last learned material_id by default.

        :exception WrongLogParam: if count <= 0, the date
         is more than today, the date even exists in
         log, 'material_id' not given and log is empty.
        """
        try:
            self._set_log(yesterday(), count, material_id)
        except WrongLogParam as e:
            logger.error(str(e))
            raise

    def dump(self) -> None:
        """ Dump log to the file. """
        logger.debug("Dumping log")

        data = {
            fmt(date): {
                'material_id': info.material_id,
                'count': info.count
            }
            for date, info in self.log.items()
        }

        with self.path.open('w', encoding='utf-8') as f:
            ujson.dump(data, f, indent=INDENT)

        logger.debug("Log dumped")

    @property
    def total(self) -> int:
        """ Get total count of read pages """
        logger.debug("Calculating total count of read pages in log")

        return sum(
            info.count
            for info in self.log.values()
        )

    @property
    def duration(self) -> int:
        """ Get duration of log """
        logger.debug("Calculating log duration")

        if not self.log:
            return 0

        return (self.stop - self.start).days + 1

    @property
    def lost_time(self) -> int:
        logger.debug("Calculating empty days count for log")
        return self.duration - len(self.log)

    @property
    def average(self) -> int:
        """ get the average count of pages read per day """
        logger.debug("Calculating average count of read pages in log")
        try:
            return self.total // self.duration
        except ZeroDivisionError:
            return 1

    @property
    def min(self) -> MinMax:
        """ Get info of the record with
        the min number of read pages.

        :return: MinMax obj.
        :exception ReadingLogIsEmpty:
        """
        logger.debug("Calculating min for log")

        if not self.log:
            raise ReadingLogIsEmpty

        date, info = min(
            [(date, info) for date, info in self.log.items()],
            key=lambda item: item[1].count
        )
        return MinMax(
            date=date,
            **info.dict()
        )

    @property
    def max(self) -> MinMax:
        """ Get info of the record with
        the max number of read pages.

        :return: MinMax obj.
        :exception ReadingLogIsEmpty:
        """
        logger.debug("Calculating max for log")

        if not self.log:
            raise ReadingLogIsEmpty

        date, info = max(
            [(date, info) for date, info in self.log.items()],
            key=lambda item: item[1].count
        )

        return MinMax(
            date=date,
            **info.dict()
        )

    @property
    def median(self) -> int:
        logger.debug("Calculating median for log")

        if not self.log:
            return 0

        return statistics.median(
            info.count
            for info in self.log.values()
        )

    @property
    def would_be_total(self) -> int:
        """
        Get count of pages would be total
        if there were no empty days.
        """
        logger.debug("Calculating ... for log")
        return self.total + self.average * self.lost_time

    def data(self) -> Iterator[tuple[datetime.date, LogRecord]]:
        """ Get pairs: date, info of all days from start to stop.
        The function is expected to make graphics.

        If the day is empty, material_id is supposed
        as the material_id of the last not empty day.
        """
        logger.debug("Getting data from log")

        if not self.log:
            return

        step = timedelta(days=1)
        iter_ = self.start

        # stack for materials
        materials = []
        try:
            completion_dates = db.get_completion_dates()
        except db.BaseDBError as e:
            logger.error(str(e))
            completion_dates = {}

        while iter_ <= today():
            last_material_id = safe_list_get(materials, -1, 0)

            if ((completion_date := completion_dates.get(last_material_id)) and
                    completion_date <= iter_):
                materials.pop()
                last_material_id = safe_list_get(materials, -1, 0)

            if not (info := self.log.get(iter_)):
                info = LogRecord(material_id=last_material_id, count=0)
            else:
                material_id = info.material_id

                if not (materials and material_id in materials):
                    # new material started, the last one completed
                    materials += [material_id]
                elif material_id != last_material_id:
                    # in this case several materials
                    # are being reading one by one
                    materials.remove(material_id)
                    materials += [material_id]

            yield iter_, info
            iter_ += step

    def m_duration(self,
                   material_id: int) -> int:
        """ Calculate how many days the material was being reading.

        :exception NoMaterialInLog:
        """
        logger.debug(f"Calculating duration for material {material_id=}")

        if material_id not in self:
            raise NoMaterialInLog

        return sum(
            1
            for _, info in self.data()
            if info.material_id == material_id
        )

    def m_total(self,
                material_id: int) -> int:
        """ Calculate how many pages of the material even read.

        :exception NoMaterialInLog:
        """
        logger.debug(f"Calculating total for material {material_id=}")

        if material_id not in self:
            raise NoMaterialInLog

        return sum(
            info.count
            for info in self.log.values()
            if info.material_id == material_id
        )

    def m_lost_time(self,
                    material_id: int) -> int:
        """ How many days was lost reading the material.

        :exception NoMaterialInLog:
        """
        logger.debug(f"Calculating lost time for material {material_id=}")

        if material_id not in self:
            raise NoMaterialInLog

        return sum(
            1
            for _, info in self.data()
            if info.material_id == material_id and info.count == 0
        )

    def m_min(self,
              material_id: int) -> MinMax:
        """ Get info of the record with
        the min number of read pages of the material.

        :exception NoMaterialInLog:
        """
        logger.debug(f"Calculating min for material {material_id=}")

        if material_id not in self:
            raise NoMaterialInLog

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
            raise NoMaterialInLog

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

    def m_average(self,
                  material_id: int) -> int:
        """
        :exception NoMaterialInLog:
        """
        logger.debug(f"Calculating average for material {material_id=}")

        if material_id not in self:
            raise NoMaterialInLog

        total = duration = 0
        for date, info in self.data():
            if info.material_id == material_id:
                total += info.count
                duration += 1

        try:
            return total // duration
        except ZeroDivisionError:
            return 0

    def dates(self) -> list[datetime.date]:
        return [
            date
            for date, _ in self.data()
        ]

    def counts(self) -> list[int]:
        return [
            info.count
            for _, info in self.data()
        ]

    def copy(self):
        return copy.deepcopy(self)

    @property
    def statistics(self) -> LogStatistics:
        """
        :exception ReadingLogIsEmpty:
        """
        logger.debug("Calculating statistics of the log")

        if not self.log:
            raise ReadingLogIsEmpty

        return LogStatistics(
            start_date=self.start,
            stop_date=self.stop,
            duration=self.duration,
            lost_time=self.lost_time,
            average=self.average,
            total_pages_read=self.total,
            would_be_total=self.would_be_total,
            min=self.min,
            max=self.max,
            median=self.median
        )

    def __getitem__(self,
                    date: Union[datetime.date, str, slice]):
        """
        Get log record by date (datetime.date or str)
        of by slice of dates.

        If slice get new Log object with [start; stop).

        :exception ReadingLogIsEmpty:
        """
        logger.debug(f"Getting item {date=} from the log")

        if not self.log:
            raise ReadingLogIsEmpty

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

    def __len__(self) -> int:
        return len(self.log)

    def __contains__(self,
                     material_id: int) -> bool:
        logger.debug(f"Whether {material_id=} is in a log record")
        if not self.log:
            return False

        return any(
            info.material_id == material_id
            for info in self.log.values()
        )

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
            material_titles = db.get_material_titles()
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError(e)

        for date, info in self.log.items():
            if (material_id := info.material_id) != last_material_id:
                last_material_id = material_id
                try:
                    title = info.material_title or \
                            f"«{material_titles.get(material_id, '')}»"
                except db.BaseDBError as e:
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

    def __repr__(self) -> str:
        log_records = ', '.join(
            f"{date}: {info}"
            for date, info in self.log.items()
        )
        return f"{self.__class__.__name__}({log_records})"


class Tracker:
    __slots__ = '__log',

    def __init__(self,
                 log: Log) -> None:
        self.__log = log

    @property
    def queue(self) -> list[db.Material]:
        """
        Get list of uncompleted materials:
        assigned but not completed and not assigned too

        :exception DatabaseError:
        """
        try:
            return db.get_free_materials()
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError(e)

    @property
    def processed(self) -> db.MATERIAL_STATUS:
        """ Get list of completed Materials.

        :exception DatabaseError:
        """
        try:
            return db.get_completed_materials()
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError(e)

    @property
    def reading(self) -> db.MATERIAL_STATUS:
        """ Get reading materials and their statuses

        :exception DatabaseError:
        """
        try:
            return db.get_reading_materials()
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError(e)

    @property
    def log(self) -> Log:
        return self.__log

    @property
    def notes(self) -> list[db.Note]:
        """
        :exception DatabaseError:
        """
        try:
            return db.get_notes()
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError(e)

    @staticmethod
    def does_material_exist(material_id: int) -> bool:
        try:
            return db.does_material_exist(material_id)
        except db.BaseDBError as e:
            logger.error(f"Error checking {material_id=} exists:\n{e}")
            return False

    @staticmethod
    def get_material_titles(**kwargs) -> dict[int, str]:
        """
        :exception DatabaseError:
        """
        try:
            return db.get_material_titles(**kwargs)
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError(e)

    def get_material_statistics(self,
                                material_id: int,
                                *,
                                material: Optional[db.Material] = None,
                                status: Optional[db.Status] = None) -> MaterialStatistics:
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
            would_be_completed = today() + timedelta(days=remaining_days)
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
                   materials: list[db.MaterialStatus]) -> list[MaterialStatistics]:
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

        return today() + timedelta(days=remaining_days + 1)

    @staticmethod
    def start_material(material_id: int,
                       start_date: Optional[datetime.date] = None) -> None:
        """ Create item in Status table.

        :param material_id: material to start.
        :param start_date: date when the material was started.
         Today by default.

        :exception DatabaseError:
        :exception MaterialNotFound:
        """
        try:
            db.start_material(
                material_id=material_id,
                start_date=start_date
            )
        except db.MaterialNotFound as e:
            logger.error(str(e))
            raise MaterialNotFound(e)
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError(e)

    def complete_material(self,
                          material_id: Optional[int] = None,
                          completion_date: Optional[datetime.date] = None) -> None:
        """
        Complete a material, set 'end' in its status.

        :param material_id: id of completed material,
         the material reading now by default.
        :param completion_date: date when the material was completed.
         Today by default.

        :exception DatabaseError:
        """
        material_id = material_id or self.log.reading_material

        try:
            db.complete_material(
                material_id=material_id,
                completion_date=completion_date
            )
        except db.MaterialNotAssigned as e:
            logger.error(str(e))
            raise MaterialNotFound(e)
        except db.BaseDBError as e:
            logger.error(e)
            raise DatabaseError(e)

    @staticmethod
    def add_material(title: str,
                     authors: str,
                     pages: int,
                     tags: str) -> None:
        """
        :exception DatabaseError:
        """
        try:
            db.add_material(
                title=title,
                authors=authors,
                pages=pages,
                tags=tags
            )
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError

    @staticmethod
    def get_material(material_id: int) -> db.Material:
        """
        :exception DatabaseError:
        """
        logger.debug(f"Getting material {material_id=}")

        try:
            return db.get_materials(materials_ids=[material_id])[0]
        except IndexError:
            msg = f"Material {material_id=} not found"
            logger.warning(msg)
            raise DatabaseError(msg)
        except db.BaseDBError as e:
            logger.error(e)
            raise DatabaseError(e)

    @staticmethod
    def get_status(material_id: int) -> db.Status:
        """
        :exception DatabaseError:
        """
        try:
            return db.get_material_status(material_id=material_id)
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError(e)

    @staticmethod
    def get_notes(material_id: Optional[int] = None) -> list[db.Note]:
        """
        :param material_id: get notes for this material.
         By default, get all notes.

        :exception DatabaseError:
        """
        material_ids = [material_id] * (material_id is not None)
        try:
            return db.get_notes(materials_ids=material_ids)
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError(e)

    @staticmethod
    def add_note(material_id: int,
                 content: str,
                 chapter: int,
                 page: int,
                 date: Optional[datetime.date] = None) -> None:
        """
        Here it is expected that all fields are valid.

        :exception DatabaseError:
        :exception ValueError: if the given page number is better
         than page count in the material.
        """
        material = Tracker.get_material(material_id)

        if material.pages < page:
            msg = f"Given page number is better than overall pages count " \
                  f"in the material, {page=} > {material.pages=}"
            logger.warning(msg)
            raise ValueError(msg)

        try:
            db.add_note(
                material_id=material_id,
                content=content,
                chapter=chapter,
                page=page,
                date=date
            )
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError(e)


class Cards:
    def __init__(self) -> None:
        self.__cards = self._get_cards()
        self.__current_card = None

    @property
    def card(self) -> Optional[db.CardNoteRecall]:
        """
        :exception CardsLimitExceeded:
        """
        if self.repeated_today() >= _MAX_PER_DAY:
            raise CardsLimitExceeded

        if self.__current_card is None:
            if not (cards := self._get_cards()):
                return
            self.__cards = cards
            self.__current_card = self.__cards.pop()

        return self.__current_card

    @staticmethod
    def _get_cards() -> list[db.CardNoteRecall]:
        try:
            cards = db.get_cards()
        except db.BaseDBError as e:
            logger.error(str(e))
            cards = []

        random.shuffle(cards)
        return cards

    @staticmethod
    def add_card(material_id: int,
                 question: str,
                 note_id: int,
                 answer: Optional[str] = None) -> None:
        """
        :exception DatabaseError:
        """
        try:
            db.add_card(
                material_id=material_id,
                question=question,
                answer=answer,
                note_id=note_id
            )
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError(e)

    def complete_card(self,
                      result: str) -> None:
        """
        :exception DatabaseError:
        :exception CardNotFound:
        """
        if self.__current_card is None:
            raise CardNotFound

        card_id = self.__current_card.card.card_id

        try:
            db.complete_card(
                card_id=card_id, result=result
            )
        except db.CardNotFound as e:
            logger.error(str(e))
            raise CardNotFound(e)
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError(e)
        else:
            self.__current_card = None

    @staticmethod
    def cards_remain() -> int:
        try:
            repeated_today = db.repeated_today()
            remains_for_today = db.remains_for_today()
        except db.BaseDBError as e:
            logger.error(str(e))
            return 0

        if repeated_today >= _MAX_PER_DAY:
            return 0
        if repeated_today + remains_for_today >= _MAX_PER_DAY:
            return _MAX_PER_DAY - repeated_today
        return remains_for_today

    @staticmethod
    def repeated_today() -> int:
        try:
            return db.repeated_today()
        except db.BaseDBError as e:
            logger.error(str(e))
            return _MAX_PER_DAY

    @staticmethod
    def notes_with_cards(material_id: Optional[int] = None) -> set[int]:
        """
        :exception DatabaseError:
        """
        material_ids = [material_id] * (material_id is not None)
        try:
            return db.notes_with_cards(material_ids=material_ids)
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError(e)

    @staticmethod
    def list(material_id: Optional[int] = None) -> list[db.CardNoteRecall]:
        material_ids = [material_id] * (material_id is not None)
        try:
            return db.all_cards(material_ids=material_ids)
        except db.BaseDBError as e:
            logger.error(str(e))
            raise DatabaseError(e)

    def __len__(self) -> int:
        try:
            return db.cards_count()
        except db.BaseDBError as e:
            logger.error(str(e))
            return 0
