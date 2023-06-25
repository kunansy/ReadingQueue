from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from tracker.common import settings
from tracker.common.logger import logger
from tracker.materials import db, schemas
from tracker.models import enums


router = APIRouter(
    prefix="/materials",
    tags=["materials"]
)
# could not specify the directory as 'templates/materials',
# because templates contains references to the root templates folder
templates = Jinja2Templates(directory="templates")


@router.get('/', response_class=RedirectResponse)
async def root():
    redirect_url = router.url_path_for(get_reading_materials.__name__)
    return RedirectResponse(redirect_url, status_code=302)


@router.get('/queue', response_class=HTMLResponse)
async def get_queue(request: Request):
    estimates = await db.estimate()
    mean = await db.get_means()

    context = {
        'request': request,
        'estimates': estimates,
        'mean': mean,
        'DATE_FORMAT': settings.DATE_FORMAT
    }
    return templates.TemplateResponse("materials/queue.html", context)


@router.get('/add-view', response_class=HTMLResponse)
async def insert_material_view(request: Request):
    """ Insert a material to the queue """
    tags = await db.get_material_tags()
    context = {
        'request': request,
        'tags_list': tags,
        'material_types': enums.MaterialTypesEnum
    }
    return templates.TemplateResponse("materials/add_material.html", context)


@router.post('/add', response_class=HTMLResponse)
async def insert_material(material: schemas.Material = Depends()):
    """ Insert a material to the queue """
    await db.insert_material(
        title=material.title,
        authors=material.authors,
        pages=material.pages,
        material_type=material.material_type,
        tags=material.tags,
        link=material.link
    )

    redirect_url = router.url_path_for(insert_material_view.__name__)
    return RedirectResponse(redirect_url, status_code=302)


@router.get('/update-view', response_class=HTMLResponse)
async def update_material_view(request: Request,
                               material_id: UUID,
                               success: bool | None = None):
    context: dict[str, Any] = {
        'request': request,
    }

    if not (material := await db.get_material(material_id=material_id)):
        context['what'] = f"'{material_id=}' not found"
        return templates.TemplateResponse("errors/404.html", context)

    tags = await db.get_material_tags()
    context = {
        **context,
        'material_id': material_id,
        'title': material.title,
        'authors': material.authors,
        'pages': material.pages,
        'material_type': material.material_type.name,
        'success': success,
        'material_types': enums.MaterialTypesEnum,
        'tags_list': tags,
    }
    if material.link:
        context['link'] = material.link
    if material.tags:
        context['tags'] = material.tags

    return templates.TemplateResponse("materials/update_material.html", context)


@router.post('/update',
             response_class=RedirectResponse)
async def update_material(material: schemas.UpdateMaterial = Depends()):
    success = True
    try:
        await db.update_material(
            material_id=material.material_id,
            title=material.title,
            authors=material.authors,
            pages=material.pages,
            material_type=material.material_type,
            tags=material.tags,
            link=material.link
        )
    except Exception as e:
        logger.error("Error updating material_id='%s': %s",
                     material.material_id, repr(e))
        success = False

    redirect_path = router.url_path_for(update_material_view.__name__)
    redirect_url = f"{redirect_path}?material_id={material.material_id}&{success=}"

    return RedirectResponse(redirect_url, status_code=302)


@router.post('/start/{material_id}')
async def start_material(material_id: UUID):
    if not (material := await db.get_material(material_id=material_id)):
        raise HTTPException(status_code=404, detail=f"Material {material_id} not found")
    # don't shift queue if the material is the first item
    if material.index != (queue_start := await db.get_queue_start()):
        await db.swap_order(material_id, queue_start)

    await db.start_material(material_id=material_id)

    redirect_url = router.url_path_for(get_queue.__name__)
    return RedirectResponse(redirect_url, status_code=302)


@router.post('/complete/{material_id}')
async def complete_material(material_id: UUID):
    await db.complete_material(material_id=material_id)

    redirect_url = router.url_path_for(get_reading_materials.__name__)
    return RedirectResponse(redirect_url, status_code=302)


@router.post('/outline/{material_id}')
async def outline_material(material_id: UUID):
    """ Mark the material as outlined """
    await db.outline_material(material_id=material_id)

    redirect_url = router.url_path_for(get_reading_materials.__name__)
    return RedirectResponse(redirect_url, status_code=302)


@router.post('/repeat/{material_id}')
async def repeat_material(material_id: UUID):
    await db.repeat_material(material_id=material_id)

    redirect_url = router.url_path_for(get_repeating_queue.__name__)
    return RedirectResponse(redirect_url, status_code=302)


@router.get('/reading', response_class=HTMLResponse)
async def get_reading_materials(request: Request):
    statistics = await db.reading_statistics()

    context = {
        'request': request,
        'statistics': statistics,
        'DATE_FORMAT': settings.DATE_FORMAT
    }
    return templates.TemplateResponse("materials/reading.html", context)


@router.get('/completed', response_class=HTMLResponse)
async def get_completed_materials(request: Request):
    statistics = await db.completed_statistics()

    context = {
        'request': request,
        'statistics': statistics,
        'DATE_FORMAT': settings.DATE_FORMAT
    }
    return templates.TemplateResponse("materials/completed.html", context)


@router.get('/repeat-view', response_class=HTMLResponse)
async def get_repeating_queue(request: Request):
    repeating_queue = await db.get_repeating_queue()

    context = {
        'request': request,
        'repeating_queue': repeating_queue,
        'DATE_FORMAT': settings.DATE_FORMAT
    }
    return templates.TemplateResponse("materials/repeat.html", context)


@router.get('/queue/start')
async def get_queue_start():
    """ Get the first material index in the queue """
    index = await db.get_queue_start()

    return {
        "index": index
    }


@router.get('/queue/end')
async def get_queue_end():
    """ Get the last material index in the queue """
    index = await db.get_queue_end()

    return {
        "index": index
    }


@router.post('/queue/swap-order', response_class=RedirectResponse)
async def update_queue_order(material_id: UUID, index: int):
    await db.swap_order(material_id, index)

    redirect_url = router.url_path_for(get_queue.__name__)
    return RedirectResponse(redirect_url, status_code=302)


@router.get('/is-reading')
async def is_material_reading(material_id: UUID):
    is_reading = await db.is_reading(material_id=material_id)

    return {
        "is_reading": is_reading
    }
