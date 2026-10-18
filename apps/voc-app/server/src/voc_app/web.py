"""Serve compiled web assets after API routes, without an HTML fallback for APIs."""

from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException


class WebFiles(StaticFiles):
    async def get_response(self, path, scope):
        # Reserve these namespaces even if a build accidentally contains matching files.
        if path.split("/", 1)[0] in {"api", "health", "mcp", "docs", "openapi.json"}:
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


def mount_web(application, directory):
    """Expose only an explicit compiled directory; missing builds fail at startup."""
    if directory is None:
        return
    if not (directory / "index.html").is_file():
        raise ValueError("VOC_WEB_DIR must contain a compiled index.html")
    application.mount(
        "/",
        WebFiles(directory=str(directory), html=True, follow_symlink=False),
        name="web",
    )
