from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import JSONResponse

from app.db.files import list_files
from app.services.ingestion import persist_upload
from app.services.windows import any_window_has_active_run


router = APIRouter()


@router.post("/files")
async def upload_file(file: UploadFile = File(...)):
    """Accept a manually uploaded data CSV.

    The file is validated and parsed at the boundary; unparseable content is
    rejected (nothing written to the data folder). Parseable files are placed
    under a recognized dataset prefix and become visible to the next cycle.

    Rejected if any Campaign Run is currently running (to protect frozen Run targets).
    """
    if any_window_has_active_run():
        return JSONResponse(
            {
                "message": "Upload rejected: a Campaign Run is currently active. "
                "Wait for the Run to stop or complete before uploading new source data."
            },
            status_code=409,
        )

    content = await file.read()
    if not content:
        return JSONResponse({"message": "Uploaded file is empty."}, status_code=400)

    record = persist_upload(file.filename or "upload.csv", content, uploaded_by="operator")

    if record["status"] == "failed":
        return JSONResponse(
            {"message": "File rejected: not a parseable CSV.", "record": record},
            status_code=400,
        )

    return {"message": "File accepted and ingested.", "record": record}


@router.get("/files")
def files(limit: int = Query(100, ge=1, le=1000)):
    """Registry of uploaded files (newest first)."""
    return {"items": list_files(limit)}