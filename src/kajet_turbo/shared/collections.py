"""Wire types shared by the REST API and MCP collection surfaces."""

from typing import Literal

from pydantic import BaseModel


class CollectionResult(BaseModel):
    name: str
    grain: Literal["day", "week", "month", "year"]
    cardinality: Literal["one", "many"]
    folder: str
    title: str
    description: str | None = None
