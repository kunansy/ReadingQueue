import base64
import datetime
from io import BytesIO
from typing import NamedTuple
from uuid import UUID

import matplotlib.pyplot as plt
import sqlalchemy.sql as sa

from tracker.common import database, settings
from tracker.materials import db as materials_db
from tracker.models import models
from tracker.reading_log import (
    db as logs_db,
    statistics,
)


class ReadingData(NamedTuple):
    counts: list[int]
    dates: list[datetime.date]


async def _get_graphic_data(*, material_id: UUID, last_days: int) -> ReadingData:
    dates, counts, total = [], [], 0

    async for date, info in logs_db.data():
        if info.material_id != material_id:
            continue
        total += info.count

        counts.append(total)
        dates.append(date)

    return ReadingData(counts[-last_days:], dates[-last_days:])


async def get_read_material_titles() -> dict[UUID, str]:
    stmt = sa.select(models.Materials.c.material_id, models.Materials.c.title).join(
        models.Statuses,
    )

    async with database.session() as ses:
        return {  # noqa: C416
            material_id: title for material_id, title in (await ses.execute(stmt)).all()
        }


def _set_plot_style() -> None:
    if datetime.datetime.now(tz=datetime.UTC).time() >= settings.EX_DARKMODE_ENABLE:
        plt.style.use("dark_background")
    else:
        plt.style.use("default")


async def create_reading_graphic(*, material_id: UUID, last_days: int) -> str:
    if not (material := await materials_db.get_material(material_id=material_id)):
        raise ValueError(f"'{material_id=}' not found")

    data = await _get_graphic_data(material_id=material_id, last_days=last_days)
    total_pages_read = data.counts[-1]
    material_pages = material.pages

    _set_plot_style()
    fig, ax = plt.subplots(figsize=(12, 10))

    line = plt.axhline(y=material.pages, color="r", linestyle="-")
    line.set_label(f"Overall {material.pages} items")

    bar = ax.bar(data.dates, data.counts, width=1, edgecolor="white")  # type: ignore[arg-type]
    ax.bar_label(bar)

    ax.set_title("Total items completed")
    ax.set_ylabel("Items count")
    ax.set_xlabel("Date")

    if (remains := material_pages - total_pages_read) > 0:
        last_rect = bar[-1]
        vline_x = last_rect.get_x() + last_rect.get_width() / 2

        ax.vlines(
            vline_x,
            ymin=last_rect.get_height(),
            ymax=material.pages,
            color="black",
            linestyles="solid",
            label=f"{remains} items remains",
        )

    ax.set(ylim=(0, material_pages + material_pages * 0.1))
    ax.legend()

    buff = BytesIO()
    fig.savefig(buff, format="svg")
    return base64.b64encode(buff.getvalue()).decode("utf-8")


get_material_reading_now = logs_db.get_material_reading_now
get_tracker_statistics = statistics.get_tracker_statistics
get_completion_dates = logs_db.get_completion_dates
