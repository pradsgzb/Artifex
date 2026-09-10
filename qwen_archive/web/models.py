"""Versioned HTTP request and response contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ChatMessage(ApiModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("message content must not be blank")
        return cleaned


class TextGenerationRequest(ApiModel):
    prompt: str = Field(min_length=1)
    system_prompt: str = Field(default="", alias="systemPrompt")
    max_new_tokens: int | None = Field(default=None, alias="maxNewTokens", ge=32)
    response_format: Literal["text", "json"] = Field(default="text", alias="responseFormat")

    @field_validator("prompt")
    @classmethod
    def normalize_prompt(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("prompt must not be blank")
        return cleaned


class ChatGenerationRequest(TextGenerationRequest):
    history: list[ChatMessage] = Field(default_factory=list)
    image_base64: str | None = Field(default=None, alias="imageBase64")
    image_mime_type: str | None = Field(default=None, alias="imageMimeType")


class GenerationResponse(ApiModel):
    request_id: str = Field(alias="requestId")
    created_at: str = Field(alias="createdAt")
    model: str
    output: str
    response_format: Literal["text", "json"] = Field(alias="responseFormat")
    json_value: Any | None = Field(default=None, alias="jsonValue")

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        model: str,
        output: str,
        response_format: Literal["text", "json"],
        json_value: Any | None = None,
    ) -> "GenerationResponse":
        return cls(
            requestId=request_id,
            createdAt=datetime.now(timezone.utc).isoformat(),
            model=model,
            output=output,
            responseFormat=response_format,
            jsonValue=json_value,
        )
