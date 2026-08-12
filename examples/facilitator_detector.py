"""Simple heuristics to classify/detect potentially malicious or self-routing facilitators.
This is intentionally lightweight and testable without external services.
"""
from typing import Dict, Any


def classify_facilitator(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Return a classification with wash_flag boolean and reasons list.

    Expected metrics keys (optional): on_chain_volume_30d (float), reuse_count (int), mediator (str)
    """
    result = {"wash_flag": False, "reasons": []}
    vol = float(metrics.get("on_chain_volume_30d", 0) or 0)
    reuse = int(metrics.get("reuse_count", 0) or 0)
    mediator = str(metrics.get("mediator", "")).lower()

    # Heuristic 1: low volume but very high reuse suggests synthetic routing
    if vol < 1000 and reuse >= 10:
        result["wash_flag"] = True
        result["reasons"].append("low_volume_high_reuse")

    # Heuristic 2: mediator label equals known exchange-owned mediator
    if mediator in ("exchange", "custodial", "internal") and reuse >= 5:
        result["wash_flag"] = True
        result["reasons"].append("mediator_exchange_reuse")

    # Heuristic 3: extreme spike pattern (flag if reuse >> volume)
    if reuse > vol / 100 and reuse > 50:
        result["wash_flag"] = True
        result["reasons"].append("spike_reuse_vs_volume")

    return result
