import argparse
import sys
import os
import json
import logging
from pathlib import Path
from typing import Any

from .client import LLMClient
from .cache import RequestCache
from .mocks import MockGood, MockLooping, MockHallucinating, MockInjectable, MockModel
from .chaos import run_chaos_suite
from .profiler import run_domain_suite
from .synthesizer import generate_report, build_routing_manifest, DOMAIN_KEYS
from .scoring import FailureLabel

try:
    from rich.console import Console
    from rich.table import Table
    console = Console()
except ImportError:
    console = None

def get_client(model: str, endpoint: str, mock: str | None, cache_mode: str, cache_dir: str, seed: int, temp: float) -> Any:
    cache = RequestCache(cache_dir, mode=cache_mode, model=model)
    if mock:
        if mock == "good":
            client = MockGood()
        elif mock == "looping":
            client = MockLooping()
        elif mock == "hallucinating":
            client = MockHallucinating()
        elif mock == "injectable":
            client = MockInjectable()
        else:
            raise ValueError(f"Unknown mock: {mock}")
        client.cache = cache
        return client
        
    client = LLMClient(
        model=model,
        endpoint=endpoint,
        temperature=temp,
        seed=seed,
        cache=cache
    )
    return client

def run_selftest() -> int:
    print("[INFO] Running ChaosHarness self-test suite...")
    
    # 1. Test MockGood -> expect 100% resilience, 100% domains, AUTONOMOUS tier
    print("  Testing MockGood...")
    good_client = MockGood()
    good_chaos = run_chaos_suite(good_client, seed=42, trials=2, quick=True, vectors=["all"], packs=[])
    good_domain = run_domain_suite(good_client, seed=42, items_per_domain=3, skip_vision=False)
    good_manifest = build_routing_manifest("mock-good", good_chaos, good_domain, seed=42, trials=2)
    
    if good_manifest["overall_resilience"] != 100.0:
        print(f"[FAIL] MockGood expected 100% resilience, got {good_manifest['overall_resilience']}%")
        return 1
        
    for dom in ["code_generation", "structured_data", "algorithmic_logic", "vision_processing"]:
        tier = good_manifest["tiers"].get(dom)
        if tier != "AUTONOMOUS":
            print(f"[FAIL] MockGood expected AUTONOMOUS for {dom}, got {tier}")
            return 1
            
    print("  [PASS] MockGood validated (100% resilience, all domains AUTONOMOUS)")
    
    # 2. Test MockLooping -> expect LOOP failures, BLOCKED posture
    print("  Testing MockLooping...")
    loop_client = MockLooping()
    loop_chaos = run_chaos_suite(loop_client, seed=42, trials=2, quick=True, vectors=["retry_trap_503"], packs=[])
    loop_tax = good_manifest["failure_taxonomy"]
    loop_fails = [r.failure_label for r in loop_chaos if r.failure_label]
    if FailureLabel.LOOP.value not in loop_fails:
        print(f"[FAIL] MockLooping expected LOOP failure, got {loop_fails}")
        return 1
    print("  [PASS] MockLooping validated (detected LOOP)")
    
    # 3. Test MockHallucinating -> expect HALLUCINATED_TOOL or FABRICATED_VALUE
    print("  Testing MockHallucinating...")
    halluc_client = MockHallucinating()
    halluc_chaos = run_chaos_suite(halluc_client, seed=42, trials=2, quick=True, vectors=["phantom_tool_lure"], packs=[])
    halluc_fails = [r.failure_label for r in halluc_chaos if r.failure_label]
    if (FailureLabel.HALLUCINATED_TOOL.value not in halluc_fails and
        FailureLabel.FABRICATED_VALUE.value not in halluc_fails):
        print(f"[FAIL] MockHallucinating expected tool/value hallucination, got {halluc_fails}")
        return 1
    print("  [PASS] MockHallucinating validated (detected hallucination)")
    
    # 4. Test MockInjectable -> expect INJECTION_FOLLOWED
    print("  Testing MockInjectable...")
    inj_client = MockInjectable()
    inj_chaos = run_chaos_suite(inj_client, seed=42, trials=2, quick=True, vectors=["prompt_injection"], packs=[])
    inj_fails = [r.failure_label for r in inj_chaos if r.failure_label]
    if FailureLabel.INJECTION_FOLLOWED.value not in inj_fails:
        print(f"[FAIL] MockInjectable expected INJECTION_FOLLOWED, got {inj_fails}")
        return 1
    print("  [PASS] MockInjectable validated (detected prompt injection breach)")
    
    print("[PASS] All self-test assertions passed successfully.")
    return 0

def run_route(manifest_path: str, task: str) -> int:
    task_map = {
        "code": "code_generation",
        "data": "structured_data",
        "logic": "algorithmic_logic",
        "vision": "vision_processing"
    }
    dom_key = task_map.get(task, task)
    if dom_key not in DOMAIN_KEYS:
        print(f"[FAIL] Unsupported task: {task}. Choose from: code, data, logic, vision")
        return 1
        
    p = Path(manifest_path)
    if not p.exists():
        print(f"[FAIL] Manifest file not found: {manifest_path}")
        return 1
        
    with open(p, "r", encoding="utf-8") as f:
        manifest = json.load(f)
        
    tier = manifest.get("tiers", {}).get(dom_key, "N/A")
    score = manifest.get("competency_scores", {}).get(dom_key)
    target = manifest.get("model_target", "unknown")
    
    if tier in ("BLOCKED", "N/A"):
        print(f"No eligible route for task '{task}' (Status: {tier})")
        return 0
        
    route_name = manifest.get("recommended_route")
    print(f"Selected route for task '{task}': {target} -> {route_name} (Tier: {tier}, Score: {score}%)")
    return 0

def run_single_model(args) -> int:
    model_safe_name = args.model.replace(":", "_").replace("/", "_")
    out_val = getattr(args, "output", None) or getattr(args, "out", None)
    out_dir = Path(out_val or f"./out/{model_safe_name}")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    quick_mode = getattr(args, "quick", False)
    trials = 2 if quick_mode else (getattr(args, "trials", None) or 5)
    items = 5 if quick_mode else (getattr(args, "items", None) or 10)
    
    print(f"[INFO] Initializing client for model: {args.model}")
    client = get_client(
        model=args.model,
        endpoint=args.endpoint,
        mock=getattr(args, "mock", None),
        cache_mode=getattr(args, "cache", "live"),
        cache_dir=getattr(args, "cache_dir", "./cache"),
        seed=getattr(args, "seed", 42),
        temp=getattr(args, "temperature", 0.0)
    )
    
    # Setup / Probe verification
    probe_info = client.probe() if hasattr(client, "probe") else {}
    if not getattr(args, "mock", None) and getattr(args, "cache", "live") == "live":
        if not probe_info.get("reachable"):
            print(f"[FAIL] Endpoint unreachable at {args.endpoint}")
            return 2
        if not probe_info.get("model_available"):
            print(f"[FAIL] Model '{args.model}' not found on endpoint.")
            print(f"Please run: ollama pull {args.model}")
            return 2
            
    # Track state for interrupt handling
    chaos_results = []
    domain_results = {}
    
    try:
        # Run Chaos
        print(f"[INFO] Running Chaos Suite ({trials} trials per vector)...")
        vec_arg = getattr(args, "vectors", "all")
        vectors = vec_arg.split(",") if vec_arg else ["all"]
        pack_arg = getattr(args, "pack", None)
        packs = [pack_arg] if pack_arg else []
        chaos_results = run_chaos_suite(client, seed=args.seed, trials=trials, quick=quick_mode, vectors=vectors, packs=packs)
        
        # Run Domains
        print(f"[INFO] Running Domain Competency Profiling ({items} items per domain)...")
        dom_arg = getattr(args, "domains", "code,data,logic,vision")
        domains_list = dom_arg.split(",") if dom_arg else ["code", "data", "logic", "vision"]
        domain_results = run_domain_suite(
            client=client,
            seed=args.seed,
            items_per_domain=items,
            skip_vision=getattr(args, "no_vision", False),
            domains_to_run=domains_list
        )
        
        # Generate Reports
        print(f"[INFO] Synthesizing results and writing reports to {out_dir}...")
        manifest = generate_report(
            model=args.model,
            resilience_results=chaos_results,
            domain_results=domain_results,
            output_path=str(out_dir),
            seed=args.seed,
            trials=trials,
            probe_info=probe_info,
            endpoint=args.endpoint
        )
        
        # Display Scorecard
        display_scorecard(manifest)
        
        # Check --min-tier
        min_tier_val = getattr(args, "min_tier", None)
        if min_tier_val:
            req_tier = min_tier_val.upper()
            active_tiers = [t for t in manifest["tiers"].values() if t in ("AUTONOMOUS", "SUPERVISED")]
            curr_tier = "AUTONOMOUS" if "AUTONOMOUS" in active_tiers else ("SUPERVISED" if active_tiers else "BLOCKED")
            tier_weights = {"AUTONOMOUS": 2, "SUPERVISED": 1, "BLOCKED": 0, "N/A": 0}
            if tier_weights.get(curr_tier, 0) < tier_weights.get(req_tier, 0):
                print(f"[WARN] Overall model tier '{curr_tier}' falls below requested minimum tier '{req_tier}'")
                return 1
                
        return 0
        
    except KeyboardInterrupt:
        print("\n[WARN] Execution interrupted by user! Preserving partial results...")
        generate_report(
            model=args.model,
            resilience_results=chaos_results,
            domain_results=domain_results,
            output_path=str(out_dir),
            seed=args.seed,
            trials=trials,
            probe_info=probe_info,
            endpoint=args.endpoint,
            partial=True
        )
        print(f"[INFO] Partial results preserved in {out_dir}")
        return 130

def display_scorecard(manifest: dict[str, Any]):
    if console:
        table = Table(title=f"ChaosHarness Scorecard: {manifest['model_target']}")
        table.add_column("Domain / Metric", style="cyan")
        table.add_column("Score", justify="right")
        table.add_column("Tier", justify="center")
        table.add_column("Readiness", justify="center")
        
        for dom in DOMAIN_KEYS:
            sc = manifest['competency_scores'].get(dom)
            sc_str = f"{sc:.1f}%" if sc is not None else "N/A"
            tier = manifest['tiers'].get(dom, "N/A")
            ready = "[PASS]" if manifest['production_readiness'].get(dom) else "[FAIL]"
            table.add_row(dom, sc_str, tier, ready)
            
        console.print(table)
        res_val = manifest.get('overall_resilience')
        res_str = f"{res_val:.1f}%" if res_val is not None else "N/A"
        lcb_val = manifest.get('overall_resilience_lcb')
        lcb_str = f"{lcb_val:.2f}%" if lcb_val is not None else "N/A"
        console.print(f"[bold]Overall Resilience:[/bold] {res_str} (LCB: {lcb_str})")
        console.print(f"[bold]Recommended Route:[/bold] {manifest.get('recommended_route') or 'None'}")
    else:
        print("\n=== ChaosHarness Scorecard ===")
        print(f"Model: {manifest['model_target']}")
        res_val = manifest.get('overall_resilience')
        res_str = f"{res_val:.1f}%" if res_val is not None else "N/A"
        print(f"Overall Resilience: {res_str}")
        print(f"Recommended Route: {manifest.get('recommended_route') or 'None'}")
        for dom in DOMAIN_KEYS:
            sc = manifest.get('competency_scores', {}).get(dom)
            sc_str = f"{sc:.1f}%" if sc is not None else "N/A"
            print(f"  {dom}: Score={sc_str}, Tier={manifest.get('tiers', {}).get(dom)}")

def run_compare(args) -> int:
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        print("[FAIL] No models provided for comparison.")
        return 1
        
    print(f"[INFO] Comparing models: {models}")
    all_manifests = []
    
    for m in models:
        args.model = m
        args.output = f"./out/compare/{m.replace(':', '_')}"
        ret = run_single_model(args)
        if ret not in (0, 1):
            return ret
        man_path = Path(args.output) / "routing_manifest.json"
        if man_path.exists():
            with open(man_path, "r", encoding="utf-8") as f:
                all_manifests.append(json.load(f))
                
    # Build combined leaderboard
    leaderboard = []
    for man in all_manifests:
        leaderboard.append({
            "model": man["model_target"],
            "resilience": man["overall_resilience"],
            "recommended_route": man["recommended_route"]
        })
    print("\n=== Comparison Leaderboard ===")
    for entry in leaderboard:
        print(f"Model: {entry['model']} | Resilience: {entry['resilience']}% | Route: {entry['recommended_route']}")
        
    return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chaosharness", description="ChaosHarness: LLM Evaluation and Runtime Chaos Testing")
    subparsers = parser.add_subparsers(dest="command")
    
    # 1. run
    p_run = subparsers.add_parser("run", help="Run full evaluation and chaos testing suite")
    p_run.add_argument("--model", type=str, default="gemma4:e2b", help="Model name")
    p_run.add_argument("--endpoint", type=str, default="http://localhost:11434", help="API Endpoint")
    p_run.add_argument("--seed", type=int, default=42, help="Seed")
    p_run.add_argument("--trials", type=int, default=5, help="Chaos trials per vector")
    p_run.add_argument("--items", type=int, default=10, help="Items per domain")
    p_run.add_argument("--domains", type=str, default="code,data,logic,vision", help="Domains to profile")
    p_run.add_argument("--vectors", type=str, default="all", help="Vectors to test")
    p_run.add_argument("--cache", type=str, choices=["live", "record", "replay"], default="live", help="Cache mode")
    p_run.add_argument("--cache-dir", type=str, default="./cache", help="Cache directory")
    p_run.add_argument("--out", type=str, default=None, help="Output directory")
    p_run.add_argument("--output", type=str, default=None, help="Output directory")
    p_run.add_argument("--temperature", type=float, default=0.0, help="Temperature")
    p_run.add_argument("--pack", type=str, default=None, help="Path to custom YAML scenario pack")
    p_run.add_argument("--min-tier", type=str, choices=["SUPERVISED", "AUTONOMOUS"], default=None, help="Minimum tier required")
    p_run.add_argument("--quick", action="store_true", help="Quick mode (2 trials, 5 domain items)")
    p_run.add_argument("--no-vision", action="store_true", help="Disable vision domain")
    p_run.add_argument("--mock", type=str, choices=["good", "looping", "hallucinating", "injectable"], default=None, help="Mock model for offline testing")

    # 2. compare
    p_comp = subparsers.add_parser("compare", help="Compare multiple models")
    p_comp.add_argument("--models", type=str, required=True, help="Comma-separated models list")
    p_comp.add_argument("--endpoint", type=str, default="http://localhost:11434")
    p_comp.add_argument("--seed", type=int, default=42)
    p_comp.add_argument("--trials", type=int, default=2)
    p_comp.add_argument("--items", type=int, default=5)
    p_comp.add_argument("--domains", type=str, default="code,data,logic,vision")
    p_comp.add_argument("--vectors", type=str, default="all")
    p_comp.add_argument("--cache", type=str, default="live")
    p_comp.add_argument("--cache-dir", type=str, default="./cache")
    p_comp.add_argument("--temperature", type=float, default=0.0)
    p_comp.add_argument("--quick", action="store_true", default=True)
    p_comp.add_argument("--no-vision", action="store_true")
    p_comp.add_argument("--mock", type=str, default=None)
    p_comp.add_argument("--pack", type=str, default=None, help="Path to custom YAML scenario pack")
    p_comp.add_argument("--out", type=str, default=None, help="Output directory")
    p_comp.add_argument("--output", type=str, default=None, help="Output directory")
    p_comp.add_argument("--min-tier", type=str, choices=["SUPERVISED", "AUTONOMOUS"], default=None, help="Minimum tier required")

    # 3. selftest
    subparsers.add_parser("selftest", help="Run deterministic mock self-test suite")

    # 4. route
    p_route = subparsers.add_parser("route", help="Lookup model route for a specific task")
    p_route.add_argument("--manifest", type=str, required=True, help="Path to routing_manifest.json")
    p_route.add_argument("--task", type=str, required=True, choices=["code", "data", "logic", "vision"], help="Task domain")
    
    return parser

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
            sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass

    parser = build_parser()
    
    # If no subcommand provided, show help
    if len(sys.argv) == 1:
        parser.print_help()
        return 0
        
    args = parser.parse_args()
    
    if args.command == "selftest":
        return run_selftest()
    elif args.command == "route":
        return run_route(args.manifest, args.task)
    elif args.command == "compare":
        return run_compare(args)
    elif args.command == "run":
        return run_single_model(args)
    else:
        parser.print_help()
        return 0

if __name__ == "__main__":
    sys.exit(main())
