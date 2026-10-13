from typing import Any

from fastapi.responses import JSONResponse


def ok(result: Any) -> JSONResponse:
    return JSONResponse({"status": "ok", "result": result})


def error(message: str) -> JSONResponse:
    return JSONResponse({"status": "error", "message": message})
