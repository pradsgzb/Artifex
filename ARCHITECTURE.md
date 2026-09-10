# Artifex Architecture

## Design objectives

Artifex 2.0 separates policy, use cases, infrastructure, and delivery mechanisms. Core workflows depend on protocols rather than concrete databases, metadata tools, model libraries, or web frameworks. Infrastructure is constructed at a composition root (`cli.py` or `server_cli.py`) and injected into use cases.

## Layers

### Domain and policy

- `domain.py` — validated painting analysis and folder classification.
- `settings.py` — strict typed policies for adaptive generation, logging, and server limits.
- `subject.py` — lossless ownership-aware Subject JSON merge rules.
- `taxonomy.py` — deterministic category loading and duplicate diagnostics.

These modules do not invoke Qwen, ExifTool, FastAPI, or the filesystem move engine.

### Application use cases

- `tasks/prompts_task.py` — metadata analysis workflow.
- `tasks/organize_task.py` — metadata-first classification and organization workflow.

Tasks receive contracts for inference, metadata, caching, and filesystem services. They coordinate behavior but do not load model libraries or parse command-line arguments.

### Infrastructure

- `engine.py` — resident local-Qwen adapter and bounded adaptive structured-output recovery.
- `metadata.py` — ExifTool repository.
- `cache.py` — thread-safe SQLite inference cache.
- `organizer.py` — collision-safe/timestamp-safe move implementation.
- `files.py` and `timestamps.py` — discovery, hashing, and portable timestamp behavior.

### Delivery mechanisms

- `cli.py` — batch CLI composition root.
- `server_cli.py` — independent web-service composition root.
- `web/app.py` — FastAPI HTTP boundary.
- `web/angular` — Angular client source.
- `web/static` — zero-build embedded client fallback.
- `bin/*.ps1` and `bin/*.sh` — platform launchers.

## Dependency direction

```text
Delivery -> Application -> Domain/Contracts
     |             |
     +-------> Infrastructure implementations
```

Application workflows know only the protocols in `contracts.py`. Concrete implementations are selected at the outer boundary. This keeps model, cache, ExifTool, and API behaviors independently testable.

## Adaptive structured-output recovery

For validated JSON generation:

1. Start with the configured token and image limits.
2. Parse and validate the response.
3. On incomplete JSON, increase `maxNewTokens` by the configured growth factor up to the ceiling.
4. On repeated truncation or when token growth is exhausted, reduce the in-memory image ceiling.
5. On timeout/GPU memory pressure, reduce the in-memory image ceiling before retrying.
6. On schema/domain validation failure, send a corrective prompt without automatically increasing compute.
7. Stop after the configured retry count and return all attempt diagnostics.

The source image path is opened read-only. Resizing occurs on a temporary PIL image object and is never written to disk.

## Subject JSON ownership

Artifex owns the standard analysis fields listed in `subject.py`. A full analysis replaces only those owned fields. Folder-only classification replaces only `folderClassification`. Unknown fields and future integration extensions remain intact.

## Web-service concurrency model

One Uvicorn worker owns one resident model. A bounded executor controls concurrent inference, queue depth, and queue timeout. Blocking model generation runs in a worker thread so the HTTP event loop remains responsive for health checks and static assets.

## Security boundary

- Loopback binding is the default.
- Non-loopback binding requires an API key or explicit unsafe override.
- API keys are read from an environment variable and compared in constant time.
- Images are decoded with byte, format, MIME, dimension, and pixel-count checks.
- Uploaded data is stored only in a request-scoped temporary file and deleted after generation.
- Static asset resolution prevents directory traversal.
- Pydantic request models reject unknown fields.
