"""Separate composition root for the embedded REST server and chat UI."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from typing import Any, Mapping

from .config import load_json_config
from .logging_utils import configure_logging
from .runtime import EngineSettings
from .settings import LoggingSettings, ServerSettings
from .web import create_app


def _prescan_config(argv: list[str]) -> str | None:
    for index, value in enumerate(argv):
        if value == "--config" and index + 1 < len(argv):
            return argv[index + 1]
        if value.startswith("--config="):
            return value.split("=", 1)[1]
    return None


def build_parser(config: dict[str, Any]) -> argparse.ArgumentParser:
    nested = config.get("server", {})
    server = ServerSettings.from_mapping(
        nested if isinstance(nested, Mapping) else {},
        resolved_paths=config.get("_resolvedPaths", {}),
    )
    logging_settings = LoggingSettings.from_application(config)
    engine = EngineSettings.from_application(config)
    parser = argparse.ArgumentParser(
        prog="artifex-server",
        description="Start the embedded Artifex REST API and chat application.",
    )
    parser.add_argument("--config", default=config.get("_configPath", ""))
    parser.add_argument("--host", default=server.host)
    parser.add_argument("--port", type=int, default=server.port)
    preload = parser.add_mutually_exclusive_group()
    preload.add_argument("--preload-model", dest="preload_model", action="store_true")
    preload.add_argument("--no-preload-model", dest="preload_model", action="store_false")
    parser.set_defaults(preload_model=server.preload_model)
    parser.add_argument("--api-key-env", default=server.api_key_environment_variable)
    parser.add_argument("--allow-unauthenticated-remote", action="store_true", default=server.allow_unauthenticated_remote)
    parser.add_argument("--cors-origin", action="append", default=None, help="Repeat to replace configured CORS origins.")
    browser = parser.add_mutually_exclusive_group()
    browser.add_argument("--open-browser", dest="open_browser", action="store_true")
    browser.add_argument("--no-open-browser", dest="open_browser", action="store_false")
    parser.set_defaults(open_browser=server.open_browser)
    parser.add_argument("--model", default=engine.model_name)
    parser.add_argument("--model-cache-dir", default=engine.model_cache_dir)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default=engine.device)
    parser.add_argument("--precision", choices=("auto", "bfloat16", "float16", "float32"), default=engine.precision)
    parser.add_argument("--attention", choices=("auto", "sdpa", "eager", "flash_attention_2"), default=engine.attention)
    parser.add_argument("--quantization", choices=("none", "8bit", "4bit"), default=engine.quantization)
    parser.add_argument("--max-new-tokens", type=int, default=engine.max_new_tokens)
    parser.add_argument("--max-image-side", type=int, default=engine.max_image_side)
    parser.add_argument("--max-inference-seconds", type=float, default=engine.max_inference_seconds)
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"), default=logging_settings.level)
    parser.add_argument("--log-path", default=logging_settings.path)
    color = parser.add_mutually_exclusive_group()
    color.add_argument("--color", dest="color_mode", action="store_const", const="always")
    color.add_argument("--no-color", dest="color_mode", action="store_const", const="never")
    parser.set_defaults(color_mode=logging_settings.color)
    parser.set_defaults(_config=config, _server=server, _engine=engine)
    return parser


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url, new=2)
    except Exception:
        pass


def run(args: argparse.Namespace) -> int:
    if not 1 <= args.port <= 65535:
        raise ValueError("--port must be between 1 and 65535.")
    if args.max_new_tokens < 32:
        raise ValueError("--max-new-tokens must be >= 32.")
    if args.max_image_side < 0:
        raise ValueError("--max-image-side must be >= 0.")

    server = args._server.with_overrides(
        host=args.host,
        port=args.port,
        preload_model=args.preload_model,
        api_key_environment_variable=args.api_key_env,
        allow_unauthenticated_remote=args.allow_unauthenticated_remote,
        cors_origins=tuple(args.cors_origin) if args.cors_origin is not None else None,
        open_browser=args.open_browser,
    )
    api_key = os.getenv(server.api_key_environment_variable, "")
    if not server.is_loopback and not api_key and not server.allow_unauthenticated_remote:
        raise ValueError(
            f"Refusing unauthenticated remote bind to {server.host}. Set {server.api_key_environment_variable}, "
            "bind to 127.0.0.1, or explicitly pass --allow-unauthenticated-remote."
        )

    logger = configure_logging(args.log_level, args.log_path, args.color_mode)
    engine_settings = args._engine.with_overrides(
        model_name=args.model,
        model_cache_dir=args.model_cache_dir,
        device=args.device,
        precision=args.precision,
        attention=args.attention,
        quantization=args.quantization,
        max_new_tokens=args.max_new_tokens,
        max_image_side=args.max_image_side,
        max_inference_seconds=args.max_inference_seconds,
    )
    engine = engine_settings.build(logger)
    app = create_app(engine=engine, settings=server, logger=logger)

    scheme_host = "127.0.0.1" if server.host in {"0.0.0.0", "::"} else server.host
    url = f"http://{scheme_host}:{server.port}/"
    logger.info("Starting Artifex server at %s", url)
    logger.info("OpenAPI documentation: %sdocs", url)
    if api_key:
        logger.info("API-key authentication is enabled via environment variable %s.", server.api_key_environment_variable)
    elif not server.is_loopback:
        logger.warning("Remote server is running without API-key authentication by explicit override.")
    if server.open_browser:
        threading.Timer(1.0, _open_browser, args=(url,)).start()

    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("Server dependencies are missing. Install requirements-server.txt.") from exc
    uvicorn.run(
        app,
        host=server.host,
        port=server.port,
        workers=1,
        log_config=None,
        access_log=False,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        config = load_json_config(_prescan_config(arguments))
        parser = build_parser(config)
        return run(parser.parse_args(arguments))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
