import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from chaosharness.cache import RequestCache
from chaosharness.mocks import MockGood
from chaosharness.chaos import run_chaos_suite
from chaosharness.profiler import run_domain_suite

def generate_demo_cache():
    cache_dir = Path(__file__).parent.parent / "demo_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    model_name = "gemma4:e2b"
    cache = RequestCache(cache_dir=str(cache_dir), mode="record", model=model_name)
    
    probe_meta = {
        "reachable": True,
        "model_available": True,
        "native_tools": True,
        "json_mode": True,
        "vision": True,
        "ctx_hint": 4096,
        "source": "synthetic_mock_demo"
    }
    cache.save_probe_metadata(probe_meta)
    
    # Create client wrapper with recording cache
    mock = MockGood()
    
    class RecordingClient:
        def __init__(self, inner, cache, model):
            self.inner = inner
            self.cache = cache
            self.model = model
            self.temperature = 0.0
            self.seed = 42
            self.max_tokens = 768
            
        def probe(self):
            return self.inner.probe()
            
        def chat(self, messages, tools=None, images=None, hint=None):
            resp = self.inner.chat(messages, tools, images, hint)
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
                
            self.cache.put(payload, hint, {
                "raw": resp.raw,
                "latency_s": resp.latency_s,
                "error": resp.error
            })
            return resp

    rec_client = RecordingClient(mock, cache, model_name)
    
    print("[INFO] Generating synthetic offline demo cache for gemma4:e2b...")
    # 1. Quick Chaos
    run_chaos_suite(rec_client, seed=42, trials=2, quick=True, vectors=["all"], packs=[])
    # 2. Quick Domains
    run_domain_suite(rec_client, seed=42, items_per_domain=5, skip_vision=False)
    
    print(f"[PASS] Demo cache successfully generated in {cache_dir}")

if __name__ == "__main__":
    generate_demo_cache()
