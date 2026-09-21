from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.files import list_files
from app.services.ingestion import persist_upload
from app.services.windows import any_window_has_active_run


router = APIRouter()


def _ingest_uploaded_file(record: dict) -> None:
    """Durably ingest a just-accepted upload into its fact table.

    Upload is an explicit ingestion lifecycle point: an accepted file's facts
    are persisted when it enters the system, not as a side effect of a later
    report read. Ingestion is single-flight via the files ledger, so retrying
    or re-uploading the same file cannot double-ingest. Registrations/other
    datasets are read live from the folder and have no fact table to ingest.
    """
    dataset = record.get("dataset")
    stored = record.get("stored_filename")
    if record.get("status") != "succeeded" or not stored:
        return
    path = settings.DATA_FOLDER / stored
    if dataset == "Sales":
        from app.services.plays import ingest_sales_file

        ingest_sales_file(path)
    elif dataset == "Deposit_events":
        from app.services.deposits import ingest_deposit_file

        ingest_deposit_file(path)
    elif dataset == "Login":
        from app.services.logins import ingest_login_file

        ingest_login_file(path)


@router.post("/files")
async def upload_files(file: list[UploadFile] = File(...)):
    """Accept one or more manually uploaded data CSVs in a single request.

    Each file is validated and parsed independently: unparseable content is
    rejected (nothing written to the data folder), parseable files are placed
    under a recognized dataset prefix and become visible to the next cycle.
    A mixed batch reports per-file outcomes rather than failing as a whole;
    only a batch where nothing was accepted returns 400.

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

    records: list[dict] = []
    errors: list[str] = []

    for upload in file:
        name = upload.filename or "upload.csv"
        content = await upload.read()
        if not content:
            errors.append(f"{name} is empty.")
            continue
        record = persist_upload(name, content, uploaded_by="operator")
        _ingest_uploaded_file(record)
        records.append(record)

    accepted = [r for r in records if r["status"] == "succeeded"]
    rejected = [r for r in records if r["status"] != "succeeded"]
    total = len(accepted) + len(rejected) + len(errors)

    if not accepted:
        reasons = errors or [f"{r['original_filename']}: {r['parse_error']}" for r in rejected]
        return JSONResponse(
            {
                "message": "No files accepted: " + "; ".join(reasons),
                "records": records,
                "errors": errors,
            },
            status_code=400,
        )

    message = f"{len(accepted)} of {total} file(s) accepted and ingested."
    if rejected or errors:
        message += f" {total - len(accepted)} rejected."

    return {"message": message, "records": records, "errors": errors}


@router.get("/files")
def files(limit: int = Query(100, ge=1, le=1000)):
    """Registry of uploaded files (newest first)."""
    return {"items": list_files(limit)}