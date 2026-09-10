# Artifex REST API

Base path: `/api/v1`

Interactive OpenAPI documentation is available at `/docs` while the server is running.

## Authentication

When the configured API-key environment variable contains a value, generation endpoints require:

```http
X-API-Key: <secret>
```

System read endpoints remain available for local health/capability discovery.

## System endpoints

### `GET /api/v1/health`

Returns service/model/queue status.

### `GET /api/v1/capabilities`

Returns supported request modes, MIME types, and configured request limits.

### `GET /api/v1/model`

Returns model identity, load state, and cache identity.

## Prompt-only generation

### `POST /api/v1/generate/text`

```json
{
  "prompt": "Describe the key compositional elements.",
  "systemPrompt": "Be precise and concise.",
  "maxNewTokens": 1024,
  "responseFormat": "text"
}
```

`responseFormat` is `text` or `json`. When `json` is requested, Artifex verifies that the model returned one complete parseable JSON value and returns it as `jsonValue`.

## Prompt with optional uploaded image

### `POST /api/v1/generate`

Content type: `multipart/form-data`

Fields:

- `prompt` — required text.
- `systemPrompt` — optional text.
- `maxNewTokens` — optional integer.
- `responseFormat` — `text` or `json`.
- `image` — optional JPEG, PNG, or WebP upload.

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/generate \
  -H "X-API-Key: $ARTIFEX_API_KEY" \
  -F "prompt=Classify this painting and describe its subject." \
  -F "responseFormat=json" \
  -F "image=@painting.jpg;type=image/jpeg"
```

## Chat with optional image

### `POST /api/v1/chat`

```json
{
  "prompt": "Now propose a six-level archival hierarchy.",
  "systemPrompt": "You are an image archivist.",
  "history": [
    {"role": "user", "content": "Describe this image."},
    {"role": "assistant", "content": "The image depicts..."}
  ],
  "maxNewTokens": 1024,
  "responseFormat": "text",
  "imageBase64": "<optional base64 bytes>",
  "imageMimeType": "image/jpeg"
}
```

The image can also be supplied as a `data:image/...;base64,...` URL. The declared MIME type, when supplied, must match the detected image format.

## Generation response

```json
{
  "requestId": "ccf830d5ac1f4c98a4170f2e1a683b3d",
  "createdAt": "2026-08-27T00:00:00+00:00",
  "model": "Qwen/Qwen3.5-4B",
  "output": "...",
  "responseFormat": "text",
  "jsonValue": null
}
```

## Status mapping

- `400` — semantic request/image/JSON validation failure.
- `401` — invalid or missing API key when authentication is enabled.
- `413` — upload or decoded image exceeds configured byte limit.
- `422` — request model validation failure or unknown field.
- `429` — bounded inference queue is full.
- `503` — request timed out waiting for inference capacity.
- `504` — model generation exceeded its inference timeout.
