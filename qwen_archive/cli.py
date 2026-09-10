"""Command-line composition root for the two batch tasks."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .cache import InferenceCache
from .config import default_state_dir, load_json_config
from .constants import (
    DEFAULT_ANALYSIS_PROMPT_PATH,
    DEFAULT_CATEGORIES_PATH,
    DEFAULT_FOLDER_PROMPT_PATH,
    VERSION,
)
from .domain import FolderValidationPolicy
from .logging_utils import configure_logging
from .metadata import ExifToolService
from .prompts import PromptLibrary
from .runtime import EngineSettings
from .settings import AdaptiveGenerationPolicy, LoggingSettings, require_bool
from .subject import SubjectMergeService
from .tasks import OrganizeTask, PromptsTask
from .taxonomy import CategoryTaxonomy


def _prescan_config(argv: list[str]) -> str | None:
    for index, argument in enumerate(argv):
        if argument == "--config" and index + 1 < len(argv):
            return argv[index + 1]
        if argument.startswith("--config="):
            return argument.split("=", 1)[1]
    return None


def _bool_default(config: Mapping[str, Any], key: str, default: bool) -> bool:
    return require_bool(config.get(key, default), f"application.{key}")


def _add_boolean_pair(
    parser: argparse.ArgumentParser,
    *,
    destination: str,
    enabled_flag: str,
    disabled_flag: str,
    default: bool,
    enabled_help: str,
    disabled_help: str,
) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(enabled_flag, dest=destination, action="store_true", help=enabled_help)
    group.add_argument(disabled_flag, dest=destination, action="store_false", help=disabled_help)
    parser.set_defaults(**{destination: default})


def _add_shared_arguments(parser: argparse.ArgumentParser, config: dict[str, Any]) -> None:
    logging_settings = LoggingSettings.from_application(config)
    adaptive_config = config.get("adaptiveGeneration", {})
    initial_tokens = int(config.get("maxNewTokens", 1024))
    initial_side = int(config.get("maxImageSide", 1024))
    adaptive = AdaptiveGenerationPolicy.from_mapping(
        adaptive_config if isinstance(adaptive_config, Mapping) else {},
        initial_max_new_tokens=initial_tokens,
        initial_max_image_side=initial_side,
    )

    parser.add_argument("-i", "--input", dest="input_path", default=".", help="Image file or directory to process.")
    parser.add_argument("--config", default=config.get("_configPath", ""), help="Alternative Artifex settings JSON file.")
    parser.add_argument("--recursive", action="store_true", help="Recursively scan directories.")
    parser.add_argument("--max-files", type=int, default=0, help="Maximum images to process; 0 means all.")
    parser.add_argument("--model", default=config.get("model", "Qwen/Qwen3.5-4B"), help="Hugging Face model ID or local model directory.")
    parser.add_argument("--model-cache-dir", default=config.get("modelCacheDir", ""), help="Optional Hugging Face model cache directory.")
    _add_boolean_pair(
        parser,
        destination="local_files_only",
        enabled_flag="--local-files-only",
        disabled_flag="--allow-model-downloads",
        default=_bool_default(config, "localFilesOnly", False),
        enabled_help="Require a complete local model/cache and never resolve model files online.",
        disabled_help="Allow Hugging Face to resolve missing model files.",
    )
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default=config.get("device", "auto"))
    parser.add_argument("--precision", choices=("auto", "bfloat16", "float16", "float32"), default=config.get("precision", "auto"))
    parser.add_argument("--attention", choices=("auto", "sdpa", "eager", "flash_attention_2"), default=config.get("attention", "sdpa"))
    parser.add_argument("--quantization", choices=("none", "8bit", "4bit"), default=config.get("quantization", "none"))
    parser.add_argument("--llm-int8-threshold", type=float, default=float(config.get("llmInt8Threshold", 6.0)))
    _add_boolean_pair(
        parser,
        destination="compile_model",
        enabled_flag="--compile-model",
        disabled_flag="--no-compile-model",
        default=_bool_default(config, "compileModel", False),
        enabled_help="Attempt torch.compile after model loading.",
        disabled_help="Keep the default uncompiled model runtime.",
    )
    parser.add_argument("--max-new-tokens", type=int, default=initial_tokens)
    parser.add_argument("--max-inference-seconds", type=float, default=float(config.get("maxInferenceSeconds", 180.0)))
    parser.add_argument(
        "--max-image-side",
        type=int,
        default=initial_side,
        help="Inference-only maximum image side; source pixels are never modified.",
    )
    _add_boolean_pair(
        parser,
        destination="adaptive_generation",
        enabled_flag="--adaptive-generation",
        disabled_flag="--no-adaptive-generation",
        default=adaptive.enabled,
        enabled_help="Adapt token/image budgets on bounded structured-output retries.",
        disabled_help="Repeat with fixed generation budgets.",
    )
    parser.add_argument("--max-new-tokens-ceiling", type=int, default=adaptive.max_new_tokens_ceiling)
    parser.add_argument("--token-growth-factor", type=float, default=adaptive.token_growth_factor)
    parser.add_argument("--minimum-image-side", type=int, default=adaptive.minimum_image_side)
    parser.add_argument("--image-reduction-factor", type=float, default=adaptive.image_reduction_factor)
    parser.add_argument("--temperature", type=float, default=float(config.get("temperature", 0.2)))
    parser.add_argument("--top-p", type=float, default=float(config.get("topP", 0.8)))
    parser.add_argument("--top-k", type=int, default=int(config.get("topK", 20)))
    _add_boolean_pair(
        parser,
        destination="sampling",
        enabled_flag="--sampling",
        disabled_flag="--no-sampling",
        default=_bool_default(config, "sampling", False),
        enabled_help="Enable stochastic generation.",
        disabled_help="Use deterministic greedy generation.",
    )
    parser.add_argument("--seed", type=int, default=int(config.get("seed", 42)))
    parser.add_argument("--batch-size", type=int, default=int(config.get("batchSize", 1)))
    parser.add_argument("--delay", dest="delay_ms", type=int, default=int(config.get("delayMs", 0)))
    parser.add_argument("--retries", type=int, default=int(config.get("retries", 2)))
    parser.add_argument("--retry-delay-seconds", type=float, default=float(config.get("retryDelaySeconds", 1.0)))
    parser.add_argument("--hint", default="", help="Optional context hint for ambiguous classification/analysis.")
    parser.add_argument("--prompt", default="", help="Optional guidance applied only to the generated transformation prompt.")
    _add_boolean_pair(
        parser,
        destination="prefer_comments_prompt",
        enabled_flag="--prefer-comments-prompt",
        disabled_flag="--no-prefer-comments-prompt",
        default=_bool_default(config, "preferCommentsPrompt", True),
        enabled_help="Prefer Comments/XPComment text before image inference.",
        disabled_help="Prefer image inference before Comments text.",
    )
    _add_boolean_pair(
        parser,
        destination="default_capture_metadata",
        enabled_flag="--default-capture-metadata",
        disabled_flag="--no-default-capture-metadata",
        default=_bool_default(config, "defaultCaptureMetadata", True),
        enabled_help="Populate configured capture tags only when missing.",
        disabled_help="Do not populate default capture tags.",
    )
    parser.add_argument("--force", action="store_true", help="Ignore reusable AI metadata/cache and regenerate.")
    parser.add_argument("--output-root", default="", help="Organize destination root; default is <input>/Organized.")
    parser.add_argument("--bucket-threshold", type=int, default=int(config.get("bucketThreshold", 10)))
    parser.add_argument("--bucket-size", type=int, default=int(config.get("bucketSize", 250)))
    parser.add_argument("--min-confidence", type=float, default=float(config.get("minConfidence", 0.75)))
    _add_boolean_pair(
        parser,
        destination="analyze_missing",
        enabled_flag="--analyze-missing",
        disabled_flag="--no-analyze-missing",
        default=_bool_default(config, "analyzeMissing", True),
        enabled_help="Use local Qwen when folderClassification is missing.",
        disabled_help="Do not infer missing folder classifications.",
    )
    resolved_paths = config.get("_resolvedPaths", {})
    parser.add_argument(
        "--categories-file",
        default=resolved_paths.get("categoriesFile", str(DEFAULT_CATEGORIES_PATH)),
        help="Marketplace taxonomy used only by Prompts.",
    )
    _add_boolean_pair(
        parser,
        destination="inference_cache",
        enabled_flag="--inference-cache",
        disabled_flag="--no-inference-cache",
        default=_bool_default(config, "inferenceCache", True),
        enabled_help="Enable persistent content-addressed inference caching.",
        disabled_help="Disable persistent inference caching.",
    )
    parser.add_argument("--cache-file", default="")
    parser.add_argument("--state-file", default="")
    parser.add_argument("--exiftool-path", default=config.get("exifToolPath", "exiftool"))
    parser.add_argument("--metadata-attempts", type=int, default=int(config.get("metadataAttempts", 3)))
    parser.add_argument("--metadata-timeout-seconds", type=int, default=int(config.get("metadataTimeoutSeconds", 45)))
    parser.add_argument("--preview", action="store_true", help="No model invocation, metadata writes, or moves.")
    parser.add_argument("--dry-run", action="store_true", help="Direct-Python alias for --preview.")
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        default=logging_settings.level,
    )
    parser.add_argument("--log-path", default=logging_settings.path)
    color_group = parser.add_mutually_exclusive_group()
    color_group.add_argument("--color", dest="color_mode", action="store_const", const="always")
    color_group.add_argument("--no-color", dest="color_mode", action="store_const", const="never")
    parser.set_defaults(color_mode=logging_settings.color)

    folder_config = config.get("folderClassification")
    if not isinstance(folder_config, Mapping):
        raise ValueError("Configuration application.folderClassification must be a JSON object.")
    parser.set_defaults(
        _application_config=config,
        _analysis_prompt_path=resolved_paths.get("analysisPromptFile", str(DEFAULT_ANALYSIS_PROMPT_PATH)),
        _folder_prompt_path=resolved_paths.get("folderPromptFile", str(DEFAULT_FOLDER_PROMPT_PATH)),
        _folder_min_levels=int(folder_config["minLevels"]),
        _folder_max_levels=int(folder_config["maxLevels"]),
        _fail_on_prohibited_folder=require_bool(folder_config["failOnProhibitedFolder"], "application.folderClassification.failOnProhibitedFolder"),
        _fail_on_low_confidence=require_bool(folder_config["failOnLowConfidence"], "application.folderClassification.failOnLowConfidence"),
        _fail_on_needs_review=require_bool(folder_config["failOnNeedsReview"], "application.folderClassification.failOnNeedsReview"),
        _state_directory_name=str(config.get("stateDirectoryName", "states")),
        _capture_metadata_values=dict(config.get("defaultCaptureMetadataValues", {})),
    )


def build_parser(config: dict[str, Any]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qwen-archive",
        description="Local-Qwen image metadata and archival organization CLI.",
    )
    parser.add_argument("--version", action="version", version=f"qwen-image-archive-cli {VERSION}")
    subparsers = parser.add_subparsers(dest="task", required=True)
    for task, description in (
        ("prompts", "Generate/reuse complete painting metadata using local Qwen."),
        ("organize", "Move images using metadata-first folder classification."),
    ):
        child = subparsers.add_parser(task, description=description)
        _add_shared_arguments(child, config)
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.max_files < 0:
        raise ValueError("--max-files must be >= 0.")
    if args.batch_size < 1:
        raise ValueError("--batch-size must be >= 1.")
    if args.delay_ms < 0:
        raise ValueError("--delay must be >= 0.")
    if not 0 <= args.retries <= 10:
        raise ValueError("--retries must be between 0 and 10.")
    if args.retry_delay_seconds < 0:
        raise ValueError("--retry-delay-seconds must be >= 0.")
    if args.max_new_tokens < 32:
        raise ValueError("--max-new-tokens must be >= 32.")
    if args.max_new_tokens_ceiling < args.max_new_tokens:
        raise ValueError("--max-new-tokens-ceiling must be >= --max-new-tokens.")
    if not 1.01 <= args.token_growth_factor <= 8.0:
        raise ValueError("--token-growth-factor must be between 1.01 and 8.0.")
    if not 0.1 <= args.image_reduction_factor < 1.0:
        raise ValueError("--image-reduction-factor must be >= 0.1 and < 1.0.")
    if args.max_image_side < 0:
        raise ValueError("--max-image-side must be >= 0.")
    if args.minimum_image_side < 64:
        raise ValueError("--minimum-image-side must be >= 64.")
    if args.max_image_side > 0 and args.minimum_image_side > args.max_image_side:
        raise ValueError("--minimum-image-side must be <= --max-image-side.")
    if args.max_inference_seconds < 0 or (0 < args.max_inference_seconds < 10):
        raise ValueError("--max-inference-seconds must be 0 or at least 10 seconds.")
    if args.bucket_threshold < 0 or args.bucket_size < 1:
        raise ValueError("Bucket threshold must be >= 0 and bucket size >= 1.")
    if not 0.0 <= args.min_confidence <= 1.0:
        raise ValueError("--min-confidence must be between 0 and 1.")
    if args.metadata_attempts < 1 or args.metadata_attempts > 10:
        raise ValueError("--metadata-attempts must be between 1 and 10.")
    if args.metadata_timeout_seconds < 5 or args.metadata_timeout_seconds > 3600:
        raise ValueError("--metadata-timeout-seconds must be between 5 and 3600.")
    if args.llm_int8_threshold < 0:
        raise ValueError("--llm-int8-threshold must be >= 0.")
    if args.quantization != "none" and args.device == "cpu":
        raise ValueError("4bit/8bit bitsandbytes quantization cannot be combined with --device cpu.")


def _state_paths(args: argparse.Namespace, input_path: Path) -> tuple[Path, Path]:
    state_dir = default_state_dir(
        input_path,
        state_directory_name=args._state_directory_name,
    )
    state_file = (
        Path(args.state_file).expanduser().resolve()
        if args.state_file
        else state_dir / f"{args.task}-state.json"
    )
    cache_file = (
        Path(args.cache_file).expanduser().resolve()
        if args.cache_file
        else state_dir / "inference-cache.sqlite3"
    )
    return state_file, cache_file


def _write_state(path: Path, task: str, summary: dict[str, int], args: argparse.Namespace) -> None:
    if args.preview or args.dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": 1,
        "task": task,
        "input": str(Path(args.input_path).expanduser().resolve()),
        "model": args.model,
        "summary": summary,
    }
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temporary = Path(temp_name)
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _PreviewMetadata:
    """Prompts preview never dereferences metadata; this makes that invariant explicit."""

    def __getattr__(self, name: str):
        raise RuntimeError(f"Preview attempted unexpected metadata operation: {name}")


def run(args: argparse.Namespace) -> int:
    _validate_args(args)
    args.preview = bool(args.preview or args.dry_run)
    input_path = Path(args.input_path).expanduser().resolve()
    state_file, cache_file = _state_paths(args, input_path)
    logger = configure_logging(args.log_level, args.log_path, args.color_mode)
    logger.info("Starting %s task.", args.task)
    logger.info("Input: %s", input_path)
    logger.info("State: %s", state_file)
    logger.info(
        "Inference cache: %s (%s)",
        cache_file,
        "enabled" if args.inference_cache else "disabled",
    )

    folder_policy = FolderValidationPolicy(
        min_levels=args._folder_min_levels,
        max_levels=args._folder_max_levels,
        min_confidence=args.min_confidence,
        fail_on_prohibited_folder=args._fail_on_prohibited_folder,
        fail_on_low_confidence=args._fail_on_low_confidence,
        fail_on_needs_review=args._fail_on_needs_review,
    )
    taxonomy = CategoryTaxonomy(Path(args.categories_file)) if args.task == "prompts" else None
    prompt_library = PromptLibrary.load(
        Path(args._analysis_prompt_path),
        Path(args._folder_prompt_path),
        taxonomy,
        folder_min_levels=folder_policy.min_levels,
        folder_max_levels=folder_policy.max_levels,
    )

    metadata = _PreviewMetadata() if args.task == "prompts" and args.preview else ExifToolService(
        args.exiftool_path,
        timeout_seconds=args.metadata_timeout_seconds,
        capture_metadata=args._capture_metadata_values,
        logger=logger,
    )
    adaptive_policy = AdaptiveGenerationPolicy(
        enabled=args.adaptive_generation,
        max_new_tokens_ceiling=args.max_new_tokens_ceiling,
        token_growth_factor=args.token_growth_factor,
        minimum_image_side=args.minimum_image_side,
        image_reduction_factor=args.image_reduction_factor,
    )
    engine_settings = EngineSettings.from_application(args._application_config).with_overrides(
        model_name=args.model,
        model_cache_dir=args.model_cache_dir,
        local_files_only=args.local_files_only,
        device=args.device,
        precision=args.precision,
        attention=args.attention,
        quantization=args.quantization,
        llm_int8_threshold=args.llm_int8_threshold,
        compile_model=args.compile_model,
        max_new_tokens=args.max_new_tokens,
        max_inference_seconds=args.max_inference_seconds,
        max_image_side=args.max_image_side,
        temperature=args.temperature,
        top_p=args.top_p,
        top_k=args.top_k,
        sampling=args.sampling,
        seed=args.seed,
        adaptive_policy=adaptive_policy,
    )
    engine = engine_settings.build(logger)
    cache = InferenceCache(cache_file, enabled=args.inference_cache)
    subject_merger = SubjectMergeService()
    try:
        if args.task == "prompts":
            assert taxonomy is not None
            task = PromptsTask(
                input_path=input_path,
                recursive=args.recursive,
                max_files=args.max_files,
                force=args.force,
                preview=args.preview,
                hint=args.hint,
                custom_prompt=args.prompt,
                prefer_comments_prompt=args.prefer_comments_prompt,
                default_capture_metadata=args.default_capture_metadata,
                batch_size=args.batch_size,
                delay_ms=args.delay_ms,
                retries=args.retries,
                retry_delay_seconds=args.retry_delay_seconds,
                metadata_attempts=args.metadata_attempts,
                folder_policy=folder_policy,
                taxonomy=taxonomy,
                prompt_library=prompt_library,
                metadata=metadata,
                engine=engine,
                cache=cache,
                subject_merger=subject_merger,
                logger=logger,
            )
        else:
            input_root = input_path.parent if input_path.is_file() else input_path
            output_root = (
                Path(args.output_root).expanduser().resolve()
                if args.output_root
                else input_root / "Organized"
            )
            task = OrganizeTask(
                input_path=input_path,
                output_root=output_root,
                recursive=args.recursive,
                max_files=args.max_files,
                force=args.force,
                preview=args.preview,
                hint=args.hint,
                prefer_comments_prompt=args.prefer_comments_prompt,
                analyze_missing=args.analyze_missing,
                retries=args.retries,
                retry_delay_seconds=args.retry_delay_seconds,
                metadata_attempts=args.metadata_attempts,
                folder_policy=folder_policy,
                bucket_threshold=args.bucket_threshold,
                bucket_size=args.bucket_size,
                prompt_library=prompt_library,
                metadata=metadata,
                engine=engine,
                cache=cache,
                subject_merger=subject_merger,
                logger=logger,
            )
        summary = task.run()
        _write_state(state_file, args.task, summary, args)
        logger.info("Summary: %s", json.dumps(summary, sort_keys=True))
        return 1 if summary.get("failed", 0) > 0 or summary.get("unresolved", 0) > 0 else 0
    finally:
        cache.close()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        config = load_json_config(_prescan_config(arguments))
        parser = build_parser(config)
        args = parser.parse_args(arguments)
        return run(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # CLI error boundary
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
