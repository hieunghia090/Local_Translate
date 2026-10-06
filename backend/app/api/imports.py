import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db import get_session
from app.errors import AppError
from app.schemas import ImportPatch
from app.services import imports

router = APIRouter()


def _text(form: FormData, name: str) -> str | None:
    value = form.get(name)
    return value.strip() or None if isinstance(value, str) else None


@router.post("/imports", status_code=202)
async def post_import(request: Request, background: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    await imports.purge_expired(session)
    try:
        form = await request.form(max_files=imports.MAX_FILES + 1, max_fields=50)
    except StarletteHTTPException as e:
        raise AppError("UPLOAD_REJECTED", f"Không đọc được dữ liệu tải lên: {e.detail}", 400) from e
    try:
        uploads = [v for key in ("files[]", "files") for v in form.getlist(key) if isinstance(v, UploadFile)]
        imp = await imports.create_import(
            session,
            mode=_text(form, "mode") or "",
            split_rule=_text(form, "split_rule") or "auto",
            split_regex=_text(form, "split_regex"),
            encoding=_text(form, "encoding") or "auto",
            source_name=_text(form, "source_name"),
            uploads=uploads,
        )
    finally:
        await form.close()
    background.add_task(imports.run_analysis, imp.id, imp.analysis_token, request.app.state.translator_factory)
    return {"import_id": str(imp.id), "status": imp.status}


@router.get("/imports/{import_id}")
async def get_import(import_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return imports.to_view(await imports.get_import(session, import_id))


@router.patch("/imports/{import_id}")
async def patch_import(
    import_id: uuid.UUID,
    body: ImportPatch,
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    imp = await imports.get_import(session, import_id, lock=True)
    params = body.model_dump(include=body.model_fields_set & {"split_rule", "split_regex", "encoding"})
    chapters = [c.model_dump(exclude_unset=True) for c in body.chapters]
    view, reparse = await imports.patch_import(session, imp, params=params, chapters=chapters)
    if reparse:
        background.add_task(imports.run_analysis, imp.id, imp.analysis_token, request.app.state.translator_factory)
    return view
