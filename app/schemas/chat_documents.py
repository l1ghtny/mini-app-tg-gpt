"""A bounded document format, never executable code or arbitrary markup."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocumentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["paragraph", "heading", "bullet_list", "numbered_list", "table", "code", "page_break"]
    text: str = Field(max_length=8000)
    items: list[str] = Field(max_length=100)
    rows: list[list[str]] = Field(max_length=100)

    @model_validator(mode="after")
    def validate_content(self):
        if self.kind in {"paragraph", "heading", "code"}:
            if not self.text.strip() or self.items or self.rows:
                raise ValueError("Text blocks require text only")
        elif self.kind.endswith("list"):
            if not self.items or self.text or self.rows or any(not x.strip() for x in self.items):
                raise ValueError("Lists require nonempty items only")
        elif self.kind == "table":
            width = len(self.rows[0]) if self.rows else 0
            if not 1 <= width <= 8 or self.text or self.items or any(len(row) != width for row in self.rows):
                raise ValueError("Tables require one to eight columns and rectangular rows")
        elif self.text or self.items or self.rows:
            raise ValueError("Page breaks do not contain text")
        strings = [self.text, *self.items, *(cell for row in self.rows for cell in row)]
        if any(len(x) > 8000 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", x) for x in strings):
            raise ValueError("Document text contains invalid characters or exceeds its limit")
        return self


class DocumentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    blocks: list[DocumentBlock] = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_size(self):
        if not self.title.strip() or re.search(r"[\x00-\x1f]", self.title):
            raise ValueError("Invalid document title")
        if len(self.model_dump_json().encode("utf-8")) > 100_000:
            raise ValueError("Document content exceeds 100 KB")
        if all(block.kind == "page_break" for block in self.blocks):
            raise ValueError("Document has no content")
        if sum(len(block.rows) for block in self.blocks) > 500 or sum(len(row) for block in self.blocks for row in block.rows) > 1500:
            raise ValueError("Document tables exceed the rendering limit")
        return self


class CreateDocumentArguments(DocumentSpec):
    query: str = Field(min_length=1, max_length=8000)
    format: Literal["docx", "pdf"]
    parent_document_id: str | None


def document_tool_schema():
    # Strict function schemas require every object property, including empty
    # fields and nullable revision IDs. Bounds are also enforced server-side.
    schema = CreateDocumentArguments.model_json_schema()
    return schema
