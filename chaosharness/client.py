import time
import json
import requests
from dataclasses import dataclass
from typing import Any

@dataclass
class ToolCall:
    id: str
    name: str
    args: dict

@dataclass
class Response:
    text: str
    tool_calls: list[ToolCall]
    latency_s: float
    prompt_tokens: int
    completion_tokens: int
    tokens_per_s: float
    raw: dict
    error: str | None

class LLMClient:
    def __init__(
        self,
        model: str,
        endpoint: str,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int = 768,
        timeout_s: float = 120,
        cache: Any = None,
        max_retries: int = 3,
        backoff_s: float = 0.5,
    ):
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.temperature = temperature
        self.seed = seed
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.cache = cache
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        
    def _do_request(self, payload: dict) -> tuple[dict, float, str | None]:
        url = f"{self.endpoint}/v1/chat/completions" if not self.endpoint.endswith("/v1/chat/completions") else self.endpoint
        
        start = time.perf_counter()
        backoff = self.backoff_s
        
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(url, json=payload, timeout=self.timeout_s)
                # Non-retryable permanent client errors
                if resp.status_code in (400, 401, 403, 404):
                    latency_s = time.perf_counter() - start
                    return {}, latency_s, f"HTTP {resp.status_code}: {resp.text}"
                    
                if resp.status_code == 429 or resp.status_code in (500, 502, 503, 504):
                    if attempt < self.max_retries:
                        time.sleep(backoff)
                        backoff *= 2
                        continue
                    else:
                        latency_s = time.perf_counter() - start
                        return {}, latency_s, f"HTTP {resp.status_code}: {resp.text}"
                        
                resp.raise_for_status()
                latency_s = time.perf_counter() - start
                return resp.json(), latency_s, None
            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries:
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    latency_s = time.perf_counter() - start
                    return {}, latency_s, str(e)
                    
        return {}, time.perf_counter() - start, "Unknown error"

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        images: list[bytes] | None = None,
        hint: dict | None = None,
    ) -> Response:
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }
        if self.seed is not None:
            payload["seed"] = self.seed
            
        if tools:
            payload["tools"] = tools

        if images:
            import base64
            payload["images"] = [base64.b64encode(img).decode("ascii") for img in images]

        if self.cache:
            cached_resp = self.cache.get(payload, hint)
            if cached_resp:
                raw = cached_resp["raw"]
                latency_s = cached_resp["latency_s"]
                err = cached_resp["error"]
            else:
                raw, latency_s, err = self._do_request(payload)
                self.cache.put(payload, hint, {
                    "raw": raw,
                    "latency_s": latency_s,
                    "error": err
                })
        else:
            raw, latency_s, err = self._do_request(payload)
        
        if err:
            return Response(
                text="",
                tool_calls=[],
                latency_s=latency_s,
                prompt_tokens=0,
                completion_tokens=0,
                tokens_per_s=0.0,
                raw=raw,
                error=err
            )
            
        choices = raw.get("choices", [])
        if not choices:
            return Response("", [], latency_s, 0, 0, 0.0, raw, "No choices returned")
            
        message = choices[0].get("message", {})
        text = message.get("content") or ""
        
        raw_tool_calls = message.get("tool_calls", [])
        tool_calls = []
        for tc in raw_tool_calls:
            try:
                args = json.loads(tc["function"]["arguments"])
                tool_calls.append(ToolCall(id=tc.get("id", ""), name=tc["function"]["name"], args=args))
            except Exception:
                pass # invalid arguments
                
        usage = raw.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        
        tokens_per_s = completion_tokens / latency_s if latency_s > 0 else 0.0
        
        return Response(
            text=text,
            tool_calls=tool_calls,
            latency_s=latency_s,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tokens_per_s=tokens_per_s,
            raw=raw,
            error=None
        )

    def probe(self) -> dict:
        if self.cache and hasattr(self.cache, "mode") and self.cache.mode == "replay":
            cached_meta = self.cache.get_probe_metadata()
            if cached_meta:
                return cached_meta

        result = {
            "reachable": False,
            "model_available": False,
            "native_tools": False,
            "json_mode": False,
            "vision": False,
            "ctx_hint": 4096
        }
        
        try:
            # Check reachable
            if "localhost" in self.endpoint or "127.0.0.1" in self.endpoint:
                resp = requests.get(f"{self.endpoint}/api/tags", timeout=5)
                result["reachable"] = resp.status_code == 200
                if result["reachable"]:
                    tags = resp.json()
                    models = [m.get("name") for m in tags.get("models", [])]
                    result["model_available"] = any(self.model == m or m.startswith(self.model+":") for m in models)
            else:
                resp = requests.get(f"{self.endpoint}/v1/models", timeout=5)
                result["reachable"] = resp.status_code == 200
                if result["reachable"]:
                    models = [m.get("id") for m in resp.json().get("data", [])]
                    result["model_available"] = self.model in models
        except Exception:
            return result
            
        if not result["model_available"]:
            return result
            
        # Probe native tools
        tool_probe = self.chat(
            messages=[{"role": "user", "content": "Add 2 and 3."}],
            tools=[{"type": "function", "function": {"name": "add", "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}}}}]
        )
        if tool_probe.error is None and tool_probe.tool_calls:
            result["native_tools"] = True
            
        # For vision and json_mode we could probe further, but simple for now
        result["json_mode"] = False
        result["vision"] = False
        
        if self.cache and hasattr(self.cache, "mode") and self.cache.mode == "record":
            self.cache.save_probe_metadata(result)
            
        return result
