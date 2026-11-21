"""Asynchronous client for interacting with the local Ollama instance."""
import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from app.config.settings import Settings, get_settings
from app.schemas.models import ModelInfo

logger = logging.getLogger(__name__)


class OllamaClientError(Exception):
    """Base exception for Ollama client errors."""
    pass


class OllamaConnectionError(OllamaClientError):
    """Raised when Ollama daemon cannot be reached."""
    pass


class OllamaTimeoutError(OllamaClientError):
    """Raised when an Ollama request times out."""
    pass


class OllamaClient:
    """Async client interfacing with the Ollama HTTP REST API."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.base_url = self.settings.ollama_base_url.rstrip("/")

    @property
    def timeout(self) -> float:
        """Dynamic timeout reflecting runtime settings."""
        return self.settings.request_timeout_seconds

    def _get_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)

    async def check_health(self) -> tuple[bool, str | None, str | None]:
        """
        Check if Ollama server is responding and retrieve its version.
        
        Returns:
            Tuple of (connected: bool, version: Optional[str], error: Optional[str])
        """
        try:
            async with self._get_client() as client:
                response = await client.get("/api/version", timeout=3.0)
                if response.status_code == 200:
                    data = response.json()
                    version = data.get("version", "unknown")
                    return True, version, None
                return False, None, f"Ollama returned HTTP status {response.status_code}"
        except httpx.ConnectError:
            return False, None, f"Cannot connect to Ollama at {self.base_url}. Is Ollama running?"
        except httpx.TimeoutException:
            return False, None, f"Connection to Ollama at {self.base_url} timed out."
        except Exception as exc:
            logger.warning("Error checking Ollama health: %s", exc)
            return False, None, str(exc)

    async def list_models(self) -> list[ModelInfo]:
        """
        Fetch the list of all models currently installed locally in Ollama.
        """
        try:
            async with self._get_client() as client:
                response = await client.get("/api/tags", timeout=5.0)
                if response.status_code != 200:
                    logger.error("Failed to list models. HTTP %s: %s", response.status_code, response.text)
                    return []

                data = response.json()
                raw_models: list[dict[str, Any]] = data.get("models", [])

                results: list[ModelInfo] = []
                active_model = self.settings.active_model.lower()

                for m in raw_models:
                    model_name = m.get("name", "")
                    details = m.get("details", {}) or {}

                    # Check if this model tag or root matches active model
                    is_active = (
                        model_name.lower() == active_model
                        or model_name.lower().startswith(f"{active_model}:")
                        or active_model.startswith(f"{model_name.lower()}:")
                    )

                    results.append(
                        ModelInfo(
                            name=model_name,
                            size_bytes=m.get("size"),
                            modified_at=m.get("modified_at"),
                            format=details.get("format"),
                            family=details.get("family"),
                            parameter_size=details.get("parameter_size"),
                            quantization_level=details.get("quantization_level"),
                            is_active=is_active,
                        )
                    )
                return results

        except httpx.ConnectError as exc:
            raise OllamaConnectionError(f"Cannot connect to Ollama at {self.base_url}") from exc
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError(f"Timeout querying Ollama models at {self.base_url}") from exc
        except Exception as exc:
            logger.exception("Unexpected error querying Ollama models: %s", exc)
            raise OllamaClientError(str(exc)) from exc

    async def is_model_installed(self, model_name: str) -> bool:
        """
        Check if a given model tag is installed in Ollama.
        """
        try:
            models = await self.list_models()
            target = model_name.lower()
            for m in models:
                name = m.name.lower()
                if name == target or name.split(":")[0] == target.split(":")[0]:
                    return True
            return False
        except Exception:
            return False

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.2,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[str]:
        """
        Stream chat completion tokens from Ollama.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            model: Optional model name override.
            temperature: Sampling temperature (default 0.2 for balanced deterministic coding).
            cancel_event: Optional asyncio.Event to trigger graceful cancellation.

        Yields:
            Token strings as they arrive.
        """
        target_model = model or self.settings.active_model
        payload = {
            "model": target_model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_ctx": self.settings.context_window,
                "repeat_penalty": 1.15,
                "repeat_last_n": 64,
                "top_p": 0.9,
            },
        }

        try:
            async with self._get_client() as client:
                async with client.stream("POST", "/api/chat", json=payload, timeout=self.timeout) as response:
                    if response.status_code != 200:
                        error_body = await response.aread()
                        error_text = error_body.decode("utf-8", errors="replace")
                        is_server_crash = any(
                            k in error_text.lower()
                            for k in [
                                "llama-server process has terminated",
                                "cuda error",
                                "0xc0000409",
                                "shared object initialization",
                            ]
                        )
                        if is_server_crash:
                            logger.warning("Ollama CUDA termination in stream; retrying on CPU memory (num_gpu=0)...")
                            await asyncio.sleep(4.0)
                            retry_payload = dict(payload)
                            retry_payload["options"] = dict(payload.get("options", {}))
                            retry_payload["options"]["num_gpu"] = 0
                            async with client.stream("POST", "/api/chat", json=retry_payload, timeout=self.timeout) as retry_res:
                                async for line in retry_res.aiter_lines():
                                    if not line or not line.strip():
                                        continue
                                    try:
                                        chunk = json.loads(line)
                                        content = chunk.get("message", {}).get("content", "")
                                        if content:
                                            yield content
                                        if chunk.get("done", False):
                                            break
                                    except json.JSONDecodeError:
                                        continue
                            return

                        raise OllamaClientError(
                            f"Ollama returned HTTP {response.status_code}: {error_text}"
                        )

                    async for line in response.aiter_lines():
                        if cancel_event and cancel_event.is_set():
                            logger.info("Chat stream aborted by user cancellation.")
                            break

                        if not line or not line.strip():
                            continue

                        try:
                            chunk = json.loads(line)
                            msg = chunk.get("message", {})
                            content = msg.get("content", "")
                            if content:
                                yield content
                            if chunk.get("done", False):
                                break
                        except json.JSONDecodeError:
                            continue

        except httpx.ConnectError as exc:
            raise OllamaConnectionError(f"Cannot connect to Ollama at {self.base_url}") from exc
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError(f"Ollama request timed out after {self.timeout}s") from exc

    async def chat_complete(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.2,
        json_format: bool = False,
        cancel_event: asyncio.Event | None = None,
        on_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """
        Execute chat generation with progressive token streaming.
        Streaming resets socket read timers on every token, eliminating false timeouts on large models,
        and feeds real-time thinking tokens to on_token callback.
        """
        target_model = model or self.settings.active_model
        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_ctx": self.settings.context_window,
                "repeat_penalty": 1.15,
                "repeat_last_n": 64,
                "top_p": 0.9,
            },
        }
        if json_format:
            payload["format"] = "json"

        try:
            if cancel_event and cancel_event.is_set():
                return ""
            async with self._get_client() as client:
                async with client.stream("POST", "/api/chat", json=payload, timeout=self.timeout) as response:
                    if response.status_code != 200:
                        error_body = await response.aread()
                        err_text = error_body.decode("utf-8", errors="replace")

                        # Auto-recovery for Ollama token repeat limit: retry once with enhanced anti-repetition parameters
                        if "token repeat limit" in err_text.lower():
                            logger.warning(
                                "Ollama token repeat limit encountered; retrying with anti-repetition parameters..."
                            )
                            retry_payload = dict(payload)
                            retry_payload["stream"] = False
                            retry_payload["options"] = dict(payload.get("options", {}))
                            retry_payload["options"]["repeat_penalty"] = 1.3
                            retry_payload["options"]["repeat_last_n"] = 128
                            retry_payload["options"]["temperature"] = max(0.35, temperature + 0.15)
                            retry_res = await client.post("/api/chat", json=retry_payload, timeout=self.timeout)
                            if retry_res.status_code == 200:
                                data = retry_res.json()
                                return data.get("message", {}).get("content", "")
                            err_text = retry_res.text

                        # Auto-recovery for Ollama llama-server CUDA crash / 0xc0000409
                        is_server_crash = any(
                            k in err_text.lower()
                            for k in [
                                "llama-server process has terminated",
                                "cuda error",
                                "0xc0000409",
                                "shared object initialization",
                            ]
                        )
                        if is_server_crash:
                            logger.warning(
                                "Ollama llama-server CUDA termination detected. Waiting 4s for daemon to re-launch and retrying on CPU memory (num_gpu=0)..."
                            )
                            await asyncio.sleep(4.0)
                            retry_payload = dict(payload)
                            retry_payload["stream"] = False
                            retry_payload["options"] = dict(payload.get("options", {}))
                            if retry_payload["options"].get("num_ctx", 4096) > 2048:
                                retry_payload["options"]["num_ctx"] = 2048
                            retry_payload["options"]["num_gpu"] = 0
                            retry_res = await client.post("/api/chat", json=retry_payload, timeout=self.timeout)
                            if retry_res.status_code == 200:
                                data = retry_res.json()
                                return data.get("message", {}).get("content", "")
                            err_text = retry_res.text

                        raise OllamaClientError(
                            f"Ollama returned HTTP {response.status_code}: {err_text}"
                        )

                    accumulated: list[str] = []
                    async for line in response.aiter_lines():
                        if cancel_event and cancel_event.is_set():
                            break
                        if not line or not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                            content = chunk.get("message", {}).get("content", "")
                            if content:
                                accumulated.append(content)
                                if on_token:
                                    await on_token(content)
                            if chunk.get("done", False):
                                break
                        except json.JSONDecodeError:
                            continue

                    return "".join(accumulated)

        except httpx.ConnectError as exc:
            raise OllamaConnectionError(f"Cannot connect to Ollama at {self.base_url}") from exc
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError(f"Ollama request timed out after {self.timeout}s") from exc


_global_ollama_client: OllamaClient | None = None


def get_ollama_client() -> OllamaClient:
    """Singleton getter for OllamaClient."""
    global _global_ollama_client
    if _global_ollama_client is None:
        _global_ollama_client = OllamaClient()
    return _global_ollama_client
