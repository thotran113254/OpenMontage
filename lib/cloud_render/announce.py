"""Assembles the `dry_run()` announce payload: offer shortlist, cost/time
estimates, and a persisted `dry_run_ref` -- everything
`skills/core/cloud-render.md`'s announce block needs, with no rental
performed.

Split out of `tools/video/vast_cloud_render.py` to keep that file a thin
registry wrapper (see its module docstring) -- this is pure domain logic
that belongs next to `queue.py`/`kit.py`/`config.py` in `lib/cloud_render`,
not tool-framework code.
"""

from __future__ import annotations

import os
from typing import Any

from lib.cloud_render import cost_estimate, dry_run_store, ledger, queue, vast_client


def build(tool_name: str, provider: str, mode: str, inputs: dict[str, Any],
          resolved: dict[str, Any]) -> dict[str, Any]:
    """`resolved` is the already-3-layer-resolved config for this call (see
    `VastCloudRender._resolve_config`). Returns the full announce payload,
    or `{"would_execute": False, "error": ...}` if no eligible offer exists
    or the search itself failed -- never raises."""
    ledger.reap()  # free hygiene sweep -- most likely moment to catch an orphan

    warnings: list[str] = ["footage leaves this machine"]
    if not resolved.get("enabled", False):
        warnings.append(
            "cloud render is disabled in config/cloud-render.json (enabled: false) -- "
            "preview only, nothing can be rented until a human enables it")

    def _refusal(error: str) -> dict[str, Any]:
        return {"tool": tool_name, "provider": provider, "would_execute": False, "error": error}

    try:
        offers = vast_client.search(resolved["offer_query"], mode=resolved["pricing_mode"])
    except Exception as exc:  # noqa: BLE001 -- an SDK/network error is a refusal, never a crash
        return _refusal(f"vast_client.search that bai: {exc}")

    eligible = sorted((o for o in offers if o.dph <= resolved["max_dph_usd"]), key=lambda o: o.dph)
    if not eligible:
        return _refusal(
            f"Khong co offer nao <= ${resolved['max_dph_usd']}/h khop query "
            f"{resolved['offer_query']!r}")

    shortlist = eligible[:5]
    recommended = shortlist[0]
    if recommended.gpu_name:
        warnings.append("GPU in the offer is unused -- render is CPU-bound")

    alt_mode = "on-demand" if resolved["pricing_mode"] == "bid" else "bid"
    try:
        alt_offers = vast_client.search(resolved["offer_query"], mode=alt_mode)
        alt_price = min((o.dph for o in alt_offers), default=None)
    except Exception:  # noqa: BLE001 -- alternative price is a nice-to-have, never blocks the announce
        alt_price = None

    render_seconds_per_video_second = float(resolved["render_seconds_per_video_second"])
    if mode == "flush":
        flush_report = queue.flush_check(resolved)
        job_count = flush_report["job_count"]
        render_minutes = flush_report["estimated_render_minutes"]
    else:
        job_count = 1
        duration_seconds = cost_estimate.job_duration_seconds(inputs.get("job_id"))
        render_minutes = round(duration_seconds * render_seconds_per_video_second / 60.0, 2)

    overhead_minutes = queue.OVERHEAD_MINUTES
    dph = recommended.dph
    estimated_cost_usd = round(dph * (overhead_minutes + render_minutes) / 60.0, 4)
    cost_if_rendered_separately_usd = round(
        dph * (overhead_minutes * max(job_count, 1) + render_minutes) / 60.0, 4)

    local_cores = os.cpu_count() or 4
    remote_cores = recommended.cpu_cores_effective or local_cores
    slowdown = max(1.0, remote_cores / max(1, local_cores))
    estimated_local_render_minutes = round(render_minutes * slowdown, 1)

    ceilings = {
        "max_dph_usd": resolved["max_dph_usd"],
        "max_total_usd_per_rental": resolved["max_total_usd_per_rental"],
        "max_runtime_minutes": resolved["max_runtime_minutes"],
    }
    dry_run_ref = dry_run_store.create([offer.id for offer in shortlist], ceilings)

    return {
        "tool": tool_name,
        "provider": provider,
        "would_execute": True,
        "offers": [_offer_dict(offer) for offer in shortlist],
        "recommended_offer_id": recommended.id,
        "pricing_mode": resolved["pricing_mode"],
        "on_demand_alternative_dph_usd": alt_price,
        "kit_size_bytes": cost_estimate.estimate_kit_size_bytes(mode, inputs),
        "jobs": job_count,
        "estimated_render_minutes": render_minutes,
        "estimated_overhead_minutes": overhead_minutes,
        "estimated_cost_usd": estimated_cost_usd,
        "cost_if_rendered_separately_usd": cost_if_rendered_separately_usd,
        "estimated_local_render_minutes": estimated_local_render_minutes,
        "ceilings": ceilings,
        "warnings": warnings,
        "dry_run_ref": dry_run_ref,
    }


def format_announce(payload: dict[str, Any]) -> str:
    """Render a `dry_run()` payload as the announce block
    `skills/core/cloud-render.md` specifies -- the one formatter the skill
    template, the CLI's `--cloud-offers`/render-now/flush paths, and any
    future UI all share, so none of them can drift from `dry_run()`'s actual
    field set or from each other. See that skill's "Field reference" table
    for the field-by-field mapping this function implements.

    Never raises on a malformed/incomplete payload -- a formatting bug must
    not turn a refusal (`would_execute: False`) into a crash that hides the
    refusal reason from the user.
    """
    if not payload.get("would_execute", False):
        return (
            "RENDER LOCATION: cloud (Vast.ai) -- cannot rent\n"
            f"  Reason : {payload.get('error', 'unknown')}"
        )

    offers = payload.get("offers") or []
    recommended_id = payload.get("recommended_offer_id")
    offer = next((o for o in offers if o.get("offer_id") == recommended_id),
                offers[0] if offers else {})
    warnings = payload.get("warnings") or []

    gpu_name = offer.get("gpu_name")
    gpu_text = gpu_name if gpu_name else "no GPU used"
    gpu_note = ("\n                    (GPU is unused -- this render is CPU-bound)"
                if gpu_name and any("GPU" in w for w in warnings) else "")
    geolocation = offer.get("geolocation") or "unknown location"
    cpu_cores = offer.get("cpu_cores")
    cpu_cores_text = f"{cpu_cores:g}" if isinstance(cpu_cores, (int, float)) else cpu_cores
    dph_usd = offer.get("dph_usd")
    dph_text = f"{dph_usd:.4f}" if isinstance(dph_usd, (int, float)) else dph_usd

    on_demand_alt = payload.get("on_demand_alternative_dph_usd")
    on_demand_text = f"${on_demand_alt:.4f}/hr" if isinstance(on_demand_alt, (int, float)) else "n/a"

    jobs = payload.get("jobs") or 1
    scope = f"batch of {jobs} jobs, one rental" if jobs > 1 else "sample of 1 job"

    overhead = payload.get("estimated_overhead_minutes", 0) or 0
    render_minutes = payload.get("estimated_render_minutes", 0) or 0
    total_minutes = round(float(overhead) + float(render_minutes), 2)

    ceilings = payload.get("ceilings") or {}

    lines = [
        "RENDER LOCATION: cloud (Vast.ai) -- needs your approval before I rent anything",
        f"  Offer           : #{offer.get('offer_id')} -- {cpu_cores_text} vCPU "
        f"{gpu_text}, {geolocation}, reliability {offer.get('reliability')}"
        f"{gpu_note}",
        f"  Pricing mode    : {payload.get('pricing_mode')} -- ${dph_text}/hr "
        f"(on-demand alternative: {on_demand_text})",
        f"  Scope           : {scope}",
        f"  Time            : ~{overhead} min overhead + ~{render_minutes} min render(s) "
        f"= ~{total_minutes} min",
        f"  Cost            : ~${payload.get('estimated_cost_usd')} total  |  "
        f"per-rental ceiling: ${ceilings.get('max_total_usd_per_rental')}  |  "
        f"$/hr ceiling: ${ceilings.get('max_dph_usd')}",
        f"  Separately      : ~${payload.get('cost_if_rendered_separately_usd')} if each job "
        "rented its own instance",
        f"  Locally         : ~{payload.get('estimated_local_render_minutes')} min, $0",
        f"  Data            : your footage/render kit ({payload.get('kit_size_bytes')} bytes) "
        "is uploaded to a third-party machine and deleted with the instance.",
        f"  Safety          : instance is destroyed on completion, on error, and by a "
        f"watchdog at the {ceilings.get('max_runtime_minutes')}-minute deadline even if this "
        "process dies.",
    ]
    for warning in warnings:
        if "footage leaves" in warning or "GPU" in warning:
            continue  # already folded into Data/Offer above -- avoid repeating it
        lines.append(f"  Note            : {warning}")
    lines.append(
        "Approve? (yes / use on-demand instead / render locally / pick a different offer)")
    return "\n".join(lines)


def _offer_dict(offer: Any) -> dict[str, Any]:
    return {
        "offer_id": offer.id,
        "dph_usd": offer.dph,
        "cpu_cores": offer.cpu_cores_effective,
        "geolocation": offer.geolocation,
        "reliability": offer.reliability,
        "gpu_name": offer.gpu_name,
    }
