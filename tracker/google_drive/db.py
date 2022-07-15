import datetime
import re
from pathlib import Path
from typing import NamedTuple

import sqlalchemy.sql as sa
import ujson
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.ddl import DropTable
from sqlalchemy.sql.schema import Table

from tracker.common import database, settings
from tracker.models import models
from tracker.common.log import logger


JSON_FIELD_TYPES = str | int
DATE_TYPES = datetime.date | datetime.datetime | str
DUMP_TYPE = dict[str, list[dict[str, JSON_FIELD_TYPES]]]

UID_REGEX = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


class TableSnapshot(NamedTuple):
    table_name: str
    rows: list[dict[str, DATE_TYPES | JSON_FIELD_TYPES]]

    @property
    def counter(self) -> int:
        return len(self.rows)


class DBSnapshot(NamedTuple):
    tables: list[TableSnapshot]

    def to_dict(self) -> dict[str, TableSnapshot]:
        return {
            table_snapshot.table_name: table_snapshot
            for table_snapshot in self.tables
        }

    def table_to_rows(self) -> dict[str, list[dict[str, DATE_TYPES | JSON_FIELD_TYPES]]]:
        return {
            table_snapshot.table_name: table_snapshot.rows
            for table_snapshot in self.tables
        }


TABLES = {
    models.Materials.name: models.Materials,
    models.Statuses.name: models.Statuses,
    models.ReadingLog.name: models.ReadingLog,
    models.Notes.name: models.Notes,
    models.Cards.name: models.Cards,
    models.Repeats.name: models.Repeats,
}


def _convert_date_to_str(value: DATE_TYPES | JSON_FIELD_TYPES) -> DATE_TYPES | JSON_FIELD_TYPES:
    if isinstance(value, datetime.date):
        return value.strftime(settings.DATE_FORMAT)
    if isinstance(value, datetime.datetime):
        return value.strftime(settings.DATETIME_FORMAT)
    return value


async def _get_table_snapshot(*,
                              table: Table,
                              conn: AsyncSession) -> TableSnapshot:
    stmt = sa.select(table)
    rows = [
        {
            str(key): _convert_date_to_str(value)
            for key, value in row.items()
        }
        for row in (await conn.execute(stmt)).mappings().all()
    ]
    return TableSnapshot(
        table_name=table.name,
        rows=rows
    )


async def get_db_snapshot() -> DBSnapshot:
    table_snapshots = []
    async with database.transaction() as ses:
        for table in TABLES.values():
            table_snapshot = await _get_table_snapshot(table=table, conn=ses)
            table_snapshots += [table_snapshot]

            logger.info("%s: %s rows got", table.name, table_snapshot.counter)

    return DBSnapshot(tables=table_snapshots)


@compiles(DropTable, "postgresql")
def _compile_drop_table(element, compiler, **kwargs):
    return compiler.visit_drop_table(element) + " CASCADE"


async def recreate_db() -> None:
    async with database.engine.begin() as conn:
        await conn.run_sync(models.metadata.drop_all)
        await conn.run_sync(models.metadata.create_all)


def _is_uid(value: str) -> bool:
    return UID_REGEX.match(value) is not None


def _contains_letter(value: str) -> bool:
    return any(
        symbol.isalpha()
        for symbol in value
    )


def _convert_str_to_date(value: JSON_FIELD_TYPES) -> JSON_FIELD_TYPES | DATE_TYPES:
    if not isinstance(value, str) or _is_uid(value) or _contains_letter(value) or not value:
        return value

    try:
        return datetime.datetime.strptime(value, settings.DATETIME_FORMAT)
    except Exception:
        pass

    try:
        return datetime.datetime.strptime(value, settings.DATE_FORMAT).date()
    except Exception:
        pass

    raise ValueError(f"Invalid date format: {value!r}")


def _read_json_file(filepath: Path) -> dict[str, list[dict[str, str | int]]]:
    assert filepath.exists(), "File not found"
    assert filepath.suffix == '.json', "File must be json"

    with filepath.open() as f:
        return ujson.load(f)


def _get_now() -> str:
    return database.utcnow().strftime(settings.DATETIME_FORMAT).replace(' ', '_')


def dump_snapshot(snapshot: DBSnapshot) -> Path:
    logger.debug("DB dumping started")

    file_path = Path("data") / f"tracker_{_get_now()}.json"

    data = snapshot.table_to_rows()
    with file_path.open('w') as f:
        ujson.dump(data, f, ensure_ascii=False, indent=2)

    logger.debug("DB dumped")

    return file_path


def _convert_dump_to_snapshot(dump_data: DUMP_TYPE) -> DBSnapshot:
    tables = []
    for table_name, values in dump_data.items():
        rows = [
            {
                key: _convert_str_to_date(value)
                for key, value in row.items()
            }
            for row in values
        ]
        tables += [
            TableSnapshot(
                table_name=table_name,
                rows=rows
            )
        ]

    return DBSnapshot(tables=tables)


async def restore_db(*,
                     dump_path: Path,
                     conn: AsyncSession) -> DBSnapshot:
    if not dump_path.exists():
        raise ValueError("Dump file not found")

    dump_data = _read_json_file(dump_path)
    snapshot = _convert_dump_to_snapshot(dump_data)
    snapshot_dict = snapshot.to_dict()

    # order of them matters
    for table_name, table in TABLES.items():
        if not (table_dict := snapshot_dict.get(table_name)) or not table_dict.rows:
            logger.warning("Table %s not found in snapshot", table_name)
            continue

        values = table_dict.rows
        stmt = table.insert().values(values)
        await conn.execute(stmt)

        logger.info("%s: %s rows inserted",
                    table.name, len(values))
    return snapshot
