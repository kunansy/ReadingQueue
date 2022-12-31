import asyncio
import datetime
from contextlib import asynccontextmanager
from typing import NamedTuple, AsyncGenerator
from uuid import UUID

import aiomysql
import sqlalchemy.sql as sa
from aiomysql.cursors import Cursor as MysqlCursor

from tracker.common import database, settings
from tracker.common.log import logger
from tracker.models import models


class ManticoreException(Exception):
    pass


class Note(NamedTuple):
    note_id: str
    material_id: str
    content: str
    added_at: datetime.datetime
    chapter: int
    page: int

    material_title: str
    material_authors: str
    material_type: str
    material_tags: str | None
    material_link: str | None


@asynccontextmanager
async def _cursor() -> AsyncGenerator[MysqlCursor, None]:
    new_session = await aiomysql.connect(
        host=settings.MANTICORE_MYSQL_HOST,
        port=settings.MANTICORE_MYSQL_PORT,
        db=settings.MANTICORE_MYSQL_DB_NAME
    )

    try:
        async with new_session.cursor() as cur:
            yield cur
        await new_session.commit()
    except Exception as e:
        logger.exception("Manticore mysql error")

        await new_session.rollback()
        raise ManticoreException(e) from e
    finally:
        new_session.close()


def _get_note_stmt() -> sa.Select:
    return sa.select([models.Notes.c.note_id,
                      models.Notes.c.material_id,
                      models.Notes.c.content,
                      models.Notes.c.chapter,
                      models.Notes.c.page,
                      models.Notes.c.added_at,
                      models.Materials.c.title.label('material_title'),
                      models.Materials.c.authors.label('material_authors'),
                      models.Materials.c.material_type.label('material_type'),
                      models.Materials.c.tags.label('material_tags'),
                      models.Materials.c.link.label('material_link')]) \
        .join(models.Materials,
              models.Materials.c.material_id == models.Notes.c.material_id)


async def _get_notes() -> list[Note]:
    stmt = _get_note_stmt()

    async with database.session() as ses:
        return [
            Note(**row)
            for row in await (ses.execute(stmt)).mappings().all()
        ]


async def _get_note(*,
                    note_id: UUID) -> Note:
    stmt = _get_note_stmt() \
        .where(models.Notes.c.note_id == str(note_id))

    async with database.session() as ses:
        if note := (await ses.execute(stmt)).one_or_none():
            return Note(**note)
    raise ValueError(f'Note {note_id} not found')


async def _drop_table() -> None:
    query = "DROP TABLE IF EXISTS notes"

    async with _cursor() as cur:
        await cur.execute(query)


async def _create_table() -> None:
    query = """CREATE TABLE IF NOT EXISTS notes (
        note_id string,
        material_id string,
        content string, 
        chapter int,
        page int,
        added_at timestamp, 
        material_title string,
        material_authors string,
        material_type string,
        material_tags string,
        material_link string) morphology='lemmatize_ru_all, lemmatize_en_all'
    """

    async with _cursor() as cur:
        await cur.execute(query)


async def _fill_table(notes: list[Note]) -> None:
    pass


async def init() -> None:
    logger.info("Init manticore search")

    logger.debug("Recreate tables")
    await _drop_table()
    await _create_table()
    logger.debug("Tables recreated")

    logger.debug("Getting notes")
    notes = await _get_notes()
    logger.debug("%s notes got, inserting", len(notes))
    await _fill_table(notes)
    logger.debug("Notes inserted")

    logger.info("Manticore search init completed")
