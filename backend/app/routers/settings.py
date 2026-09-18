from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.settings import list_operator_settings, update_setting


router = APIRouter()


class SettingsUpdateRequest(BaseModel):
    key: str = Field(..., description="Setting key from the settings registry")
    value: str = Field(..., description="New value as a string")


@router.get("/settings")
def get_settings():
    """Operator settings with current effective values and UI hints."""
    return {"items": list_operator_settings()}


@router.post("/settings")
def post_settings(req: SettingsUpdateRequest):
    """Persist and apply one operator setting. Secrets are not editable here."""
    try:
        change = update_setting(req.key, req.value)
    except KeyError:
        return JSONResponse(
            {"message": f"Unknown setting key: {req.key}"}, status_code=404
        )
    except (ValueError, TypeError):
        return JSONResponse(
            {"message": f"Invalid value: {req.value}"}, status_code=400
        )
    return {"message": "Setting updated.", "setting": change}