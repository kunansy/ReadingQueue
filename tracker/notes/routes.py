import asyncio
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from tracker.common import settings
from tracker.common.log import logger
from tracker.models import enums
from tracker.notes import db, schemas


router = APIRouter(
    prefix="/notes",
    tags=['notes'],
    default_response_class=HTMLResponse
)
templates = Jinja2Templates(directory="templates")


@router.get('/')
async def get_notes(request: Request):
    get_notes_task = asyncio.create_task(db.get_notes())
    get_titles_task = asyncio.create_task(db.get_material_with_notes_titles())
    get_material_types_task = asyncio.create_task(db.get_material_types())

    await asyncio.gather(
        get_notes_task,
        get_titles_task,
        get_material_types_task
    )
    chapters = db.get_distinct_chapters(get_notes_task.result())

    context = {
        'request': request,
        'notes': get_notes_task.result(),
        'titles': get_titles_task.result(),
        'material_types': get_material_types_task.result(),
        'chapters': chapters,
        'DATE_FORMAT': settings.DATE_FORMAT
    }
    return templates.TemplateResponse("notes/notes.html", context)


@router.get('/material')
async def get_material_notes(request: Request,
                             material_id: UUID):
    get_notes_task = asyncio.create_task(db.get_material_notes(material_id=material_id))
    get_titles_task = asyncio.create_task(db.get_material_with_notes_titles())
    get_material_types_task = asyncio.create_task(db.get_material_types())

    await asyncio.gather(
        get_notes_task,
        get_titles_task,
        get_material_types_task
    )
    chapters = db.get_distinct_chapters(get_notes_task.result())

    context = {
        'request': request,
        'notes': get_notes_task.result(),
        'titles': get_titles_task.result(),
        'material_types': get_material_types_task.result(),
        'chapters': chapters,
        'material_id': material_id,
        'DATE_FORMAT': settings.DATE_FORMAT
    }
    return templates.TemplateResponse("notes/notes.html", context)


@router.get('/add-view')
async def add_note_view(request: Request):
    titles = await db.get_material_titles()

    context = {
        'request': request,
        'material_id': request.cookies.get('material_id', ''),
        'material_type': request.cookies.get('material_type', enums.MaterialTypesEnum.book.name),
        'content': request.cookies.get('content', ''),
        'page': request.cookies.get('page', ''),
        'chapter': request.cookies.get('chapter', ''),
        'note_id': request.cookies.get('note_id', ''),
        'titles': titles
    }
    return templates.TemplateResponse("notes/add_note.html", context)


@router.post('/add',
             response_class=RedirectResponse)
async def add_note(note: schemas.Note = Depends()):
    redirect_url = router.url_path_for(add_note_view.__name__)
    response = RedirectResponse(redirect_url, status_code=302)

    for key, value in note.dict(exclude={'content'}).items():
        response.set_cookie(key, value, expires=3600)

    note_id = await db.add_note(
        material_id=note.material_id,
        content=note.content,
        chapter=note.chapter,
        page=note.page
    )
    response.set_cookie('note_id', str(note_id), expires=5)
    if material_type := await db.get_material_type(material_id=note.material_id):
        response.set_cookie('material_type', material_type, expires=5)

    return response


@router.get('/update-view')
async def update_note_view(note_id: UUID,
                           request: Request,
                           success: bool | None = None):
    context: dict[str, Any] = {
        'request': request,
    }

    if not (note := await db.get_note(note_id=note_id)):
        context['what'] = f"Note id='{note_id}' not found"
        return templates.TemplateResponse("errors/404.html", context)

    material_type = await db.get_material_type(material_id=note.material_id) \
                    or enums.MaterialTypesEnum.book.name # noqa

    context = {
        **context,
        'material_id': note.material_id,
        'material_type': material_type,
        'note_id': note.note_id,
        'content': schemas.demark_note(note.content),
        'chapter': note.chapter,
        'page': note.page,
        'success': success
    }
    return templates.TemplateResponse("notes/update_note.html", context)


@router.post('/update',
             response_class=RedirectResponse)
async def update_note(note: schemas.UpdateNote = Depends()):
    success = True
    try:
        await db.update_note(
            note_id=note.note_id,
            content=note.content,
            chapter=note.chapter,
            page=note.page
        )
    except Exception as e:
        logger.error("Error updating note: %s", repr(e))
        success = False

    redirect_path = router.url_path_for(update_note_view.__name__)
    redirect_url = f"{redirect_path}?note_id={note.note_id}&{success=}"

    return RedirectResponse(redirect_url, status_code=302)


@router.post('/delete',
             response_class=RedirectResponse)
async def delete_note(note: schemas.DeleteNote = Depends()):
    await db.delete_note(note_id=note.note_id)

    redirect_path = router.url_path_for(get_material_notes.__name__)
    redirect_url = f"{redirect_path}?material_id={note.material_id}"

    return RedirectResponse(redirect_url, status_code=302)
