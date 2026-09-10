# Artifex — Local Qwen Image Archive Platform

Artifex is a standalone Python application for bulk image analysis, metadata enrichment, archival organization, and interactive local multimodal chat. It uses a locally loaded Qwen3.5 model and does not require Gemini, ChatGPT, a browser automation layer, ComfyUI, or any cloud inference API.

Version **2.0.0** provides three independently launchable surfaces:

1. **Prompts** — generate or reuse structured image-analysis metadata.
2. **Organize** — classify and safely move images into a 6–9-level archive hierarchy.
3. **Server** — expose the shared inference runtime through REST APIs and an embedded chat interface.

The two batch tasks and the web service use the same configuration, inference engine, adaptive recovery policy, image handling, and logging infrastructure.

## Key safeguards

- The Qwen model is loaded lazily and remains resident for the process lifetime.
- Images may be reduced **in memory only** for inference; source files are never resized or re-encoded.
- Incomplete structured output automatically receives a larger bounded token budget.
- Repeated truncation, timeout, or GPU-memory pressure can reduce the in-memory image ceiling.
- Subject/XPSubject updates preserve unknown third-party JSON properties.
- Organize is metadata-first and avoids inference whenever valid classification already exists.
- File moves are collision-safe, timestamp-preserving, and hash-verified across filesystems.
- SQLite inference caching is thread-safe and uses content/settings/prompt identity.
- The REST server enforces request, upload, image-pixel, queue, token, and history limits.
- Remote network binding is refused without an API key unless explicitly overridden.

## Repository layout

```text
Artifex/
├── qwen_archive/              Python application packages
│   ├── tasks/                 Batch use cases
│   └── web/                   REST boundary and API models
├── config/                    Runtime settings, prompts, and taxonomy
├── bin/                       Windows, macOS, and Linux launchers
├── web/
│   ├── angular/               Angular chat application source
│   ├── static/                Zero-build embedded UI fallback
│   └── dist/                  Angular production output (generated)
├── tests/                     Automated regression suite
├── setup.ps1 / setup.sh       Platform setup entry points
├── verify.ps1 / verify.sh     Validation entry points
└── make-source-package.sh     Clean source archive builder
```

See `ARCHITECTURE.md` for dependency boundaries and `API.md` for REST contracts.

## Configuration

The canonical configuration is `config/settings.json`. Operational settings are not embedded in task code. The file contains:

- platform-specific runtime and ExifTool paths;
- model, cache, device, precision, attention, and quantization settings;
- token, timeout, retry, and image ceilings;
- adaptive generation policy;
- metadata defaults and attempts;
- folder validation policy;
- logging level/color/file destination;
- REST host, port, authentication environment variable, CORS, and request limits;
- Angular distribution and fallback UI paths.

Important production defaults:

```json
{
  "maxNewTokens": 1024,
  "retries": 2,
  "maxInferenceSeconds": 180,
  "maxImageSide": 1024,
  "adaptiveGeneration": {
    "enabled": true,
    "maxNewTokensCeiling": 3072,
    "tokenGrowthFactor": 1.6,
    "minimumImageSide": 640,
    "imageReductionFactor": 0.75
  }
}
```

## Setup

### Windows

The existing portable bootstrap remains supported:

```powershell
.\setup.ps1
.\verify.ps1
```

The Windows bootstrap installs an application-local runtime, Qwen dependencies, server dependencies, model files, and ExifTool according to the deployment configuration.

### macOS or Linux

Python 3.10 or newer is required. ExifTool must be available on `PATH` or configured explicitly.

```bash
./setup.sh
./verify.sh
```

`setup.sh` creates `.venv`, installs PyTorch, the local-Qwen runtime dependencies, and the embedded server dependencies. Model weights are not silently downloaded by the POSIX bootstrap; set `application.model` to a local model directory or permit model resolution through configuration/CLI.

Optional frontend build:

```bash
./setup.sh --build-web
```

## Bulk image analysis

### Windows

```powershell
.\bin\prompts.ps1 -InputPath 'D:\Paintings\Input' -Recurse
```

### macOS or Linux

```bash
./bin/prompts.sh --input /data/paintings/input --recursive
```

Useful options:

```text
--log-level DEBUG|INFO|WARNING|ERROR|CRITICAL
--color | --no-color
--max-new-tokens 1024
--adaptive-generation | --no-adaptive-generation
--max-new-tokens-ceiling 3072
--minimum-image-side 640
--preview
```

Prompts processing order:

1. Read existing Subject/XPSubject metadata.
2. Reuse a complete valid analysis unless forced.
3. Populate only configured capture tags that are missing.
4. Prefer Comments/XPComment when configured.
5. Use the persistent inference cache when possible.
6. Generate and validate structured local-Qwen output.
7. Apply bounded adaptive recovery when output is incomplete.
8. Merge application-owned fields without deleting unknown properties.
9. Preserve filesystem timestamps.

## Archive organization

### Windows

```powershell
.\bin\organize.ps1 -InputPath 'D:\Paintings\Input' -OutputRoot 'D:\Paintings\Organized' -Recurse
```

### macOS or Linux

```bash
./bin/organize.sh \
  --input /data/paintings/input \
  --output-root /data/paintings/organized \
  --recursive
```

Organize first uses valid embedded `folderClassification`. When classification is absent, it can classify Comments text before using the image. It merges only `folderClassification` into Subject JSON and then performs a collision-safe move.

Use `--preview` to display the plan without inference, metadata writes, or moves.

## Embedded REST server and chat application

The server is intentionally separate from both batch launchers.

### Windows

```powershell
.\bin\server.ps1 -OpenBrowser
```

### macOS or Linux

```bash
./bin/server.sh --open-browser
```

Default addresses:

```text
Chat UI:      http://127.0.0.1:8000/
OpenAPI UI:   http://127.0.0.1:8000/docs
OpenAPI JSON: http://127.0.0.1:8000/openapi.json
```

The embedded server prefers the compiled Angular bundle under `web/dist/artifex-chat/browser`. When it is not present, it serves the dependency-free UI under `web/static`, so the chat interface works without Node.js at runtime.

### API-key protection

Set the configured environment variable before launching:

Windows PowerShell:

```powershell
$env:ARTIFEX_API_KEY = 'replace-with-a-long-random-secret'
.\bin\server.ps1 -HostAddress '0.0.0.0'
```

macOS/Linux:

```bash
export ARTIFEX_API_KEY='replace-with-a-long-random-secret'
./bin/server.sh --host 0.0.0.0
```

Clients then send `X-API-Key`. The server refuses an unauthenticated non-loopback bind unless `--allow-unauthenticated-remote` is explicitly supplied.

## Build the Angular application

Windows:

```powershell
.\bin\build-web.ps1
```

macOS/Linux:

```bash
./bin/build-web.sh
```

The Angular source is in `web/angular`; its production bundle is generated into `web/dist/artifex-chat`.

## Logging

Logging is configured in `application.logging` and can be overridden by command switches.

```json
{
  "logging": {
    "level": "INFO",
    "color": "auto",
    "path": ""
  }
}
```

Console levels have distinct ANSI colors when supported. File logs are always plain UTF-8. Reconfiguration replaces handlers and does not duplicate messages.

## Validation

Run the complete source-level validation suite:

```bash
./verify.sh
```

The suite compiles Python, validates configuration, runs unit/integration tests, checks every shell launcher with `bash -n`, checks fallback JavaScript syntax, and performs a TypeScript syntax pass when TypeScript is available.

The test suite does not load multi-gigabyte production model weights or require a GPU. Full deployment validation remains available through the platform setup/verification workflow.

## Create a clean source ZIP

```bash
./make-source-package.sh /path/to/Artifex-v2.0.0-source.zip
```

The source packager excludes runtime binaries, model weights, caches, logs, generated frontend bundles, virtual environments, `node_modules`, bytecode, and existing ZIP files.
