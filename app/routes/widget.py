"""
Embeddable widget: the script any website can include, and a demo page.
"""

import hashlib
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["widget"])

WIDGET_DIR = Path(__file__).resolve().parent.parent / "widget"


@lru_cache
def _asset(name: str) -> tuple[bytes, str]:
    """File bytes plus a content-hash ETag, read once per process."""
    content = (WIDGET_DIR / name).read_bytes()
    etag = '"' + hashlib.sha256(content).hexdigest()[:16] + '"'
    return content, etag


@router.get("/widget.js", include_in_schema=False)
async def widget_script(request: Request) -> Response:
    """The widget. Cacheable for an hour; revalidates cheaply with ETag."""
    content, etag = _asset("widget.js")
    headers = {"Cache-Control": "public, max-age=3600", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=content, media_type="application/javascript; charset=utf-8", headers=headers)


@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Keep browser consoles quiet on the demo page."""
    return Response(status_code=204, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/widget/demo", include_in_schema=False)
async def widget_demo() -> HTMLResponse:
    """A page that embeds the widget from this service."""
    content, _ = _asset("demo.html")
    return HTMLResponse(content=content.decode("utf-8"), headers={"Cache-Control": "no-cache"})
