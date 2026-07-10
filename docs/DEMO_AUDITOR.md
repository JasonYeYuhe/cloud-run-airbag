# Auditor money-shot — run-of-show (v6 Phase 1)

The marquee beat: next to Airbag's green "healed" card, a **second, independent** Cloud Run service —
the **Auditor** — shows it has *re-verified and counter-signed* the heal, trusting neither Airbag's
word nor its own echo. The auditor demonstrates all **three honest states** (SIGNED-VERIFIED /
INTEGRITY-ONLY / FAIL) live, then a tamper flips a card to **FAIL** on camera.

- **Auditor:** `https://airbag-auditor-43ohujtr3q-an.a.run.app` (status page `/`)
- **Agent:** `https://airbag-agent-43ohujtr3q-an.a.run.app`
- **Project / region:** `airbag-hack-260628` / `asia-northeast1`

The auditor is READ-ONLY / out-of-band — it only GETs the agent's public proof endpoints and signs
its own attestations with its OWN KMS key. It **cannot** block or alter a heal (no write path). The
agent is never touched.

## 0. Before recording (warm + tune)

The auditor defaults to **scale-to-zero** (no cost when idle) — but the background poll loop only runs
while an instance is alive. **Pin it warm and tighten the cadence** for the recording:

```bash
gcloud run services update airbag-auditor --region asia-northeast1 --min-instances 1 \
  --update-env-vars AIRBAG_POLL_INTERVAL_S=5,AIRBAG_MAX_INCIDENTS=8
# (afterwards, scale back to zero: --min-instances 0)
```

Cadence trade-off: the poll cycle ≈ `AIRBAG_MAX_INCIDENTS` sequential fetches + a sign per *changed*
proof (unchanged proofs are cached, so steady-state is fetch-bound). A smaller `MAX_INCIDENTS` → a
tighter flip, but it only audits the most-recent incidents — so make the incident you tamper a
**recent** one (trigger a fresh heal right before, see step 1).

## 1. The three states, live

| State | How to show it | What it says |
|---|---|---|
| 🟢 **SIGNED-VERIFIED** | Trigger a fresh heal (`POST /demo/run` on the agent, token-gated) — it lands at the top of the auditor's list within a poll or two. | provenance + integrity confirmed against the **pinned** agent key `…/airbag-proof/…/1` |
| 🔵 **INTEGRITY-ONLY** | Point at a pinned unsigned incident (`inc-eb3daee9`) if it's still in the window, or an incident healed with `AIRBAG_PROOF_SIGN` briefly off. | honestly unsigned (pre-signing) — integrity holds, provenance unchecked. **The opposite of "verified recovery" marketing.** |
| 🔴 **FAIL** | The tamper beat — step 2. | integrity broke, or the signer isn't who it claims |

## 2. The FAIL beat — `scripts/demo-tamper.sh`

Pick the freshly-healed (SIGNED-VERIFIED) incident id, then tamper its **stored** proof in Firestore.
The auditor's next poll re-fetches it and the card flips to **FAIL** on camera. Two modes:

```bash
# (a) INTEGRITY break — mutate one field of the bundle without updating the digest
INCIDENT=<id> MODE=integrity  PROJECT=airbag-hack-260628 ./scripts/demo-tamper.sh

# (b) SIGNER-PIN — the marquee differentiator: rewrite signature.key to cryptoKeyVersions/2. The
#     signature still verifies AND the bundle is intact, so the STOCK verify-proof.py would echo the
#     new key and PASS — but the auditor's expected-signer PIN rejects the unexpected identity -> FAIL.
INCIDENT=<id> MODE=signer-pin PROJECT=airbag-hack-260628 ./scripts/demo-tamper.sh

# RESTORE (always, before the window ends) — writes the backed-up original proof back
INCIDENT=<id> MODE=restore    PROJECT=airbag-hack-260628 ./scripts/demo-tamper.sh
```

`integrity`/`signer-pin` first back up the original proof to `scripts/.demo-tamper-<id>.json`;
`restore` writes it back. The card returns to SIGNED-VERIFIED within a poll.

**Say the differentiator out loud on the signer-pin beat:** *"the file still has a mathematically
valid signature — the standard offline verifier would accept it. The auditor refuses it, because it
isn't the signer we pinned. No shipped remediation agent proves its actions to an independent
verifier like this."*

## 3. Offline "verify it yourself" (no live infra)

**In the browser** — the **Proof Explorer** (`docs/explorer/index.html`) re-verifies a proof or the
auditor's attestation with WebCrypto, zero network: recomputes the byte-exact canonical digest and
checks the Cloud KMS ECDSA-P256 signature against a public key the judge supplies, with an optional
pinned signer. Serve it (`python -m http.server` in the repo, or link it from the deployed auditor)
and hit the example buttons — real heal → SIGNED-VERIFIED, rogue signer → FAIL, unsigned →
INTEGRITY-ONLY, auditor attestation → SIGNED-VERIFIED — all verified client-side. The byte-exact
canonicalizer (ensure_ascii + number-token preservation) is CI-gated by `docs/explorer/parity-test.js`.

**On the CLI** — a skeptic can verify the auditor's counter-signature with zero network, against the
**committed** public key — the private key never leaves the auditor's own Cloud KMS:

```bash
python3 - <<'EOF'
import json, sys; sys.path.insert(0, "auditor")
import verify
env = json.load(open("docs/proof/auditor-attestation-inc-7d44556f.json"))
key = ("projects/airbag-hack-260628/locations/asia-northeast1/keyRings/airbag/"
       "cryptoKeys/airbag-auditor/cryptoKeyVersions/1")
v = verify.attest(env, expected_pem=open("scripts/auditor-pubkey.pem", "rb").read(), expected_key=key)
print("auditor counter-signature :", v["tri_state"])              # -> SIGNED-VERIFIED
print("the HEAL verdict it attests:", env["bundle"]["tri_state"])  # -> SIGNED-VERIFIED
EOF
```

And the committed **rogue-key FAIL fixture** (`docs/proof/rogue-signer-FAIL-demo.json`) — the real
heal bundle re-signed by a throwaway key that *claims* Airbag's key name — FAILs provenance against
`scripts/airbag-proof-pubkey.pem` (`python scripts/verify-proof.py …` reports SIGNATURE FAIL). This is
the rogue-signer beat, verifiable offline.

## 4. The one headline metric

On the auditor card: **"alert → independently-attested recovery: Xs"** — detection + heal +
independent attestation in a single number (v5's MTTR is the loop; never lead with the loop).

## 5. After recording

```bash
INCIDENT=<id> MODE=restore PROJECT=airbag-hack-260628 ./scripts/demo-tamper.sh   # if you tampered
gcloud run services update airbag-auditor --region asia-northeast1 --min-instances 0  # scale to zero
```

Leave the agent's demo baseline HEALTHY (airbag-target-00024 newest/healthy at 100%), as always.
