# Deployment Guide

## Source package

The source ZIP intentionally excludes Python runtimes, model weights, ExifTool binaries, caches, logs, virtual environments, Angular build output, and `node_modules`.

## Windows portable deployment

Run:

```powershell
.\setup.ps1
.\verify.ps1
```

The Windows setup follows `config/deployment.json` and installs all managed components inside the Artifex folder. After setup, copy the complete folder to a compatible Windows machine when the same GPU/runtime profile is appropriate.

Batch launchers:

```powershell
.\bin\prompts.ps1 -InputPath 'D:\Images' -Recurse
.\bin\organize.ps1 -InputPath 'D:\Images' -Recurse
```

Server launcher:

```powershell
.\bin\server.ps1 -OpenBrowser
```

## macOS/Linux deployment

Run:

```bash
./setup.sh
./verify.sh
```

Install ExifTool using the operating-system package manager or configure an explicit executable path. Point `application.model` to a local Qwen model directory. For production services, use a dedicated operating-system account, loopback binding behind a trusted reverse proxy, and an API key environment variable.

Batch launchers:

```bash
./bin/prompts.sh --input /data/images --recursive
./bin/organize.sh --input /data/images --recursive
```

Server launcher:

```bash
export ARTIFEX_API_KEY='replace-with-a-long-random-secret'
./bin/server.sh --host 127.0.0.1 --port 8000
```

## Angular client

The fallback UI is available immediately. To compile the Angular source:

```bash
./bin/build-web.sh
```

or:

```powershell
.\bin\build-web.ps1
```

The server automatically selects the production Angular bundle when present.

## Production checklist

- Keep model/cache directories on sufficiently large local storage.
- Keep the server at one process/worker per resident GPU model.
- Set `ARTIFEX_API_KEY` before any non-loopback bind.
- Restrict CORS origins to trusted client origins.
- Put TLS termination and network access controls in front of the service.
- Run `verify.sh` or `verify.ps1` after deployment changes.
- Use `--preview` before organizing a new archive root.
- Back up images and metadata before the first production bulk run.
