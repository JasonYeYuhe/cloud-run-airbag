#!/usr/bin/env python
"""Generate a golden DSSE heal-attestation for the cosign-in-CI HARD GATE (v6 Phase 1.2, Round 3 #9).

Builds the SAME DSSE construction the live KMS path emits (agent/autosre/dsse.py), signed here with a
LOCAL EC-P256 key — a faithful proxy for Cloud KMS EC_SIGN_P256_SHA256 (identical DER + PAE), so
`cosign verify-blob-attestation` can gate our hand-rolled DSSE with NO GCP. Writes three files the
cosign command consumes:
  - heal.intoto.dsse.json  : the DSSE envelope  (cosign `--signature`)
  - canonical-bundle.json  : the blob whose sha256 == the statement subject  (cosign positional)
  - cosign.pub             : the EC public key  (cosign `--key`)

CI generates a FRESH key each run (gating the current dsse.py end-to-end). The committed copy under
docs/proof/dsse-golden/ was produced ONCE with a throwaway key (private discarded, like the rogue-key
fixture) — a demo artifact + the input to test_dsse.test_committed_golden_is_self_consistent.

Usage:  python scripts/gen_dsse_golden.py --out-dir <dir> [--key priv.pem]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agent"))

from autosre import dsse, proof  # noqa: E402  (LLM-free construction modules; path set above)
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, utils  # noqa: E402

# A representative healed incident — mirror of the live latency-heal shape (carries bundle_version).
_REC = {
    "incident_id": "inc-dsse-golden", "service": "airbag-target", "status": "mitigated",
    "decision": {"action": "ROLLBACK", "confidence": 0.9, "reasoning": "latency regression",
                 "_source": "gemini-adk"},
    "error_before": 1.0, "error_after": 0.0, "rolled_back_to": "airbag-target-00013",
    "events": [{"stage": "RECEIVED", "ts": 100.0},
               {"stage": "ANALYZED", "ts": 101.0, "verdict": "FAIL", "reason": "latency 4/4"},
               {"stage": "MITIGATED", "ts": 130.0}],
}


def _local_signer(priv):
    """KMS-EC_SIGN_P256_SHA256 mimic: sign the PROVIDED sha256(PAE) digest (Prehashed) -> base64 DER."""
    def sign(digest: str):
        raw = bytes.fromhex(digest.split(":", 1)[-1])
        der = priv.sign(raw, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        return {"signature": base64.b64encode(der).decode(), "key": "local-ec-golden"}
    return sign


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--key", help="EC-P256 private key PEM (a fresh key is generated if omitted)")
    args = ap.parse_args()

    if args.key:
        priv = serialization.load_pem_private_key(pathlib.Path(args.key).read_bytes(), password=None)
    else:
        priv = ec.generate_private_key(ec.SECP256R1())

    built = proof.build(_REC)
    bundle = built["bundle"]
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str)
    # the blob cosign hashes MUST be byte-identical to what proof.build digested
    assert "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest() == built["digest"]
    subject_hex = built["digest"].split(":", 1)[-1]

    env = dsse.build_dsse(bundle, _REC["incident_id"], subject_hex, signer=_local_signer(priv))
    if env is None:
        print("ERROR: build_dsse returned None (signer declined)", file=sys.stderr)
        return 1

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "canonical-bundle.json").write_text(canonical)                    # blob: NO trailing newline
    (out / "heal.intoto.dsse.json").write_text(json.dumps(env, indent=2) + "\n")
    (out / "cosign.pub").write_bytes(priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    print(f"wrote DSSE golden to {out}/ (subject sha256:{subject_hex[:16]}…)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
