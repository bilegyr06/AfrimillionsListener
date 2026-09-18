from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.phones import validate_phone
from app.db.sms import count_sms_logs, get_sms_logs
from app.services.reporting import active_feature_kinds, active_sms_kinds
from app.services.sms import MANUAL, SmsDispatcher


router = APIRouter()


class SMSRequest(BaseModel):
    phone: str = Field(..., min_length=7, description="Recipient phone number")
    message: str = Field(..., min_length=1, max_length=160, description="SMS text")


@router.post("/sms")
async def send_custom_sms(req: SMSRequest):
    """Manual operator send. The request goes through the same canonical phone
    gate and dispatcher as every other SMS; a send is logged in sms_log."""
    check = validate_phone(req.phone)
    if check.canonical is None:
        return JSONResponse(
            {"message": f"SMS not sent: {check.reason}", "phone": req.phone},
            status_code=400,
        )

    dispatcher = SmsDispatcher(max_concurrency=1)
    outcome = await dispatcher.dispatch(
        phone=req.phone,
        message=req.message,
        kind=MANUAL,
        user_id=req.phone,
    )
    dispatcher.flush()

    if outcome.accepted:
        return {
            "message": "SMS sent.",
            "phone": outcome.phone,
            "response": {"message_id": outcome.message_id, "balance": outcome.balance_after},
        }
    return {"message": "SMS delivery failed.", "phone": outcome.phone}


@router.get("/sms/logs")
def sms_logs(
    kind: str | None = Query(None, description="welcome | inactive | manual"),
    since: str | None = Query(None),
    until: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
):
    """Persisted SMS attempts, newest first, with page metadata.

    With no `kind` this is the active SMS activity view: records of the
    currently enabled features plus manual operator sends. An explicit `kind`
    keeps historical/feature-specific inspection available regardless of
    enablement. `enabled_features` is the backend's authoritative scope, so
    the frontend never derives it itself.
    """
    kinds = None if kind else active_sms_kinds()
    total = count_sms_logs(kind=kind, kinds=kinds, since=since, until=until)
    items = get_sms_logs(
        kind=kind, kinds=kinds, since=since, until=until,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": -(-total // page_size) if total else 0,
        "enabled_features": sorted(active_feature_kinds()),
    }