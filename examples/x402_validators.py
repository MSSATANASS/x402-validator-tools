"""Pequeños utilitarios para validar CAIP-2 y manifest x402.
Archivo de ejemplo: no añade dependencias externas y sirve como base para tests.
"""
import json
import re
from typing import Any, Dict, List

CAIP2_RE = re.compile(r"^[a-z0-9]{3,8}:[-_a-zA-Z0-9]{1,32}$")


def is_valid_caip2(chain_id: str) -> bool:
    """Valida forma básica de CAIP-2 (namespace:reference)."""
    if not isinstance(chain_id, str):
        return False
    return bool(CAIP2_RE.match(chain_id))


def validate_manifest_shape(manifest: Dict[str, Any]) -> List[str]:
    """Chequeo muy básico de keys esperadas en /.well-known/x402 manifest.

    Retorna lista de errores (vacía si ok).
    """
    errors: List[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]

    # x402Version required
    if "x402Version" not in manifest:
        errors.append("missing x402Version")

    # accepts must be present and be a list
    accepts = manifest.get("accepts")
    if accepts is None:
        errors.append("missing accepts array")
    elif not isinstance(accepts, list):
        errors.append("accepts must be an array/list")
    else:
        for i, opt in enumerate(accepts):
            if not isinstance(opt, dict):
                errors.append(f"accepts[{i}] must be an object")
                continue
            # Chequear fields mínimos
            for field in ("scheme", "network", "asset", "payTo"):
                if field not in opt:
                    errors.append(f"accepts[{i}] missing field: {field}")
            # network debe ser CAIP-2-like
            net = opt.get("network")
            if net is not None and not is_valid_caip2(net):
                errors.append(f"accepts[{i}].network not valid CAIP-2: {net}")

    # resource is recommended
    if "resource" not in manifest:
        errors.append("missing resource (recommended)")

    return errors


def parse_402_challenge(body: str) -> Dict[str, Any]:
    """Parser tolerante para cuerpos de 402: intenta parsear JSON y devolver dict.

    Lanza ValueError con mensaje accionable si no es JSON o la forma es inválida.
    """
    try:
        obj = json.loads(body)
    except Exception as e:
        raise ValueError("invalid JSON in 402 body: " + str(e))

    if not isinstance(obj, dict):
        raise ValueError("402 body must be a JSON object")

    # Quick sanity: must contain x402Version and accepts
    if "x402Version" not in obj:
        raise ValueError("402 body missing x402Version")
    if "accepts" not in obj:
        raise ValueError("402 body missing accepts array")

    return obj
