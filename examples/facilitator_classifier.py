"""Facilitator classification and wash-trade detection heuristics.

Identifies when facilitators are self-operated by the exchange (wash trades),
detects suspicious patterns, and classifies facilitator types.

Heuristics:
  - Ownership chain analysis (same parent entity)
  - Volume correlation (high settlement volume but low on-chain volume)
  - Latency patterns (suspiciously fast settlement)
  - Historical flags and reputation tracking
"""

import json
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
from datetime import datetime, timedelta


class FacilitatorType(Enum):
    """Classification of facilitator types."""
    INDEPENDENT_THIRD_PARTY = "independent_third_party"
    EXCHANGE_OPERATED = "exchange_operated"
    EXCHANGE_AFFILIATE = "exchange_affiliate"
    SUSPECTED_WASH = "suspected_wash"
    UNKNOWN = "unknown"


class RiskLevel(Enum):
    """Risk classification for a facilitator."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class FacilitatorProfile:
    """Profile and risk assessment of a facilitator."""
    facilitator_id: str
    name: str
    facilitator_type: FacilitatorType
    risk_level: RiskLevel
    parent_entity: Optional[str]  # None for independent, exchange name for affiliates
    settlement_method: str  # "on-chain", "custodial", "hybrid"
    key_disclosure_status: str  # "verified", "partial", "none"
    average_settlement_time_ms: float
    historical_flags: List[str]  # ["wash_suspected", "low_volume_correlation", "rapid_settlement", ...]
    last_assessed: str  # ISO 8601 timestamp
    confidence_score: float  # 0.0 to 1.0


def assess_ownership_chain(facilitator: Dict[str, Any], exchanges: Dict[str, Dict[str, Any]]) -> Tuple[Optional[str], bool]:
    """Analyze ownership relationships to detect self-operation.

    Args:
        facilitator: Facilitator data (id, name, parent, operator, etc.)
        exchanges: Dict of known exchanges and their affiliates

    Returns:
        (parent_entity, is_wash_risk) where parent_entity is the owning exchange
        and is_wash_risk indicates high likelihood of self-operation
    """
    fac_parent = facilitator.get("parent", "").lower()
    fac_operator = facilitator.get("operator", "").lower()

    # Direct parent match
    for exchange_name, exchange_data in exchanges.items():
        exchange_lower = exchange_name.lower()

        if fac_parent == exchange_lower or fac_operator == exchange_lower:
            return exchange_name, True

        # Check affiliates
        affiliates = exchange_data.get("affiliates", [])
        for affiliate in affiliates:
            if affiliate.lower() == fac_parent or affiliate.lower() == fac_operator:
                return exchange_name, True

    return None, False


def compute_volume_correlation(
    settlement_volume_usd: float,
    onchain_volume_usd: float,
) -> float:
    """Compute correlation score between settlement and on-chain volume.

    A low score (close to 0) suggests wash trading: high settlement volume but
    minimal confirmed on-chain activity.

    Args:
        settlement_volume_usd: Total value settled by facilitator
        onchain_volume_usd: Total value confirmed on-chain

    Returns:
        Correlation score 0.0 to 1.0 (1.0 = perfect match, 0.0 = no correlation)
    """
    if settlement_volume_usd == 0:
        return 0.0
    if onchain_volume_usd == 0:
        return 0.0

    ratio = onchain_volume_usd / settlement_volume_usd
    # If on-chain matches settlement, correlation is high
    # If on-chain is much lower, correlation is low (wash risk)
    return min(1.0, ratio)


def analyze_settlement_latency(
    settlement_times_ms: List[float],
    threshold_ms: float = 1000,
) -> Tuple[float, bool]:
    """Analyze settlement speed to detect suspiciously fast settlement.

    Fast settlement (< 1 second) may indicate custodial systems or wash trades.

    Args:
        settlement_times_ms: List of settlement times in milliseconds
        threshold_ms: Settlement time threshold for "suspiciously fast"

    Returns:
        (average_time_ms, is_suspicious)
    """
    if not settlement_times_ms:
        return 0.0, False

    avg = sum(settlement_times_ms) / len(settlement_times_ms)
    is_suspicious = avg < threshold_ms

    return avg, is_suspicious


@dataclass
class AssessmentFlags:
    """Flags raised during facilitator assessment."""
    wash_suspected: bool = False
    low_volume_correlation: bool = False
    rapid_settlement: bool = False
    missing_key_disclosure: bool = False
    high_error_rate: bool = False
    reputation_issues: bool = False


def assess_facilitator(
    facilitator: Dict[str, Any],
    exchanges: Dict[str, Dict[str, Any]],
    historical_flags: Optional[Dict[str, Any]] = None,
) -> FacilitatorProfile:
    """Comprehensive assessment of a facilitator.

    Analyzes ownership, volumes, settlement times, and key disclosure to
    assign type, risk level, and flagged concerns.

    Args:
        facilitator: Facilitator record (id, name, parent, volumes, etc.)
        exchanges: Dict of known exchanges for ownership analysis
        historical_flags: Dict of known issues by facilitator_id

    Returns:
        FacilitatorProfile with classification and risk assessment
    """
    fac_id = facilitator.get("id", "unknown")
    fac_name = facilitator.get("name", fac_id)

    flags = AssessmentFlags()
    flags_list = []

    # Ownership analysis
    parent_entity, is_wash_risk = assess_ownership_chain(facilitator, exchanges)
    if is_wash_risk:
        flags.wash_suspected = True
        flags_list.append("wash_suspected")

    fac_type = FacilitatorType.UNKNOWN
    if parent_entity:
        fac_type = FacilitatorType.EXCHANGE_OPERATED if is_wash_risk else FacilitatorType.EXCHANGE_AFFILIATE
    else:
        fac_type = FacilitatorType.INDEPENDENT_THIRD_PARTY

    # Volume correlation
    settlement_vol = facilitator.get("settlement_volume_usd", 0.0)
    onchain_vol = facilitator.get("onchain_volume_usd", 0.0)
    correlation = compute_volume_correlation(settlement_vol, onchain_vol)

    if correlation < 0.3:
        flags.low_volume_correlation = True
        flags_list.append("low_volume_correlation")

    # Settlement latency
    settlement_times = facilitator.get("recent_settlement_times_ms", [])
    avg_time, is_rapid = analyze_settlement_latency(settlement_times)
    if is_rapid:
        flags.rapid_settlement = True
        flags_list.append("rapid_settlement")

    # Key disclosure
    key_status = facilitator.get("key_disclosure_status", "none")
    if key_status == "none":
        flags.missing_key_disclosure = True
        flags_list.append("missing_key_disclosure")

    # Historical flags
    if historical_flags and fac_id in historical_flags:
        for flag in historical_flags[fac_id]:
            flags_list.append(flag)
            if flag == "high_error_rate":
                flags.high_error_rate = True
            elif flag == "reputation_issues":
                flags.reputation_issues = True

    # Determine risk level
    risk_level = RiskLevel.LOW
    if flags.wash_suspected or flags.rapid_settlement:
        risk_level = RiskLevel.CRITICAL
    elif flags.low_volume_correlation or flags.missing_key_disclosure:
        risk_level = RiskLevel.HIGH
    elif flags.high_error_rate or flags.reputation_issues:
        risk_level = RiskLevel.MEDIUM

    # Confidence score (how confident we are in this assessment)
    confidence = 0.5
    if settlement_vol > 0:
        confidence += 0.2  # Have settlement data
    if onchain_vol > 0:
        confidence += 0.1  # Have on-chain verification
    if settlement_times:
        confidence += 0.1  # Have latency data
    if key_status != "none":
        confidence += 0.1  # Have key disclosure info

    return FacilitatorProfile(
        facilitator_id=fac_id,
        name=fac_name,
        facilitator_type=fac_type,
        risk_level=risk_level,
        parent_entity=parent_entity,
        settlement_method=facilitator.get("settlement_method", "unknown"),
        key_disclosure_status=key_status,
        average_settlement_time_ms=avg_time,
        historical_flags=flags_list,
        last_assessed=datetime.utcnow().isoformat() + "Z",
        confidence_score=min(1.0, confidence),
    )


def example_usage():
    """Example: assess facilitators in a manifest."""
    exchanges = {
        "binance": {
            "name": "Binance",
            "affiliates": ["binance_us", "binance_jex"],
        },
        "coinbase": {
            "name": "Coinbase",
            "affiliates": ["coinbase_prime"],
        },
    }

    # Suspicious facilitator (exchange-operated)
    fac1 = {
        "id": "binance_facilitator_1",
        "name": "Binance Settlement",
        "parent": "binance",
        "operator": "binance",
        "settlement_volume_usd": 1000000.0,
        "onchain_volume_usd": 50000.0,  # Low correlation!
        "recent_settlement_times_ms": [100, 150, 200],  # Very fast
        "settlement_method": "custodial",
        "key_disclosure_status": "none",
    }

    # Independent facilitator
    fac2 = {
        "id": "independent_fac_1",
        "name": "Ramp Network",
        "parent": None,
        "settlement_volume_usd": 500000.0,
        "onchain_volume_usd": 480000.0,  # Good correlation
        "recent_settlement_times_ms": [5000, 6000, 4500],  # Normal speed
        "settlement_method": "on-chain",
        "key_disclosure_status": "verified",
    }

    profile1 = assess_facilitator(fac1, exchanges)
    profile2 = assess_facilitator(fac2, exchanges)

    print("Facilitator 1 (Suspicious):")
    print(json.dumps(asdict(profile1), indent=2, default=str))
    print("\nFacilitator 2 (Independent):")
    print(json.dumps(asdict(profile2), indent=2, default=str))


if __name__ == "__main__":
    example_usage()
