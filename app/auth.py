from __future__ import annotations

import secrets
from functools import wraps
from typing import Callable, TypeVar

from fastapi import Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import get_settings


F = TypeVar("F", bound=Callable)
COOKIE_NAME = "mir_indexer_session"


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().secret_key, salt="mir-indexer-auth")


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        data = _serializer().loads(token)
    except BadSignature:
        return False
    return data.get("user") == get_settings().admin_username


def create_session_token() -> str:
    return _serializer().dumps({"user": get_settings().admin_username})


def verify_credentials(username: str, password: str) -> bool:
    settings = get_settings()
    return secrets.compare_digest(username, settings.admin_username) and secrets.compare_digest(
        password,
        settings.admin_password,
    )


def login_required(func: F) -> F:
    @wraps(func)
    async def wrapper(*args, **kwargs):
        request = kwargs.get("request")
        if request is None:
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
        if request is None or not is_authenticated(request):
            return RedirectResponse("/login", status_code=303)
        result = func(*args, **kwargs)
        if hasattr(result, "__await__"):
            return await result
        return result

    return wrapper  # type: ignore[return-value]
