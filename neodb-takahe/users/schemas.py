from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NodeInfoServices(BaseModel):
    inbound: list[str]
    outbound: list[str]


class NodeInfoSoftware(BaseModel):
    name: str
    version: str = "unknown"


class NodeInfoUsage(BaseModel):
    users: dict[str, int | None] | None = None
    local_posts: int = Field(default=0, alias="localPosts")


class NodeInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: Literal["2.0", "2.1", "2.2"]
    software: NodeInfoSoftware
    protocols: list[str] | None = None
    open_registrations: bool = Field(alias="openRegistrations")
    usage: NodeInfoUsage
    metadata: dict[str, Any] | list | None = None
