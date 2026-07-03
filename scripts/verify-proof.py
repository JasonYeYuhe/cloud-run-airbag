#!/usr/bin/env python3
"""Offline verifier for an Airbag signed proof bundle (v5 Phase 4.2). ZERO network.

    python scripts/verify-proof.py <proof.json> [public_key.pem]

Two independent checks:
  1. INTEGRITY — recompute sha256 over the CANONICAL bundle and compare to the claimed digest (the
     bundle wasn't altered). This works on any bundle, signed or not.
  2. PROVENANCE — verify the Cloud KMS EC_SIGN_P256_SHA256 signature with the committed public key
     (the bundle was produced by the holder of Airbag's KMS identity). Only for signed bundles.

HONESTY: a valid signature proves the bundle's PROVENANCE + integrity — NOT that the decisions
inside were correct. The canonicalization here MIRRORS autosre.proof.build (a deliberate contract
mirror, like infra/alert-setup-v2 mirroring gcp._error_rate_filter) so the verifier needs only
stdlib + `cryptography`, never the agent code. The public key comes from infra/kms-setup.sh.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path


def _canonical(bundle: dict) -> str:
    # MUST match autosre.proof.build's canonicalization exactly.
    return json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str)


def verify(proof: dict, pem_bytes: bytes | None) -> dict:
    """Return {integrity_ok, signature_ok, signed, messages}. signature_ok is None for an unsigned
    bundle or when no public key was supplied. Pure — no I/O, so it is unit-testable."""
    msgs: list[str] = []
    bundle = proof.get("bundle") or {}
    claimed = proof.get("digest", "")
    recomputed = "sha256:" + hashlib.sha256(_canonical(bundle).encode("utf-8")).hexdigest()
    integrity_ok = (recomputed == claimed)
    msgs.append(f"INTEGRITY {'OK' if integrity_ok else 'FAIL'}: {claimed}"
                + ("" if integrity_ok else f" (recomputed {recomputed})"))

    sig = proof.get("signature")
    signature_ok: bool | None = None
    if not sig:
        msgs.append("UNSIGNED: digest-only bundle (no KMS signature to verify)")
    elif not pem_bytes:
        msgs.append("SIGNED but no public key supplied — provenance NOT checked")
    else:
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            pub = serialization.load_pem_public_key(pem_bytes)
            der = base64.b64decode(sig["signature"])
            # KMS signed sha256(canonical); verify the ECDSA over the same canonical bytes.
            pub.verify(der, _canonical(bundle).encode("utf-8"), ec.ECDSA(hashes.SHA256()))
            signature_ok = True
            msgs.append(f"SIGNATURE OK: provenance verified ({sig.get('algorithm')}, key {sig.get('key')})")
        except Exception as e:  # noqa: BLE001
            signature_ok = False
            msgs.append(f"SIGNATURE FAIL: {type(e).__name__}: {e}")

    return {"integrity_ok": integrity_ok, "signature_ok": signature_ok,
            "signed": bool(sig), "messages": msgs}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 64
    proof = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    pem_path = Path(argv[2]) if len(argv) > 2 else Path(__file__).with_name("airbag-proof-pubkey.pem")
    pem = pem_path.read_bytes() if pem_path.exists() else None
    result = verify(proof, pem)
    for m in result["messages"]:
        print(m)
    # exit non-zero on any FAIL (integrity, or a signed bundle whose signature failed)
    if not result["integrity_ok"]:
        return 2
    if result["signed"] and result["signature_ok"] is False:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
