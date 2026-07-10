#!/usr/bin/env bash
# The money-shot's live FAIL beat (v6 Phase 1). Tamper ONE incident's STORED proof in Firestore so the
# independent auditor's next poll flips that card to FAIL on camera — then RESTORE it. This is a
# read-modify-restore on a single `incidents/<id>` doc's `proof` field; it NEVER redeploys the agent,
# changes its flags, or touches the target baseline (all within the live guardrails).
#
#   INCIDENT=<id> MODE=integrity  PROJECT=<gcp> ./scripts/demo-tamper.sh   # break the bundle digest
#   INCIDENT=<id> MODE=signer-pin PROJECT=<gcp> ./scripts/demo-tamper.sh   # claim an UNEXPECTED key ver
#   INCIDENT=<id> MODE=restore    PROJECT=<gcp> ./scripts/demo-tamper.sh   # put the original back
#
# The two beats:
#   integrity  — mutates one field inside the bundle WITHOUT updating the digest -> INTEGRITY FAIL.
#   signer-pin — rewrites signature.key to cryptoKeyVersions/2. The signature still verifies against
#                the pinned key AND the bundle is intact, so the STOCK verify-proof.py would echo the
#                new key and PASS — but the AUDITOR's expected-signer PIN rejects the unexpected
#                identity -> FAIL. This is the marquee differentiator, shown live.
#
# SAFETY: integrity/signer-pin first BACK UP the original proof to scripts/.demo-tamper-<id>.json;
# `restore` writes that backup back. ALWAYS restore before the demo window ends. Mutates LIVE Firestore.
set -euo pipefail
: "${INCIDENT:?set INCIDENT=<incident_id>}"
: "${PROJECT:?set PROJECT=your-gcp-project}"
MODE="${MODE:-integrity}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BACKUP="${HERE}/.demo-tamper-${INCIDENT}.json"
PYBIN="$(cd "${HERE}/.." && pwd)/.venv-demo/bin/python"
[[ -x "$PYBIN" ]] || PYBIN="python3"   # fall back to system python if the demo venv is absent

"$PYBIN" - "$INCIDENT" "$MODE" "$PROJECT" "$BACKUP" <<'PY'
import copy
import json
import os
import sys

from google.cloud import firestore

incident, mode, project, backup = sys.argv[1:5]
ref = firestore.Client(project=project).collection("incidents").document(incident)
snap = ref.get()
if not snap.exists:
    sys.exit(f"incident {incident} not found")
rec = snap.to_dict()

if mode == "restore":
    try:
        original = json.load(open(backup))
    except FileNotFoundError:
        sys.exit(f"no backup at {backup} — nothing to restore")
    ref.update({"proof": original})                            # REPLACE the proof field with the original
    os.remove(backup)                                          # clear the backup so the next tamper starts clean
    print(f"RESTORED proof for {incident} from {backup} (backup cleared)")
    sys.exit(0)

# refuse to overwrite an existing backup — that would lose the ONLY copy of the original proof
if os.path.exists(backup):
    sys.exit(f"backup already exists at {backup} — the incident may already be tampered; "
             f"run MODE=restore first (or delete the backup if you are sure it is stale)")
proof = rec.get("proof")
if not proof:
    sys.exit(f"incident {incident} has no STORED proof to tamper (it is served live) — pick a signed one")
with open(backup, "w") as f:                                   # back up the ORIGINAL before mutating
    json.dump(proof, f)
print(f"backed up original proof -> {backup}")

t = copy.deepcopy(proof)
if mode == "integrity":
    rb = (t.get("bundle") or {}).get("recovery") or {}
    t.setdefault("bundle", {}).setdefault("recovery", {})["rolled_back_to"] = \
        (rb.get("rolled_back_to") or "x") + "-TAMPERED"
    print("MODE integrity: mutated bundle.recovery.rolled_back_to; digest now stale -> INTEGRITY FAIL")
elif mode == "signer-pin":
    sig = t.get("signature")
    if not sig:
        sys.exit("incident is unsigned — signer-pin needs a signed proof; use MODE=integrity")
    sig["key"] = sig["key"].replace("cryptoKeyVersions/1", "cryptoKeyVersions/2")
    print("MODE signer-pin: envelope now CLAIMS cryptoKeyVersions/2 -> the auditor's PIN FAILs it "
          "(the stock verifier would echo it and PASS)")
else:
    sys.exit(f"unknown MODE={mode} (use integrity | signer-pin | restore)")

ref.update({"proof": t})                                       # REPLACE the proof field with the tampered one
print(f"TAMPERED {incident} — the auditor's next poll flips to FAIL. "
      f"Restore: INCIDENT={incident} MODE=restore PROJECT={project} ./scripts/demo-tamper.sh")
PY
