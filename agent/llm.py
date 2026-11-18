"""agent/llm.py
Lightweight LLM provider wrapper with response caching, rate-limit backoff,
robust JSON extraction, and offline fallback (Ollama).
"""

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Union

# Load environment variables if python-dotenv is present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("InsightAnalyst.LLM")

# Configuration
DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
CACHE_DIR = Path(os.getenv("LLM_CACHE_DIR", ".cache/llm"))
CACHE_ENABLED = os.getenv("LLM_CACHE_ENABLED", "true").lower() in ("true", "1", "yes")

# Ensure cache directory exists
if CACHE_ENABLED:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _get_cache_key(prompt: str, system_prompt: Optional[str], model: str, provider: str) -> str:
    """Computes a SHA-256 hash for caching prompt responses."""
    raw = f"{provider}:{model}:{system_prompt or ''}:{prompt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_from_cache(cache_key: str) -> Optional[str]:
    """Reads a cached response from disk if present."""
    if not CACHE_ENABLED:
        return None
    cache_file = CACHE_DIR / f"{cache_key}.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("response")
        except Exception:
            return None
    return None


def _write_to_cache(cache_key: str, response_text: str, metadata: Dict[str, Any]) -> None:
    """Persists a response to disk cache for zero-quota replay and offline demo."""
    if not CACHE_ENABLED:
        return
    cache_file = CACHE_DIR / f"{cache_key}.json"
    try:
        payload = {
            "timestamp": time.time(),
            "metadata": metadata,
            "response": response_text
        }
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to write cache file {cache_file}: {e}")


def clean_json_string(text: str) -> str:
    """Strips markdown code fences and cleans trailing commas or whitespace."""
    text = text.strip()
    # Match markdown code fences like ```json ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if match:
        text = match.group(1).strip()
    return text


def parse_json_safely(text: str) -> Dict[str, Any]:
    """Parses a string into a dictionary, handling code fences and basic formatting issues."""
    cleaned = clean_json_string(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as err:
        # Try extracting the outermost { ... }
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            substring = cleaned[start:end + 1]
            try:
                return json.loads(substring)
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Failed to parse LLM JSON response: {err}\nRaw text:\n{text}")


def _call_gemini(prompt: str, system_prompt: Optional[str] = None, model: str = "gemini-2.0-flash") -> str:
    """Calls Google Gemini API using google-genai or google-generativeai."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is not set. Check your .env file.")

    # Try modern google-genai SDK first
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        contents = prompt
        if system_prompt:
            contents = f"System: {system_prompt}\n\nUser: {prompt}"
        response = client.models.generate_content(
            model=model,
            contents=contents
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
            system_instruction=system_prompt if system_prompt else None
        )
        response = gen_model.generate_content(prompt)
        return response.text or ""
    except ImportError:
        raise ImportError(
            "Neither 'google-genai' nor 'google-generativeai' is installed. "
            "Please install google-genai via pip."
        )


def _call_groq(prompt: str, system_prompt: Optional[str] = None, model: str = "llama-3.3-70b-versatile") -> str:
    """Calls Groq API using the official groq SDK."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable is not set.")
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.0
        )
        return resp.choices[0].message.content or ""
    except ImportError:
        raise ImportError("groq package is not installed. Install it via 'pip install groq'.")


def _call_ollama(prompt: str, system_prompt: Optional[str] = None, model: Optional[str] = None) -> str:
    """Calls local Ollama instance using standard library urllib (zero dependencies, offline)."""
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    target_model = model or os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    endpoint = f"{base_url}/api/generate"

    payload = {
        "model": target_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0}
    }
    if system_prompt:
        payload["system"] = system_prompt

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            return res_data.get("response", "")
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"Ollama local instance unavailable at {base_url}: {e}")


def generate(
    prompt: str,
    system_prompt: Optional[str] = None,
    json_mode: bool = False,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    max_retries: int = 3,
    initial_backoff: float = 2.0
) -> Union[str, Dict[str, Any]]:
    """Generates a completion from the configured LLM provider with caching, retries, and offline fallback.

    Args:
        prompt: User prompt.
        system_prompt: Optional system instruction.
        json_mode: If True, parses output safely into a dict.
        provider: "gemini", "groq", or "ollama" (defaults to LLM_PROVIDER env).
        model: Model name override.
        max_retries: Exponential backoff retries for rate-limit / network errors.
        initial_backoff: Initial sleep duration in seconds for backoff.

    Returns:
        String completion or parsed dictionary if json_mode=True.
    """
    chosen_provider = (provider or DEFAULT_PROVIDER).lower()
    chosen_model = model or ("gemini-2.0-flash" if chosen_provider == "gemini" else "llama-3.3-70b-versatile")
    
    # 1. Check disk cache
    cache_key = _get_cache_key(prompt, system_prompt, chosen_model, chosen_provider)
    cached_text = _read_from_cache(cache_key)
    if cached_text is not None:
        if json_mode:
            return parse_json_safely(cached_text)
        return cached_text

    # 2. Execute with exponential backoff
    last_error: Optional[Exception] = None
    backoff = initial_backoff

    for attempt in range(max_retries):
        try:
            if chosen_provider == "gemini":
                text_response = _call_gemini(prompt, system_prompt=system_prompt, model=chosen_model)
            elif chosen_provider == "groq":
                text_response = _call_groq(prompt, system_prompt=system_prompt, model=chosen_model)
            elif chosen_provider == "ollama":
                text_response = _call_ollama(prompt, system_prompt=system_prompt, model=chosen_model)
            else:
                raise ValueError(f"Unknown LLM provider: {chosen_provider}")

            # Persist to disk cache
            _write_to_cache(cache_key, text_response, {
                "provider": chosen_provider,
                "model": chosen_model,
                "attempt": attempt + 1
            })

            if json_mode:
                return parse_json_safely(text_response)
            return text_response

        except Exception as e:
            last_error = e
            err_str = str(e).lower()
            is_rate_limit = "429" in err_str or "quota" in err_str or "rate limit" in err_str or "resourceexhausted" in err_str
            is_network_err = "connection" in err_str or "timeout" in err_str or "urlerror" in err_str

            if (is_rate_limit or is_network_err) and attempt < max_retries - 1:
                logger.warning(f"LLM request error on {chosen_provider} (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {backoff:.1f}s...")
                time.sleep(backoff)
                backoff *= 2.0
            else:
                break

    # 3. Fallback to Ollama if primary provider failed and is not already Ollama
    if chosen_provider != "ollama":
        logger.warning(f"Primary provider '{chosen_provider}' failed ({last_error}). Attempting local Ollama fallback...")
        try:
            ollama_text = _call_ollama(prompt, system_prompt=system_prompt)
            _write_to_cache(cache_key, ollama_text, {
                "provider": "ollama_fallback",
                "model": os.getenv("OLLAMA_MODEL", "llama3.2:3b")
            })
            if json_mode:
                return parse_json_safely(ollama_text)
            return ollama_text
        except Exception as fallback_err:
            logger.error(f"Ollama fallback also failed: {fallback_err}")

    # If all failed, raise clear error
    raise RuntimeError(f"LLM generation failed after retries and fallback. Last error: {last_error}")
