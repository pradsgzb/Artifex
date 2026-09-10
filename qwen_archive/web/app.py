"""FastAPI application factory for local prompt and image inference."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from ..contracts import InferenceEngine
from ..errors import (
    InferenceQueueFullError,
    InferenceQueueTimeoutError,
    InferenceTimeoutError,
    PayloadTooLargeError,
    RequestValidationError,
)
from ..jsonutil import parse_json_response
from ..settings import ServerSettings
from .executor import BoundedInferenceExecutor
from .images import decode_base64_image, read_upload_limited, temporary_validated_image
from .models import ChatGenerationRequest, GenerationResponse, TextGenerationRequest
from .security import ApiKeyVerifier


def _select_ui_root(settings: ServerSettings) -> Path | None:
    for raw in (settings.web_dist_path, settings.static_fallback_path):
        if not raw:
            continue
        root = Path(raw).expanduser().resolve()
        if (root / "index.html").is_file():
            return root
        browser = root / "browser"
        if (browser / "index.html").is_file():
            return browser
    return None


def _validate_request_limits(
    *,
    prompt: str,
    system_prompt: str,
    history: Sequence[Mapping[str, str]],
    max_new_tokens: int | None,
    settings: ServerSettings,
) -> int:
    if not prompt.strip():
        raise RequestValidationError("prompt must not be blank.")
    prompt_length = len(prompt) + len(system_prompt)
    if prompt_length > settings.max_prompt_chars:
        raise RequestValidationError(
            f"Prompt and systemPrompt exceed the configured {settings.max_prompt_chars}-character limit."
        )
    if len(history) > settings.max_history_messages:
        raise RequestValidationError(
            f"History exceeds the configured {settings.max_history_messages}-message limit."
        )
    history_chars = sum(len(str(item.get("content", ""))) for item in history)
    if history_chars > settings.max_history_chars:
        raise RequestValidationError(
            f"History exceeds the configured {settings.max_history_chars}-character limit."
        )
    requested = int(max_new_tokens if max_new_tokens is not None else min(1024, settings.max_request_new_tokens))
    if requested < 32:
        raise RequestValidationError("maxNewTokens must be >= 32.")
    if requested > settings.max_request_new_tokens:
        raise RequestValidationError(
            f"maxNewTokens exceeds the configured limit of {settings.max_request_new_tokens}."
        )
    return max(32, requested)


def create_app(
    *,
    engine: InferenceEngine,
    settings: ServerSettings,
    logger: logging.Logger | None = None,
) -> FastAPI:
    """Build an independently testable HTTP boundary around the shared engine."""
    app_logger = logger or logging.getLogger("qwen_archive")
    executor = BoundedInferenceExecutor(
        max_concurrency=settings.max_concurrency,
        max_queue_depth=settings.max_queue_depth,
        queue_timeout_seconds=settings.queue_timeout_seconds,
    )
    api_key = ApiKeyVerifier(settings.api_key_environment_variable)
    ui_root = _select_ui_root(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.preload_model:
            app_logger.info("Preloading local Qwen model before accepting requests.")
            await executor.run(engine.load)
        yield

    app = FastAPI(
        title="Artifex Local Qwen API",
        version="2.0.0",
        description="Local-only text and multimodal generation using the shared Artifex Qwen runtime.",
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.state.server_settings = settings
    app.state.inference_executor = executor
    app.state.ui_root = ui_root

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "X-API-Key"],
        )

    @app.exception_handler(PayloadTooLargeError)
    async def payload_too_large(_: Request, exc: PayloadTooLargeError):
        return JSONResponse(status_code=413, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(InferenceQueueFullError)
    async def queue_full(_: Request, exc: InferenceQueueFullError):
        return JSONResponse(
            status_code=429,
            content={"detail": str(exc)},
            headers={"Retry-After": "5"},
        )

    @app.exception_handler(InferenceQueueTimeoutError)
    async def queue_timeout(_: Request, exc: InferenceQueueTimeoutError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(InferenceTimeoutError)
    async def inference_timeout(_: Request, exc: InferenceTimeoutError):
        return JSONResponse(status_code=504, content={"detail": str(exc)})

    async def perform_generation(
        *,
        prompt: str,
        system_prompt: str,
        history: Sequence[Mapping[str, str]],
        image_path: Path | None,
        max_new_tokens: int | None,
        response_format: Literal["text", "json"],
    ) -> GenerationResponse:
        token_budget = _validate_request_limits(
            prompt=prompt,
            system_prompt=system_prompt,
            history=history,
            max_new_tokens=max_new_tokens,
            settings=settings,
        )
        request_id = uuid.uuid4().hex
        app_logger.info(
            "API inference %s accepted (image=%s history=%d maxNewTokens=%d format=%s).",
            request_id,
            "yes" if image_path else "no",
            len(history),
            token_budget,
            response_format,
        )
        output = await executor.run(
            engine.generate,
            system_prompt=system_prompt,
            user_text=prompt,
            image_path=image_path,
            history=history,
            max_new_tokens=token_budget,
            response_format=response_format,
        )
        json_value: Any | None = None
        if response_format == "json":
            try:
                json_value = parse_json_response(output)
            except Exception as exc:
                raise RequestValidationError(
                    f"The model did not return complete valid JSON: {exc}"
                ) from exc
        app_logger.info("API inference %s completed.", request_id)
        return GenerationResponse.create(
            request_id=request_id,
            model=engine.model_name,
            output=output,
            response_format=response_format,
            json_value=json_value,
        )

    @app.get("/api/v1/health", tags=["System"])
    async def health() -> dict[str, Any]:
        snapshot = await executor.snapshot()
        return {
            "status": "ok",
            "model": engine.model_name,
            "modelLoaded": engine.loaded,
            "activeRequests": snapshot.active,
            "queuedRequests": snapshot.waiting,
        }

    @app.get("/api/v1/capabilities", tags=["System"])
    async def capabilities() -> dict[str, Any]:
        return {
            "textGeneration": True,
            "imageGenerationInput": True,
            "chatHistory": True,
            "responseFormats": ["text", "json"],
            "imageMimeTypes": ["image/jpeg", "image/png", "image/webp"],
            "maxUploadBytes": settings.max_upload_bytes,
            "maxImagePixels": settings.max_image_pixels,
            "maxPromptChars": settings.max_prompt_chars,
            "maxHistoryMessages": settings.max_history_messages,
            "maxRequestNewTokens": settings.max_request_new_tokens,
            "apiKeyConfigured": api_key.configured,
        }

    @app.get("/api/v1/model", tags=["System"])
    async def model() -> dict[str, Any]:
        return {
            "name": engine.model_name,
            "loaded": engine.loaded,
            "cacheIdentity": engine.cache_identity,
        }

    @app.post(
        "/api/v1/generate/text",
        response_model=GenerationResponse,
        dependencies=[Depends(api_key)],
        tags=["Generation"],
    )
    async def generate_text(request: TextGenerationRequest) -> GenerationResponse:
        return await perform_generation(
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            history=(),
            image_path=None,
            max_new_tokens=request.max_new_tokens,
            response_format=request.response_format,
        )

    @app.post(
        "/api/v1/generate",
        response_model=GenerationResponse,
        dependencies=[Depends(api_key)],
        tags=["Generation"],
    )
    async def generate_multipart(
        prompt: str = Form(...),
        system_prompt: str = Form(default="", alias="systemPrompt"),
        max_new_tokens: int | None = Form(default=None, alias="maxNewTokens"),
        response_format: str = Form(default="text", alias="responseFormat"),
        image: UploadFile | None = File(default=None),
    ) -> GenerationResponse:
        normalized_format = response_format.casefold()
        if normalized_format not in {"text", "json"}:
            raise RequestValidationError("responseFormat must be text or json.")
        if image is None:
            return await perform_generation(
                prompt=prompt.strip(),
                system_prompt=system_prompt.strip(),
                history=(),
                image_path=None,
                max_new_tokens=max_new_tokens,
                response_format=normalized_format,  # type: ignore[arg-type]
            )
        data = await read_upload_limited(image, max_bytes=settings.max_upload_bytes)
        try:
            with temporary_validated_image(
                data,
                declared_mime_type=image.content_type,
                max_image_pixels=settings.max_image_pixels,
            ) as validated:
                return await perform_generation(
                    prompt=prompt.strip(),
                    system_prompt=system_prompt.strip(),
                    history=(),
                    image_path=validated.path,
                    max_new_tokens=max_new_tokens,
                    response_format=normalized_format,  # type: ignore[arg-type]
                )
        finally:
            await image.close()

    @app.post(
        "/api/v1/chat",
        response_model=GenerationResponse,
        dependencies=[Depends(api_key)],
        tags=["Generation"],
    )
    async def chat(request: ChatGenerationRequest) -> GenerationResponse:
        history = [message.model_dump() for message in request.history]
        if request.image_base64 is None:
            return await perform_generation(
                prompt=request.prompt,
                system_prompt=request.system_prompt,
                history=history,
                image_path=None,
                max_new_tokens=request.max_new_tokens,
                response_format=request.response_format,
            )
        data = decode_base64_image(
            request.image_base64,
            max_bytes=settings.max_upload_bytes,
        )
        with temporary_validated_image(
            data,
            declared_mime_type=request.image_mime_type,
            max_image_pixels=settings.max_image_pixels,
        ) as validated:
            return await perform_generation(
                prompt=request.prompt,
                system_prompt=request.system_prompt,
                history=history,
                image_path=validated.path,
                max_new_tokens=request.max_new_tokens,
                response_format=request.response_format,
            )

    @app.get("/", include_in_schema=False)
    async def index():
        if ui_root is None:
            return JSONResponse(
                status_code=200,
                content={
                    "name": "Artifex Local Qwen API",
                    "docs": "/docs",
                    "message": "Build web/angular or restore web/static to enable the embedded UI.",
                },
            )
        return FileResponse(ui_root / "index.html")

    @app.get("/{asset_path:path}", include_in_schema=False)
    async def static_asset(asset_path: str):
        if asset_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        if ui_root is None:
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (ui_root / asset_path).resolve()
        try:
            candidate.relative_to(ui_root)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Not found") from exc
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(ui_root / "index.html")

    return app
