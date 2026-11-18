"""agent/llm.py
Lightweight LLM provider wrapper adhering to Prompt 2.1 specifications.
Supports primary provider (Gemini/Groq), Ollama (via stdlib urllib), and Fake provider for offline testing.
Includes on-disk caching (readwrite, readonly, off), rate-limit backoff with jitter, and safe JSON parsing with single-retry recovery.
"""

import hashlib
import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

# Load environment variables if dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("InsightAnalyst.LLM")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


class LLMError(Exception):
    """Base exception for LLM generation failures."""
    pass


class LLMRateLimitError(LLMError):
    """Raised when rate limits are exceeded after exhausting retries."""
    pass


# Global in-memory storage for the fake provider to simulate dynamic responses and retries
_FAKE_RESPONSES: Dict[str, Any] = {}
_FAKE_CALL_COUNTS: Dict[str, int] = {}


def register_fake_response(prompt_substring: str, response: str) -> None:
    """Helper for tests to register canned responses for the fake provider."""
    _FAKE_RESPONSES[prompt_substring] = response


def clear_fake_responses() -> None:
    """Helper to reset fake responses and call counts."""
    _FAKE_RESPONSES.clear()
    _FAKE_CALL_COUNTS.clear()


def clean_markdown_fences(text: str) -> str:
    """Strips markdown code fences (e.g. ```json ... ```) from output."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text


clean_json_string = clean_markdown_fences


def parse_json_safely(text: str) -> Dict[str, Any]:
    """Parses a JSON string after stripping markdown fences."""
    cleaned = clean_markdown_fences(text)
    return json.loads(cleaned)


def _get_cache_key(*args, **kwargs) -> str:
    """Computes a SHA-256 hash across provider, model, system, prompt, and temperature."""
    # Supports both new (provider, model, system, prompt, temperature) and legacy positional calls
    if len(args) == 5:
        provider, model, system, prompt, temperature = args
    elif len(args) == 4:
        # Legacy: prompt, system, model, provider
        prompt, system, model, provider = args
        temperature = 0.0
    else:
        provider = kwargs.get("provider", "default")
        model = kwargs.get("model", "default")
        system = kwargs.get("system", "")
        prompt = kwargs.get("prompt", "")
        temperature = kwargs.get("temperature", 0.0)

    raw = f"{provider}:{model}:{system or ''}:{prompt}:{temperature:.2f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_cache(cache_key: str, cache_dir: Path) -> Optional[str]:
    """Reads response text from disk cache if present."""
    cache_file = cache_dir / f"{cache_key}.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("response")
        except Exception:
            return None
    return None


def _write_cache(cache_key: str, cache_dir: Path, response: str, metadata: Dict[str, Any]) -> None:
    """Writes response text to disk cache."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{cache_key}.json"
        payload = {
            "timestamp": time.time(),
            "metadata": metadata,
            "response": response
        }
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to write cache key {cache_key}: {e}")


def _write_to_cache(cache_key: str, response: str, metadata: Dict[str, Any]) -> None:
    """Backwards-compatible cache writer alias."""
    cache_dir = Path(os.getenv("LLM_CACHE_DIR", ".cache/llm"))
    _write_cache(cache_key, cache_dir, response, metadata)


def _call_fake_provider(prompt: str, system: Optional[str] = None) -> str:
    """Returns canned responses for offline testing without quota consumption."""
    # Check registered in-memory responses first
    for sub, resp in _FAKE_RESPONSES.items():
        if sub in prompt:
            _FAKE_CALL_COUNTS[sub] = _FAKE_CALL_COUNTS.get(sub, 0) + 1
            if isinstance(resp, list):
                call_idx = _FAKE_CALL_COUNTS[sub] - 1
                if call_idx < len(resp):
                    return resp[call_idx]
                return resp[-1]
            return str(resp)

    # Check canned JSON file if specified
    canned_file = os.getenv("FAKE_LLM_FILE")
    if canned_file and Path(canned_file).exists():
        try:
            with open(canned_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for key, val in data.items():
                    if key in prompt:
                        return val
        except Exception:
            pass

    # Default canned fallback
    return '{"status": "ok", "message": "canned fake response"}'


def _call_ollama(prompt: str, system: Optional[str] = None, model: Optional[str] = None, temperature: float = 0.0) -> str:
    """Calls Ollama HTTP API using standard library urllib."""
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    target_model = model or os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    endpoint = f"{base_url}/api/generate"

    payload = {
        "model": target_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature}
    }
    if system:
        payload["system"] = system

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body.get("response", "")
    except urllib.error.HTTPError as e:
        raise LLMError(f"Ollama HTTP {e.code}: {e.reason}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise LLMError(f"Ollama connection error: {e}")


def _call_gemini(prompt: str, system: Optional[str] = None, model: str = "gemini-2.0-flash", temperature: float = 0.0) -> str:
    """Calls Google Gemini using the official SDK (never logging keys)."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise LLMError("GEMINI_API_KEY environment variable is missing.")

    # Try official modern google-genai SDK
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=system if system else None
        )
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=config
        )
        return response.text or ""
    except ImportError:
        pass

    # Try legacy google.generativeai SDK
    try:
        import google.generativeai as legacy_genai
        legacy_genai.configure(api_key=api_key)
        gen_model = legacy_genai.GenerativeModel(
            model_name=model,
            system_instruction=system if system else None
        )
        generation_config = legacy_genai.types.GenerationConfig(temperature=temperature)
        response = gen_model.generate_content(prompt, generation_config=generation_config)
        return response.text or ""
    except ImportError:
        raise LLMError("Neither 'google-genai' nor 'google-generativeai' is installed.")


def _call_groq(prompt: str, system: Optional[str] = None, model: str = "llama-3.3-70b-versatile", temperature: float = 0.0) -> str:
    """Calls Groq API using the official groq SDK."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise LLMError("GROQ_API_KEY environment variable is missing.")
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            timeout=30.0
        )
        return resp.choices[0].message.content or ""
    except ImportError:
        raise LLMError("'groq' SDK is not installed.")


def generate(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.0,
    **kwargs: Any
) -> Any:
    """Generates text from configured provider with rate-limit retries, jitter, and on-disk caching.

    Public API:
        generate(prompt: str, system: str | None = None, temperature: float = 0.0) -> str
    """
    # Support legacy / alias parameters if passed
    if system is None and "system_prompt" in kwargs:
        system = kwargs["system_prompt"]
    if kwargs.get("json_mode", False):
        return generate_json(prompt, system=system)

    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    model = os.getenv("LLM_MODEL", "gemini-2.0-flash" if provider == "gemini" else ("llama3.2:3b" if provider == "ollama" else "fake-model"))
    cache_mode = os.getenv("CACHE_MODE", "readwrite").lower()
    cache_dir = Path(os.getenv("LLM_CACHE_DIR", ".cache/llm"))

    # 1. Check On-Disk Cache
    cache_key = _get_cache_key(provider, model, system, prompt, temperature)
    if cache_mode in ("readwrite", "readonly"):
        cached_val = _read_cache(cache_key, cache_dir)
        if cached_val is not None:
            logger.info(f"Provider: {provider} | Cache: HIT | Latency: 0.000s")
            return cached_val
        elif cache_mode == "readonly":
            raise LLMError(f"Cache miss in readonly mode for provider={provider}, key={cache_key}")

    # 2. Execute call with Exponential Backoff + Jitter (max 4 attempts, 30s timeout)
    max_attempts = 4
    initial_backoff = 1.0
    last_error: Optional[Exception] = None
    start_time = time.time()

    for attempt in range(max_attempts):
        try:
            call_start = time.time()
            if provider == "fake":
                text = _call_fake_provider(prompt, system=system)
            elif provider == "ollama":
                text = _call_ollama(prompt, system=system, model=model, temperature=temperature)
            elif provider == "groq":
                text = _call_groq(prompt, system=system, model=model, temperature=temperature)
            else:
                # Default primary: Gemini
                text = _call_gemini(prompt, system=system, model=model, temperature=temperature)

            latency = time.time() - call_start
            logger.info(f"Provider: {provider} | Cache: MISS | Latency: {latency:.3f}s")

            # Write to cache if enabled
            if cache_mode == "readwrite":
                _write_cache(cache_key, cache_dir, text, {
                    "provider": provider,
                    "model": model,
                    "temperature": temperature
                })
            return text

        except Exception as e:
            last_error = e
            err_msg = str(e).lower()
            is_rate_limit = any(term in err_msg for term in ["429", "quota", "rate limit", "resourceexhausted"])
            is_network = any(term in err_msg for term in ["connection", "timeout", "urlerror", "network"])

            # If primary provider has network error and Ollama is configured, attempt fallback
            if is_network and provider != "ollama" and os.getenv("OLLAMA_FALLBACK", "true").lower() in ("true", "1"):
                logger.warning(f"Network error on primary provider '{provider}'. Attempting Ollama fallback...")
                try:
                    fb_start = time.time()
                    fb_text = _call_ollama(prompt, system=system, temperature=temperature)
                    latency = time.time() - fb_start
                    logger.info(f"Provider: ollama_fallback | Cache: MISS | Latency: {latency:.3f}s")
                    if cache_mode == "readwrite":
                        _write_cache(cache_key, cache_dir, fb_text, {"provider": "ollama_fallback", "model": "ollama"})
                    return fb_text
                except Exception as fb_err:
                    logger.error(f"Ollama fallback failed: {fb_err}")

            if attempt < max_attempts - 1 and (is_rate_limit or is_network):
                jitter = random.uniform(0.1, 1.0)
                delay = (initial_backoff * (2 ** attempt)) + jitter
                time.sleep(delay)
            else:
                if is_rate_limit:
                    raise LLMRateLimitError(f"Rate limit exceeded after {attempt + 1} attempts: {e}") from e
                break

    raise LLMError(f"LLM request failed after {max_attempts} attempts: {last_error}") from last_error


def generate_json(prompt: str, system: Optional[str] = None) -> Dict[str, Any]:
    """Generates structured JSON output, stripping code fences and retrying once on parse error.

    Public API:
        generate_json(prompt: str, system: str | None = None) -> dict
    """
    raw = generate(prompt, system=system, temperature=0.0)
    cleaned = clean_markdown_fences(raw)

    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError) as first_err:
        logger.warning(f"Initial JSON parse failed ({first_err}). Retrying once with reminder prompt...")

        # Single retry with explicit JSON-only reminder
        retry_prompt = (
            f"{prompt}\n\n"
            "CRITICAL: Your previous response was not valid JSON.\n"
            "Return valid JSON only. Do not include markdown formatting, backticks, or any explanation."
        )
        retry_raw = generate(retry_prompt, system=system, temperature=0.0)
        retry_cleaned = clean_markdown_fences(retry_raw)

        try:
            return json.loads(retry_cleaned)
        except (json.JSONDecodeError, ValueError) as second_err:
            raise LLMError(f"Failed to parse valid JSON from LLM response after retry. Output was:\n{retry_raw}") from second_err
