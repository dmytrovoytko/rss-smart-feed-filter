from __future__ import annotations

from app.config import get_settings
from app.main import app


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
