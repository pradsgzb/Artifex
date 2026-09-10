"""Lazy local-Qwen multimodal inference with bounded adaptive recovery."""

from __future__ import annotations

import contextlib
import gc
import io
import logging
import random
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TypeVar

from .errors import IncompleteJsonError, InferenceError, InferenceTimeoutError
from .jsonutil import parse_json_response
from .settings import AdaptiveGenerationPolicy

T = TypeVar("T")


def _complete_top_level_json(text: str) -> bool:
    """Return true after a complete top-level JSON object has been generated."""
    start = text.find("{")
    if start < 0:
        return False
    depth = 0
    in_string = False
    escape = False
    for char in text[start:]:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return True
            if depth < 0:
                return False
    return False


class _JsonObjectStoppingCriteria:
    """Stop deterministic structured generation after the first complete object."""

    def __init__(self, tokenizer: Any, prompt_length: int):
        self.tokenizer = tokenizer
        self.prompt_length = int(prompt_length)
        self.completed = False

    def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> bool:
        del scores, kwargs
        if self.completed:
            return True
        if int(input_ids.shape[-1]) <= self.prompt_length:
            return False
        text = self.tokenizer.decode(
            input_ids[0, self.prompt_length :],
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        self.completed = _complete_top_level_json(text)
        return self.completed


class _GenerationProgress:
    """Transformers streamer that emits a low-noise heartbeat for long requests."""

    def __init__(self, logger: logging.Logger, *, interval_seconds: float = 15.0):
        self.logger = logger
        self.interval_seconds = max(5.0, float(interval_seconds))
        self.started = time.monotonic()
        self.tokens = 0
        self._first_put = True
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._heartbeat,
            name="qwen-generation-progress",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def put(self, value: Any) -> None:
        if self._first_put:
            self._first_put = False
            return
        try:
            self.tokens += int(value.numel())
        except Exception:
            self.tokens += 1

    def end(self) -> None:
        pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            elapsed = max(0.001, time.monotonic() - self.started)
            self.logger.info(
                "Qwen generation still running: %.0fs elapsed, %d output token(s), %.2f token/s.",
                elapsed,
                self.tokens,
                self.tokens / elapsed,
            )


@contextlib.contextmanager
def _suppress_known_transformers_doc_lint(logger: logging.Logger):
    """Suppress one known non-fatal Transformers development-build diagnostic."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        yield
    for line in captured.getvalue().splitlines():
        text = line.strip()
        known = (
            text.startswith("[ERROR] `")
            and "but not documented. Make sure to add it to the docstring" in text
        )
        if known:
            logger.debug("Suppressed known Transformers auto-docstring diagnostic: %s", text)
        elif text:
            print(line, file=sys.stdout, flush=True)


class QwenEngine:
    """Single-process, model-resident Qwen multimodal inference adapter."""

    def __init__(
        self,
        *,
        model_name: str,
        model_cache_dir: str = "",
        local_files_only: bool = False,
        device: str = "auto",
        precision: str = "auto",
        attention: str = "sdpa",
        quantization: str = "none",
        llm_int8_threshold: float = 6.0,
        compile_model: bool = False,
        max_new_tokens: int = 1024,
        max_inference_seconds: float = 180.0,
        max_image_side: int = 1024,
        temperature: float = 0.2,
        top_p: float = 0.8,
        top_k: int = 20,
        sampling: bool = False,
        seed: int = 42,
        adaptive_policy: AdaptiveGenerationPolicy | None = None,
        logger: logging.Logger | None = None,
    ):
        self.model_name = str(model_name)
        self.model_cache_dir = str(model_cache_dir).strip() or None
        self.local_files_only = bool(local_files_only)
        self.device_option = str(device)
        self.precision_option = str(precision)
        self.attention = str(attention)
        self.quantization = str(quantization)
        self.llm_int8_threshold = max(0.0, float(llm_int8_threshold))
        self.compile_model = bool(compile_model)
        self.max_new_tokens = max(32, int(max_new_tokens))
        self.max_inference_seconds = max(0.0, float(max_inference_seconds))
        self.max_image_side = max(0, int(max_image_side))
        self.temperature = max(0.0, float(temperature))
        self.top_p = min(1.0, max(0.01, float(top_p)))
        self.top_k = max(0, int(top_k))
        self.sampling = bool(sampling)
        self.seed = int(seed)
        self.adaptive_policy = adaptive_policy or AdaptiveGenerationPolicy.from_mapping(
            {},
            initial_max_new_tokens=self.max_new_tokens,
            initial_max_image_side=self.max_image_side,
        )
        self.logger = logger or logging.getLogger("qwen_archive")
        self.model: Any | None = None
        self.processor: Any | None = None
        self.torch: Any | None = None
        self._resolved_dtype: Any | None = None
        self._device: str | None = None
        self._load_lock = threading.RLock()

    @property
    def loaded(self) -> bool:
        return self.model is not None and self.processor is not None

    @property
    def cache_identity(self) -> str:
        policy = self.adaptive_policy
        return (
            f"model={self.model_name}|image={self.max_image_side}|tokens={self.max_new_tokens}|"
            f"sampling={int(self.sampling)}|temperature={self.temperature:.6g}|top_p={self.top_p:.6g}|"
            f"top_k={self.top_k}|seed={self.seed}|precision={self.precision_option}|"
            f"quantization={self.quantization}|int8threshold={self.llm_int8_threshold:.6g}|"
            f"attention={self.attention}|adaptive={int(policy.enabled)}:{policy.max_new_tokens_ceiling}:"
            f"{policy.token_growth_factor:.6g}:{policy.minimum_image_side}:{policy.image_reduction_factor:.6g}"
        )

    def load(self) -> None:
        if self.loaded:
            return
        with self._load_lock:
            if self.loaded:
                return
            try:
                import torch
            except ImportError as exc:
                raise RuntimeError(
                    "Qwen runtime dependencies are missing. Run setup.ps1/setup.sh or install requirements.txt."
                ) from exc

            logging.getLogger("torch.utils.flop_counter").setLevel(logging.ERROR)
            try:
                with _suppress_known_transformers_doc_lint(self.logger):
                    from transformers import AutoModelForMultimodalLM, AutoProcessor
            except ImportError as exc:
                raise RuntimeError(
                    "Qwen runtime dependencies are missing. Run setup.ps1/setup.sh or install requirements.txt."
                ) from exc

            self.torch = torch
            self._configure_torch(torch)
            dtype = self._resolve_dtype(torch)
            self._resolved_dtype = dtype

            model_kwargs: dict[str, Any] = {
                "cache_dir": self.model_cache_dir,
                "local_files_only": self.local_files_only,
                "low_cpu_mem_usage": True,
            }
            if self.attention != "auto":
                model_kwargs["attn_implementation"] = self.attention

            if self.quantization in {"4bit", "8bit"}:
                if not torch.cuda.is_available():
                    raise RuntimeError(
                        f"{self.quantization} quantization was requested but CUDA is not available to PyTorch."
                    )
                try:
                    from transformers import BitsAndBytesConfig
                except ImportError as exc:
                    raise RuntimeError("bitsandbytes/transformers quantization support is not installed.") from exc
                if self.quantization == "4bit":
                    model_kwargs["quantization_config"] = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_compute_dtype=dtype,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_use_double_quant=True,
                    )
                else:
                    model_kwargs["quantization_config"] = BitsAndBytesConfig(
                        load_in_8bit=True,
                        llm_int8_threshold=self.llm_int8_threshold,
                    )
                model_kwargs["device_map"] = self._device_map(torch)
                model_kwargs["dtype"] = dtype
            else:
                model_kwargs["device_map"] = self._device_map(torch)
                model_kwargs["dtype"] = dtype

            quant_text = self.quantization
            if self.quantization == "8bit":
                quant_text += f" threshold={self.llm_int8_threshold:g}"
            self.logger.info(
                "Loading model %s (device=%s precision=%s quantization=%s attention=%s)...",
                self.model_name,
                self.device_option,
                self.precision_option,
                quant_text,
                self.attention,
            )
            try:
                model = AutoModelForMultimodalLM.from_pretrained(self.model_name, **model_kwargs)
            except TypeError as exc:
                if "dtype" not in str(exc) or "dtype" not in model_kwargs:
                    raise
                model_kwargs["torch_dtype"] = model_kwargs.pop("dtype")
                model = AutoModelForMultimodalLM.from_pretrained(self.model_name, **model_kwargs)

            with _suppress_known_transformers_doc_lint(self.logger):
                processor = AutoProcessor.from_pretrained(
                    self.model_name,
                    cache_dir=self.model_cache_dir,
                    local_files_only=self.local_files_only,
                )
            model.eval()
            if self.compile_model:
                try:
                    self.logger.info("Compiling model with torch.compile(mode='reduce-overhead')...")
                    model = torch.compile(model, mode="reduce-overhead", fullgraph=False)
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("torch.compile unavailable/failed; continuing uncompiled: %s", exc)
            self.model = model
            self.processor = processor
            self.logger.info("Qwen model is resident and ready.")

    def release(self) -> None:
        """Release resident model references; primarily useful for controlled shutdown/tests."""
        with self._load_lock:
            self.model = None
            self.processor = None
            if self.torch is not None and self.torch.cuda.is_available():
                try:
                    self.torch.cuda.empty_cache()
                except Exception:
                    pass
            gc.collect()

    def _configure_torch(self, torch: Any) -> None:
        random.seed(self.seed)
        torch.manual_seed(self.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(self.seed)
            torch.backends.cuda.matmul.allow_tf32 = True
            if hasattr(torch.backends, "cudnn"):
                torch.backends.cudnn.allow_tf32 = True
                torch.backends.cudnn.benchmark = True
            try:
                torch.set_float32_matmul_precision("high")
            except Exception:
                pass

    def _device_map(self, torch: Any) -> Any:
        option = self.device_option.casefold()
        if option == "cpu":
            self._device = "cpu"
            return {"": "cpu"}
        if option == "cuda":
            if not torch.cuda.is_available():
                raise RuntimeError("--device cuda was requested but CUDA is not available to PyTorch.")
            self._device = "cuda:0"
            return {"": 0}
        if option != "auto":
            raise ValueError(f"Unsupported device: {self.device_option}")
        if torch.cuda.is_available():
            self._device = "cuda:0"
            return "auto"
        self._device = "cpu"
        return {"": "cpu"}

    def _resolve_dtype(self, torch: Any) -> Any:
        option = self.precision_option.casefold()
        if self.quantization == "8bit":
            if option not in {"auto", "float16", "bfloat16", "float32"}:
                raise ValueError(f"Unsupported precision: {self.precision_option}")
            if option != "float16":
                self.logger.info(
                    "8-bit bitsandbytes inference uses FP16 activations; overriding precision=%s to float16.",
                    self.precision_option,
                )
            return torch.float16
        if option == "float32":
            return torch.float32
        if option == "float16":
            return torch.float16
        if option == "bfloat16":
            return torch.bfloat16
        if option != "auto":
            raise ValueError(f"Unsupported precision: {self.precision_option}")
        if torch.cuda.is_available():
            try:
                if torch.cuda.is_bf16_supported():
                    return torch.bfloat16
            except Exception:
                pass
            return torch.float16
        return torch.float32

    def _prepare_image(self, image_path: Path, max_image_side: int):
        from PIL import Image, ImageOps

        with Image.open(image_path) as source:
            oriented = ImageOps.exif_transpose(source)
            try:
                image = oriented.convert("RGB")
                image.load()
            finally:
                if oriented is not source:
                    oriented.close()
        if max_image_side > 0 and max(image.size) > max_image_side:
            resized = image.copy()
            resized.thumbnail((max_image_side, max_image_side), Image.Resampling.LANCZOS)
            image.close()
            image = resized
        return image

    @staticmethod
    def _text_content(text: str) -> list[dict[str, str]]:
        return [{"type": "text", "text": text}]

    def _build_messages(
        self,
        *,
        system_prompt: str,
        user_text: str,
        image: Any | None,
        history: Sequence[Mapping[str, str]],
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if system_prompt.strip():
            messages.append({"role": "system", "content": self._text_content(system_prompt)})
        for item in history:
            role = str(item.get("role", "")).casefold()
            content = str(item.get("content", ""))
            if role not in {"user", "assistant"}:
                raise ValueError("Chat history roles must be user or assistant.")
            if content:
                messages.append({"role": role, "content": self._text_content(content)})
        current: list[dict[str, Any]] = []
        if image is not None:
            current.append({"type": "image", "image": image})
        current.append({"type": "text", "text": user_text})
        messages.append({"role": "user", "content": current})
        return messages

    def generate(
        self,
        *,
        system_prompt: str,
        user_text: str,
        image_path: Path | None = None,
        history: Sequence[Mapping[str, str]] = (),
        max_new_tokens: int | None = None,
        max_image_side: int | None = None,
        response_format: str = "text",
    ) -> str:
        self.load()
        assert self.model is not None and self.processor is not None and self.torch is not None
        response_format = response_format.casefold()
        if response_format not in {"text", "json"}:
            raise ValueError("response_format must be text or json.")
        token_budget = max(32, int(max_new_tokens or self.max_new_tokens))
        image_side = self.max_image_side if max_image_side is None else max(0, int(max_image_side))
        torch = self.torch
        image = None
        try:
            if image_path is not None:
                image = self._prepare_image(Path(image_path), image_side)
            messages = self._build_messages(
                system_prompt=system_prompt,
                user_text=user_text,
                image=image,
                history=history,
            )
            template_kwargs: dict[str, Any] = {
                "add_generation_prompt": True,
                "tokenize": True,
                "return_dict": True,
                "return_tensors": "pt",
                "enable_thinking": False,
            }
            try:
                inputs = self.processor.apply_chat_template(messages, **template_kwargs)
            except TypeError:
                template_kwargs.pop("enable_thinking", None)
                inputs = self.processor.apply_chat_template(messages, **template_kwargs)
            model_device = next(self.model.parameters()).device
            inputs = inputs.to(model_device)
            input_length = int(inputs["input_ids"].shape[-1])
            generation_kwargs: dict[str, Any] = {
                "max_new_tokens": token_budget,
                "do_sample": self.sampling,
                "use_cache": True,
                "disable_compile": not self.compile_model,
                "pad_token_id": getattr(self.processor.tokenizer, "eos_token_id", None),
            }
            if self.max_inference_seconds > 0:
                generation_kwargs["max_time"] = self.max_inference_seconds
            if self.sampling:
                generation_kwargs.update(
                    temperature=max(self.temperature, 1e-5),
                    top_p=self.top_p,
                    top_k=self.top_k,
                )

            progress = _GenerationProgress(self.logger, interval_seconds=15.0)
            generation_kwargs["streamer"] = progress
            json_stop = None
            if response_format == "json" and not self.sampling:
                try:
                    from transformers import StoppingCriteriaList

                    json_stop = _JsonObjectStoppingCriteria(self.processor.tokenizer, input_length)
                    generation_kwargs["stopping_criteria"] = StoppingCriteriaList([json_stop])
                except Exception:
                    json_stop = None

            if torch.cuda.is_available():
                try:
                    torch.cuda.synchronize()
                    torch.cuda.reset_peak_memory_stats()
                except Exception:
                    pass
            image_size = f"{image.width}x{image.height}" if image is not None else "none"
            limit_text = (
                f"{self.max_inference_seconds:.0f}s"
                if self.max_inference_seconds > 0
                else "disabled"
            )
            self.logger.info(
                "Starting Qwen generation (format=%s image=%s inputTokens=%d maxNewTokens=%d timeLimit=%s)...",
                response_format,
                image_size,
                input_length,
                token_budget,
                limit_text,
            )
            started = time.monotonic()
            progress.start()
            try:
                with torch.inference_mode():
                    generated = self.model.generate(**inputs, **generation_kwargs)
                if torch.cuda.is_available():
                    try:
                        torch.cuda.synchronize()
                    except Exception:
                        pass
            finally:
                progress.stop()
            elapsed = max(0.001, time.monotonic() - started)
            new_tokens = generated[:, input_length:]
            output_tokens = int(new_tokens.shape[-1])
            peak_text = ""
            if torch.cuda.is_available():
                try:
                    peak_gb = torch.cuda.max_memory_allocated() / (1024**3)
                    peak_text = f", peak CUDA allocated={peak_gb:.2f} GB"
                except Exception:
                    pass
            self.logger.info(
                "Qwen generation completed: %d token(s) in %.1fs (%.2f token/s)%s.",
                output_tokens,
                elapsed,
                output_tokens / elapsed,
                peak_text,
            )

            eos_ids = getattr(self.processor.tokenizer, "eos_token_id", None)
            if eos_ids is None:
                eos_set: set[int] = set()
            elif isinstance(eos_ids, (list, tuple, set)):
                eos_set = {int(value) for value in eos_ids}
            else:
                eos_set = {int(eos_ids)}
            last_token = int(generated[0, -1].item()) if generated.numel() else -1
            stopped_on_json = bool(json_stop is not None and json_stop.completed)
            if stopped_on_json:
                self.logger.debug("Stopped generation after complete top-level JSON.")
            if (
                self.max_inference_seconds > 0
                and elapsed >= self.max_inference_seconds * 0.98
                and last_token not in eos_set
                and not stopped_on_json
            ):
                raise InferenceTimeoutError(
                    f"Qwen generation exceeded the {self.max_inference_seconds:.0f}s limit "
                    f"after producing {output_tokens} token(s)."
                )
            if output_tokens >= token_budget and last_token not in eos_set and not stopped_on_json:
                self.logger.warning(
                    "Qwen generation reached maxNewTokens=%d before EOS.",
                    token_budget,
                )
            return self.processor.batch_decode(new_tokens, skip_special_tokens=True)[0].strip()
        except RuntimeError as exc:
            if "out of memory" in str(exc).casefold() and torch.cuda.is_available():
                torch.cuda.empty_cache()
                gc.collect()
                raise InferenceError(
                    "CUDA out of memory during Qwen inference; adaptive recovery may retry with a smaller in-memory image."
                ) from exc
            raise
        finally:
            if image is not None:
                image.close()

    def generate_validated(
        self,
        *,
        system_prompt: str,
        user_text: str,
        image_path: Path | None,
        validator: Callable[[Any], T],
        retries: int,
        retry_delay_seconds: float,
    ) -> tuple[T, str]:
        """Generate typed JSON using bounded, failure-aware recovery.

        Incomplete JSON grows the output budget. Repeated truncation, timeout,
        or memory pressure can reduce only the in-memory inference copy of the
        image. The source file is never rewritten.
        """
        errors: list[str] = []
        current_user_text = user_text
        token_budget = self.max_new_tokens
        image_side = self.max_image_side
        total_attempts = max(0, int(retries)) + 1
        last_raw = ""

        for attempt in range(1, total_attempts + 1):
            try:
                last_raw = self.generate(
                    system_prompt=system_prompt,
                    user_text=current_user_text,
                    image_path=image_path,
                    max_new_tokens=token_budget,
                    max_image_side=image_side,
                    response_format="json",
                )
                parsed = parse_json_response(last_raw)
                return validator(parsed), last_raw
            except Exception as exc:  # bounded application boundary
                errors.append(f"attempt {attempt}: {exc}")
                if attempt >= total_attempts:
                    break

                previous_tokens = token_budget
                previous_side = image_side
                is_incomplete = isinstance(exc, IncompleteJsonError)
                is_pressure = isinstance(exc, (InferenceTimeoutError, InferenceError)) or "out of memory" in str(exc).casefold()

                if is_incomplete:
                    token_budget = self.adaptive_policy.next_token_budget(token_budget)
                    # On repeated truncation—or when token growth has reached its
                    # ceiling—reduce visual-token pressure as a second bounded lever.
                    if image_path is not None and (attempt >= 2 or token_budget == previous_tokens):
                        image_side = self.adaptive_policy.next_image_side(image_side)
                elif is_pressure and image_path is not None:
                    image_side = self.adaptive_policy.next_image_side(image_side)

                if token_budget != previous_tokens:
                    self.logger.warning(
                        "Structured response was incomplete; retry %d/%d increases maxNewTokens from %d to %d.",
                        attempt + 1,
                        total_attempts,
                        previous_tokens,
                        token_budget,
                    )
                if image_side != previous_side:
                    self.logger.warning(
                        "Retry %d/%d reduces the in-memory inference image from max side %d to %d; source file is unchanged.",
                        attempt + 1,
                        total_attempts,
                        previous_side,
                        image_side,
                    )

                current_user_text = (
                    user_text
                    + "\n\nYOUR PREVIOUS RESPONSE FAILED VALIDATION:\n"
                    + str(exc)
                    + "\nReturn one complete corrected JSON object only. Do not explain the correction."
                )
                if retry_delay_seconds > 0:
                    time.sleep(float(retry_delay_seconds))

        raise ValueError("Local Qwen failed validation: " + " | ".join(errors))
