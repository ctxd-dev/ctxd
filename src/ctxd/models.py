from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class SearchItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(validation_alias=AliasChoices("id", "document_uid"))
    app_name: str | None = None
    title: str
    url: str
    text: str = Field(
        default="",
        validation_alias=AliasChoices("text", "snippet"),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    results: list[SearchItem] = Field(default_factory=list)
    error: str | None = None
    dsl_parse_error: str | None = None


class DocumentResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    app_name: str | None = None
    title: str | None = None
    text: str | None = None
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class ProfileResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    integration_access: str
    file_tree: str
