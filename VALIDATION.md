# Validation Record — Artifex 2.0.0

The source package was validated in a clean Linux extraction using Python 3.13.5.

## Automated checks

- Python bytecode compilation for the complete `qwen_archive` package and launcher.
- Canonical JSON configuration loading and strict setting validation.
- 103 automated unit/integration tests.
- REST API tests using FastAPI TestClient, including prompt-only, chat, multipart image, Base64 image, API-key, request limits, temporary-file cleanup, and embedded UI behavior.
- Adaptive structured-output tests covering token growth, repeated truncation, timeout recovery, in-memory image reduction, retry bounds, and source-image immutability.
- SQLite cache concurrency and corruption recovery tests.
- Metadata-merge, folder-policy, taxonomy, discovery, hashing, timestamp, collision, bucket, and cross-filesystem move tests.
- Windows launcher parameter-parity and routing checks.
- `bash -n` validation for every macOS/Linux script.
- Fallback JavaScript syntax validation.
- TypeScript syntax validation for the Angular source.
- ZIP integrity check and a second full test run from the extracted archive.

## Environment-dependent validation boundaries

- Production Qwen weights and a GPU were not loaded during source-level validation; model loading remains a deployment verification step.
- PowerShell was not installed in the build environment. Windows scripts were checked structurally and for parameter/CLI parity, but should also be exercised on the target Windows runtime after `setup.ps1`.
- npm dependency retrieval was unavailable during the build session. The Angular TypeScript source passed syntax validation, and the embedded zero-build web client passed API integration tests; run `bin/build-web.ps1` or `bin/build-web.sh` in an npm-enabled environment for a production Angular bundle.
