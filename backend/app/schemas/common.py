"""统一响应与分页模型."""

from typing import Any, Optional, TypeVar, Generic, List

from pydantic import BaseModel

T = TypeVar("T")


class ResponseModel(BaseModel):
    """统一 API 响应."""
    code: int = 200
    message: str = ""
    data: Any = None


class PaginatedResponse(BaseModel, Generic[T]):
    """分页响应."""
    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int
