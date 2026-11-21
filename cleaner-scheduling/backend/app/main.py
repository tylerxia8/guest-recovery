from __future__ import annotations

from app.api.app_factory import create_app
from app.config import get_settings

app = create_app(get_settings())
