"""为每个请求生成唯一 trace_id 并注入上下文."""

import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """为请求分配唯一请求 ID，附加到响应头和日志上下文."""

    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.trace_id = trace_id
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = trace_id
        return response
