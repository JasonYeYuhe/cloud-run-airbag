# Live-heal proof bundles (v4, captured 2026-07-02, agent rev 00033/00034)

Two REAL incidents from the live demo service (`airbag-hack-260628` / `asia-northeast1`), captured
verbatim from `/incidents/{id}/proof` — the tamper-evident bundle Airbag persists for every run.

| File | Incident | What it proves |
|---|---|---|
| [`live-5xx-heal-recency.json`](live-5xx-heal-recency.json) | `inc-eb3daee9` | 💣 5xx Break→Heal: detection FAIL (20/20 5xx, latency PASS), the deterministic promotion drove the rollback Gemini hedged on (`_target_source: recency` — the ledger was cold on its first v4 run), the live causal probe cleared the target (0/8), recovery verified, and the mitigation target was **witnessed into the live Firestore ledger**. |
| [`live-latency-heal-ledger.json`](live-latency-heal-ledger.json) | `inc-7d266764` | 🐢 latency Break→Heal: the latency detector FAILs (4/4 windows over SLO; 5xx quiet), the rollback is aimed via the **serving-history ledger** (`_target_source: ledger` — witnessed-good, not merely newest), the **latency-keyed** causal probe reports `0/8 errs, 0/8 slow` (the v4 `{errs,total,slow}` axis, cold-start rinsed), recovery is proven on the latency signal, and **no code-fix PR is fabricated** (a latency regression is remedied by the rollback). |

**How to verify a bundle** (the digest proves integrity, not authorship — stated in each bundle's
`note`): recompute `sha256` over the canonical JSON of the `bundle` field and compare to `digest`:

```bash
python3 - <<'EOF'
import hashlib, json
d = json.load(open("docs/proof/live-latency-heal-ledger.json"))
canonical = json.dumps(d["bundle"], sort_keys=True, separators=(",", ":"), default=str)
print("recomputed:", "sha256:" + hashlib.sha256(canonical.encode()).hexdigest())
print("committed: ", d["digest"])
EOF
```

Context worth knowing (honest): between these two runs, a THIRD live incident showed Gemini
hallucinating "100% 5xx" during the latency scenario and aiming the rollback at the 5xx-landmine
revision — the causal probe vetoed it safely (escalated, zero traffic shifted). That live catch
motivated the v4 FSM **re-aim** (an LLM aim with no witnessed history is substituted with the
witnessed candidate, only when the live probe is on to gate it), which shipped in rev 00034 and is
exercised by `test_llm_wrong_aim_heals_end_to_end_via_reaim`.

## v5 — cryptographically SIGNED proof (captured 2026-07-04, agent rev 00041)

| File | Incident | What it proves |
|---|---|---|
| [`live-kms-signed-latency-heal.json`](live-kms-signed-latency-heal.json) | `inc-7d44556f` | 🔏 A REAL live latency heal whose bundle is **Cloud KMS-signed** (`EC_SIGN_P256_SHA256`, Phase 4.2) — provenance, not just integrity. It also carries the v5.3 **`revision_delta`** ("what changed" spec diff) *inside the signed bundle*: for this demo the delta is honestly empty (`image_changed:false`, no env/limit change) because the demo's `slow` fault is a runtime `FAULT_MODE` env-**value** toggle on the **same image** — a real bad-image deploy would show `image_changed:true`. |

**Verify it offline** (zero network — recomputes the digest AND checks the KMS signature against the
committed public key `scripts/airbag-proof-pubkey.pem`; the private key never leaves Cloud KMS):

```bash
python scripts/verify-proof.py docs/proof/live-kms-signed-latency-heal.json
# -> INTEGRITY OK …  /  SIGNATURE OK: provenance verified (EC_SIGN_P256_SHA256, key …/airbag-proof/…/1)
```

Live verification run (2026-07-04, agent rev 00041, `airbag-hack-260628` / `asia-northeast1`): **1.1**
storm-coalesce (5 distinct-id alerts → 1 leader + 4 attached), **1.2** observer-safe (10 marked probe
5xx excluded from the log-scan count, 10 user 5xx kept), and **4.2** KMS-signed proof (this bundle,
verified offline incl. a tamper negative-control) all passed. The demo baseline was left HEALTHY.

## v6 — INDEPENDENTLY counter-signed attestation (captured 2026-07-09)

The v6 marquee: a SECOND, adversarially-independent Cloud Run service — the **Auditor** — polls the
agent's public `/incidents/{id}/proof`, re-verifies each heal against a **PINNED** signer identity
(refusing even a cryptographically-valid signature from an *unexpected* key version — the stock
`verify-proof.py` only echoes the claimed key), and **counter-signs its verdict with its OWN, distinct
Cloud KMS key** (`airbag-auditor`, NEVER the agent's `airbag-proof`).

| File | Incident | What it proves |
|---|---|---|
| [`auditor-attestation-inc-7d44556f.json`](auditor-attestation-inc-7d44556f.json) | `inc-7d44556f` | 🛡 The deployed auditor's **counter-signed attestation** of the v5 KMS-signed heal above. It independently re-verified the heal → **SIGNED-VERIFIED** against the pinned agent key, and BINDS THE FETCH CONTEXT it verified under (`fetch.raw_fetched_digest` of the exact bytes, `agent_url`, `requested_incident_id`, HTTP status, and a `bundle.incident_id == requested-id` check). Its `subject_digest` equals the heal bundle's own `digest`. The attestation is itself **offline-verifiable** against the committed auditor pubkey. |
| [`rogue-signer-FAIL-demo.json`](rogue-signer-FAIL-demo.json) | `inc-7d44556f` (bundle) | 🚫 The money-shot's **rogue-signer FAIL** fixture: the real heal bundle re-signed by a **throwaway** keypair that *claims* Airbag's KMS key resource name. Integrity is intact but PROVENANCE FAILs against `scripts/airbag-proof-pubkey.pem` — a valid-looking signature from an unauthorized signer is rejected. Verify: `python scripts/verify-proof.py docs/proof/rogue-signer-FAIL-demo.json` → SIGNATURE FAIL. The throwaway private key is intentionally discarded (never committed). |

**Verify the auditor's counter-signature offline** (zero network — the same kernel, the auditor's
committed public key `scripts/auditor-pubkey.pem`; the auditor's private key never leaves its Cloud KMS):

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

Live verification run (2026-07-09, `airbag-hack-260628` / `asia-northeast1`): the auditor
(`airbag-auditor`, a 2nd Cloud Run service under its OWN zero-role SA with `signerVerifier` on the
auditor key ONLY) independently attested 25 real incidents — the KMS-signed heal as
**SIGNED-VERIFIED**, the unsigned recency heal (`inc-eb3daee9`) honestly as **INTEGRITY-ONLY** — and
counter-signed each with its own KMS identity. This is the on-camera FLOOR: an independent party
proving Airbag's heal, trusting neither Airbag's word nor its own echo. The agent was **untouched**
(read-only, out-of-band — the auditor has no write path into it).
