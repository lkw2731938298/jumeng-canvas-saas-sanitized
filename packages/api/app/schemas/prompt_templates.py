from pydantic import BaseModel, ConfigDict, Field


class PromptTemplateOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    tool: str
    category: str
    key: str
    label: str
    content: str
    enabled: bool = True
    sort_order: int = Field(0, alias="sortOrder")


class PromptTemplateListOut(BaseModel):
    tool: str
    templates: list[PromptTemplateOut]


class PromptTemplateCreateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tool: str
    category: str
    key: str
    label: str = ""
    content: str = ""
    enabled: bool = True
    sort_order: int = Field(0, alias="sortOrder")


class PromptTemplatePatchIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tool: str | None = None
    category: str | None = None
    key: str | None = None
    label: str | None = None
    content: str | None = None
    enabled: bool | None = None
    sort_order: int | None = Field(None, alias="sortOrder")


class PromptTemplateDocumentOut(BaseModel):
    document: str
    help: str = ""


class PromptTemplateDocumentIn(BaseModel):
    document: str


class PromptConfigOut(BaseModel):
    version: int = 2
    tools: dict[str, dict]


class PromptToolConfigOut(BaseModel):
    tool: str
    config: dict


class PromptToolConfigIn(BaseModel):
    config: dict


class PromptPreviewIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tool: str
    runtime: dict = Field(default_factory=dict)
    # Optional draft config for admin preview only.
    config: dict | None = None


class PromptPreviewOut(BaseModel):
    tool: str
    prompt: str
    errors: list[str] = Field(default_factory=list)
