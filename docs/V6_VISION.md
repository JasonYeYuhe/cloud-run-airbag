# Airbag v6 — Vision & Development Plan (finals week)

> Plan of record for the finals stage. Produced by a grounded planning workflow (5 code/market
> fact-finders → 6 first-principles ideation lenses → dedup → per-bet refute-by-default verification):
> **25 raw bets → 11 deduped → 9 survived both adversarial lenses, 2 refuted (§5)** — the survivors are
> transcribed here against the REAL code seams (every file/symbol re-verified against current code before
> commit). Window:
> **finals dev 2026-07-30 → 2026-08-19** (~3 weeks), **one human**. Submission 2026-07-10 is DELIVERY
> only (video/deck/writeup) — no dev. This plan assumes v5 shipped + live-verified on real Cloud Run
> (agent rev 00041, commit 8677901, 347 tests).

## §0. Adversarial review (to be incorporated at kickoff)
The v5 discipline (§0 Gemini 3.1 Pro pass) repeats here: run the marquee (Phase 1 Auditor) past
`agy` Gemini 3.1 Pro and/or a multi-agent refute-by-default workflow BEFORE the first substantial
commit. Findings already folded from the pre-drafting refutation pass:
1. *(BLOCKER — folds into 1.1)* A verbatim re-run of `verify-proof.py:verify()` against the same
   committed PEM is the part closest to **provenance-theater** — it re-proves what the agent already
   published. The **differentiated, un-owned value is the auditor REFUSING a cryptographically-valid
   signature from an unexpected signer identity.** Confirmed in code: `verify()` (verify-proof.py:57)
   only *echoes* `sig.get("key")` — it does NOT compare it to an expected key. So the signer-identity
   PIN is genuinely NEW work and MUST live inside the auditor's verify path, not a fetch-time string
   compare a refactor could drop. This is the headline, not the re-run.
2. *(MAJOR — folds into 1.1)* The auditor's counter-signature is itself unverifiable unless its
   public PEM is committed — otherwise the cross-sign reduces to theater. Commit `auditor-pubkey.pem`;
   canonicalize + sign the attestation; make it offline-verifiable by the same kernel.
3. *(MAJOR — folds into P4/the Proof Explorer)* The canonical JSON uses Python `json.dumps(...,
   ensure_ascii=True)`. The one committed signed fixture contains 3 em-dashes → the canonical is 2244
   bytes with `\uXXXX` escapes. A naive JS/second-consumer canonicalizer emits raw UTF-8 (2223 bytes)
   and would show **INTEGRITY FAIL on a genuinely valid heal** — the worst failure mode for a
   verify-it-yourself surface. Any third canonicalization consumer needs a byte-parity test + a
   `bundle_version` field before it ships.
4. *(MAJOR — folds into 2)* The one committed live signed bundle reads `image_changed: false` (verified)
   because the demo faults are `FAULT_MODE` env-value toggles on the SAME image — the 5.3 headline
   evidence field is provably dead in the artifact. Exercise it once, for real, or don't narrate it.
5. *(MAJOR — folds into 3)* `mcp_remote.py` imports `apply_approval` from `state_machine` (which
   imports the LLM), so it can NEVER go in `_action_files()`. The governance logic MUST be a SEPARATE
   pure module (`mcp_governance.py`) or the LLM-free guarantee for the new authz code is unenforceable.
6. *(MINOR — folds into 4.2-hardening)* `sign_digest`'s httpx timeout is 15s AND there is a second
   unbounded network call (`creds.refresh`) in the same fail-open path; a KMS/token hang at the
   terminal MITIGATED stamp can extend settlement. Bound both; keep the same fail-open.

## 1. Executive summary
The arc: **v2** made the agent durable and governed; **v3** made **detection** trustworthy; **v4**
made the one reversible **action** provably correct; **v5** made the agent **safe around itself**
(storm-safe autonomy — coalesce the alert storm, mark its own probes, coalesce approvals, sign the
proof). **v6 closes the last credibility gap: a claim of a safe, correct heal is only as good as an
INDEPENDENT PARTY'S ability to verify it.**

The market likely moved under us since submission and the finals framing must move with it. Per the
best-effort recon, by mid/late 2026 autonomous prod remediation is **no longer white space** — the major
vendors (Azure SRE Agent, Datadog Bits AI SRE, et al.) are shipping in-platform autonomous remediation,
all **guardrailed/approval-gated**. A judge who hears "we act on prod" in August may call it **table
stakes**. ⚠️ *The specific vendor/quantitative claims in this section are best-effort recon on a
fast-moving field and are NOT independently verified — RE-VERIFY every competitive fact + citation on
day 1 of finals (§8 Q5) before any of it goes in the deck.*

Two gaps remain un-owned by every vendor, and Airbag already half-owns both:
- **Agent self-safety / the observer effect** — no vendor names or addresses an agent amplifying its
  own incident. v5 shipped the mitigations (storm lease 1.1, observer-safe probes 1.2, approval
  coalescing 1.3, the storm scorecard).
- **Cryptographic provenance + INDEPENDENT attestation of a remediation agent's actions** — per the
  recon, cross-agent identity + attestation (the A2A protocol, DID/VC-for-agents) remains largely
  unshipped in remediation tooling (re-verify the specifics at kickoff). Airbag's v5 4.2 KMS-signed
  proof + a **deterministic auditor** is a concrete, shipped instance of exactly this open problem.

v6's thesis: **provable autonomy — the only Cloud Run remediation agent that is safe around ITSELF
*and* can prove, to an independent, LLM-free auditor, exactly what it did.** (The recon claims recent
agent-safety research independently echoes Airbag's separate-the-reasoning-from-a-deterministic-
execution-layer invariant — a strong deck point IF true; verify the specific paper before citing it.)

## 2. The gaps v6 closes (grounded in code + live findings)
- **(a) Provenance is single-bundle and self-attested.** v5 4.2 signs each bundle, and the offline
  `verify()` (verify-proof.py:31-63) does two real checks — INTEGRITY (recompute sha256 over
  `_canonical(bundle)`) + PROVENANCE (ECDSA-P256 over the committed PEM). But **verify() never pins
  the SIGNER**: line 57 only *echoes* `sig.get("key")`. A re-keyed or rogue signer with its own valid
  keypair passes. And the whole thing is Airbag verifying Airbag — no second party, no independent
  identity. → **Phase 1 (Auditor Agent, the marquee)**
- **(b) No tamper-evident ORDERING across incidents.** Proofs live in MUTABLE per-incident Firestore
  docs (`incidents.record` merge-updates one doc keyed by incident_id; a doc overwrite silently
  replaces the snapshot). `grep append-only|merkle|hash.chain|prev_digest|transparency` across the repo
  returns NOTHING. So an auditor can attest ONE bundle but cannot detect a **deleted, reordered, or
  back-dated** incident. → **Phase 2 (transparency log, the auditor's spine)**
- **(c) The trust anchor is a committed FILE and the remote MCP can't serve a proof.** The verify key
  is `scripts/airbag-proof-pubkey.pem` (exported by **infra/kms-setup.sh:43-46**); there is NO
  `/.well-known/`, `/pubkey`, or JWKS route in app.py. And the remote MCP registers exactly **7 tools**
  and omits `airbag_incident_proof` — the signed bundle is reachable remotely only via the raw GET.
  A judge holding a `proof.json` must find the repo to verify it. → **Phase 3 (served anchor + remote
  proof tool)**
- **(d) Provenance you can't SEE.** `verify-proof.py` is a CLI; a skeptic must run Python. There is no
  zero-network in-browser surface a judge drops a proof into. → **Phase 4 (offline Proof Explorer)**
- **(e) Remote MCP is one all-or-nothing token.** `BearerGate` (mcp_remote.py:98-118) is a single
  `hmac.compare_digest` against `config.MCP_TOKEN`; any holder can `airbag_set_autonomy('L3')`,
  `airbag_trigger_heal`, or `airbag_approve`. `airbag_trigger_heal` (mcp_remote.py:62-69) mints
  `mcp-<uuid8>` and calls `queue.enqueue_heal` DIRECTLY (bypassing the 1.1 lease when
  `STORM_COALESCE` is off); `airbag_set_autonomy` → `autonomy.set_level`, which **erases the demotion
  breadcrumb and can jump L1→L3**, bypassing the trust ramp. → **Phase 5 (remote-MCP governance tiers)**
- **(f) Live-finding (a): the crashed-leader dead-lease window.** `claim_service_heal`
  (state_store.py:242) writes `lease_until = now + 900s` ONCE and NEVER refreshes it during a running
  heal (only claim/settle write it; verified). `_service_heal_live` is purely `lease_until > now`. A
  leader whose Cloud Run instance is KILLED mid-heal holds the per-service lease for ~15 min while every
  follower `_attach_to_leader`s to the corpse (state_machine.py:55 even `finish_heal`s the follower so
  its redelivery no-ops). A still-broken service gets ZERO remediation. → **Phase 6 (heartbeat + fencing)**
- **(g) Live-finding (b): burn detector self-DoS.** `sample_error_windows` (backends/gcp.py:222) is two
  nested SERIAL loops of blocking httpx GETs — `BURN_WINDOWS(6) × BURN_PER_WINDOW(50) = 300` sequential
  requests, each `timeout=5.0`, NO break/deadline/concurrency; `_detect_burn` pools only AFTER all
  sampling. On a slow fault every request nears the SLO, so the burst dominates the heal (~10 min
  observed) — which is why the live set is pinned to `AIRBAG_SIGNALS=5xx,latency` (burn opt-out).
  → **Phase 7 (bounded burn sampling)**
- **(h) Live-finding (c): revision-delta's headline field is empty.** `revision_delta.diff`
  (revision_delta.py:38) computes `image_changed = bad_img != tgt_img`, already riding the signed
  bundle — but the demo faults are `FAULT_MODE` env-value toggles on ONE image, so the only committed
  signed bundle reads `image_changed: false` (verified). The "what changed" forward story is
  perpetually blank. → **Phase 8 (bad-image fault fixture)**

## 3. Phased plan (each item: LLM-free in the action tier, flag-gated default-OFF unless noted, TDD'd,
adversarially reviewed before commit, demo left HEALTHY)

### Phase 1 — Auditor Agent: independent A2A cryptographic attestation (THE marquee) — ~4d nominal
A **SECOND Cloud Run service** (`auditor/`, its OWN least-privilege SA + its OWN KMS key) that polls
the agent's public `GET /incidents/{id}/proof` (app.py:92-104 — no agent change for the FLOOR), lifts
`scripts/verify-proof.py:verify()` VERBATIM as its kernel (pure, stdlib+`cryptography` only, zero agent
imports — deterministic + LLM-free by construction), and returns an HONEST tri-state, then
**counter-signs its own attestation** — a different agent, with a distinct identity, vouching for the
heal, trusting neither Airbag's word nor its own.

1.1 **Auditor verify core + signer-identity PIN** *(1.5d)* — new `auditor/verify.py` = the lifted
    kernel PLUS the differentiator the refutation demanded: an **expected-signer allowlist**. The
    auditor pins BOTH (i) the committed `airbag-proof-pubkey.pem` (offline anchor, authoritative) AND
    (ii) the expected `signature.key` full `cryptoKeyVersions/N` resource name (embedded in every real
    envelope — verified: `.../airbag-proof/cryptoKeyVersions/1`). Fold the equality check INTO the
    verify path so a **cryptographically-valid signature from an unexpected key version FAILS
    attestation** — separation of duties enforced by the crypto path, not a bolt-on string compare.
    Honest tri-state: **SIGNED-VERIFIED** (integrity_ok AND signature_ok AND signer pinned) /
    **INTEGRITY-ONLY** (unsigned/pre-4.2, `signature_ok=None` — e.g. `live-5xx-heal-recency.json`) /
    **FAIL**. Never surface "verified correct" — provenance + integrity only (mirrors proof.py:88).
    *Proof (TDD, lifts test_proof_sign.py's baseline):* valid→SIGNED-VERIFIED; tamper one byte INSIDE
    `bundle` (e.g. `rolled_back_to`, NOT the outer `note`)→FAIL; wrong keypair→FAIL; **valid signature,
    wrong `cryptoKeyVersions/2`→FAIL** (the NEW case, absent from v5's wrong-*keypair* test);
    unsigned→INTEGRITY-ONLY.
1.2 **Auditor's second KMS identity + counter-signed attestation** *(1d)* — clone `infra/kms-setup.sh`
    into `infra/auditor-kms-setup.sh`: keyring `airbag`, a NEW key `airbag-auditor`, `signerVerifier`
    granted to the AUDITOR SA ONLY (NEVER reuse `airbag-proof` — independence is load-bearing; the
    script is already parameterized via `AIRBAG_KMS_KEYNAME`/`AGENT_SA`). The auditor canonicalizes an
    attestation `{incident_id, subject_digest, tri_state, verified_at, expected_key}`, KMS-signs it, and
    commits `scripts/auditor-pubkey.pem` so the attestation is itself offline-verifiable by the same
    kernel. Attestation is READ-ONLY and out-of-band — it NEVER writes to the heal FSM. **FAIL-OPEN:** a
    failed/unreachable attestation surfaces as "unattested"; it structurally cannot block a heal (no
    write path in).
1.3 **New-service infra + own AST guard** *(1d)* — `auditor/Dockerfile` + `gcloud run deploy` with a
    zero-role SA (mirror the shipped `sandbox-job/` + `infra/sandbox-job-setup.sh` new-service
    precedent). The auditor lives OUTSIDE the agent's `_action_files()` AST scan (which globs `autosre/`
    only), so ship `auditor/tests/test_auditor_invariant.py` mirroring `_FORBIDDEN`/`_offending` — or
    the LLM-free guarantee for the new service is merely true, not enforced.
0.5d **The money shot + honest coverage** — next to Airbag's green "healed" card, the independently
    deployed auditor (different URL + identity) shows "AUDITOR: inc-X **SIGNED-VERIFIED** — provenance
    confirmed against pinned key `.../cryptoKeyVersions/1`, integrity OK". Demo ALL THREE states: point
    at a real pre-4.2 unsigned bundle → **INTEGRITY-ONLY** shown as a first-class honest outcome (the
    strongest anti-hype signal — the opposite of Azure's "verified recovery" marketing); tamper a byte
    inside `bundle` live → **FAIL** flips on camera; swap the signer → FAIL flips. Commit one real
    cross-attested heal to `docs/proof/`.
    *Flag posture:* the agent side needs NO flag (the proof GET is already public); the auditor is
    simply **not-deployed by default** — the strongest form of default-OFF, and the recorded demo stays
    byte-identical.

### Phase 2 — Hash-chained transparency log: the spine the auditor walks — ~2d nominal (STRETCH #1)
The auditor graduates from "verify ONE bundle" to "walk the whole history, prove no incident was
deleted, reordered, or back-dated." New LLM-free `autosre/transparency.py` with `append(entry)` that,
inside ONE `state_store.transact()` on a `log_head` doc, reads `prev_entry_hash`, computes
`entry_hash = sha256(canonical({seq, prev_entry_hash, incident_id, service, bundle_digest, signature,
ts}))`, and writes BOTH the head pointer and an immutable `log_entries/{seq}` doc in the same transact.
Called from the ALREADY flag-gated + fail-open `_persist_proof` (state_machine.py:723-736), inheriting
its `try/except`. The auditor walks the chain, recomputes every link, confirms no seq gaps, confirms
each `bundle_digest` matches its committed signature, and **counter-signs the chain HEAD** (not
per-entry — per-entry would couple the heal path to a live auditor and break fail-open).
- *Design decision spec'd up front (folded from refutation):* `_persist_proof` fires at MULTIPLE
  terminal transitions for one incident (MITIGATED at 365/387/517 AND CLOSED at 628). Append **BOTH**
  links (seq N = mitigated snapshot, seq N+1 = closed snapshot) — most honest, most tamper-evident;
  the auditor must accept two entries sharing an incident_id.
- *Retry-safety (folded):* the Firestore `@transactional` path re-runs the mutator on contention.
  Compute `entry_hash` from the head read INSIDE the mutator (never captured outside); if `log_head`
  already advanced past this incident's committed entry, return `KEEP` (idempotent, no dup seq).
- *Honesty:* the chain ALONE proves internal consistency; a single writer controlling both chain and
  key only proves "consistent with itself." The genuine independent teeth are the AUDITOR's SEPARATE
  KMS identity counter-signing the head — gate the "no incident deleted/reordered/back-dated" claim on
  that counter-signed checkpoint, never on the chain in isolation.
- *Flag `AIRBAG_TRANSPARENCY_LOG` (default OFF) → no log doc, proof snapshot byte-identical.* Add
  `transparency.py` to `_action_files()` in the SAME commit that adds the module (mirror
  `revision_delta.py`).
- *Proof:* append 3 heals → chain verifies; tamper one entry's bundle → link break; delete seq=2 → gap;
  reorder → prev-hash mismatch; mitigate-then-close → TWO valid chained links for one incident_id;
  mutator re-run → no dup/skipped seq; flag-off → byte-identical.

### Phase 3 — Served trust anchor + remote proof tool: make the auditor a first-class A2A peer — ~1d nominal
Two small, read-only, LLM-free API-tier seams the auditor (and any third party) needs.
- **Served anchor** — `GET /.well-known/airbag-proof-pubkey.pem` serving the committed PEM bytes, plus
  a versioned key **registry** `{key_version_resource_name, algorithm, not_before, status:active|retired}`
  generated at setup time (extend `infra/kms-setup.sh` to emit `registry.json` alongside the PEM — NOT a
  live KMS call in the request path). Because every envelope embeds the full `cryptoKeyVersions/N`, a
  verifier maps an old heal's `signature.key` to the RIGHT (possibly retired) key — rotation-ready
  non-repudiation. *Honesty:* the COMMITTED PEM pinned offline is authoritative; the served endpoint is
  a convenience for parties without the repo (never let a served key on the audited service become the
  trust root a MITM could swap). Handle the unsigned/no-key case gracefully (serve the anchor, state
  provenance is unchecked).
- **Remote proof tool** — port `airbag_incident_proof` (exists on the stdio proxy,
  mcp-server/airbag_mcp.py:88-94) to `mcp_remote.py`, a one-tool read-only addition making the auditor a
  first-class MCP peer. DELIBERATELY flip `test_mcp_remote.py`'s locked 7-tool set → 8.
- *Framing:* NOT a headline — a supporting seam sequenced AFTER the Phase-1 verify core, its whole value
  being what the auditor pulls over A2A/MCP. A brand-new route serving public bytes is inherently
  additive (byte-identical demo); keep `AIRBAG_PUBKEY_ROUTE` for cadence-consistency but the route's
  additivity — not the flag — is what preserves the demo.
- *Proof:* served PEM bytes == committed file byte-for-byte; `verify-proof.py` accepts a real bundle
  using the SERVED key (round-trip, registry can't drift); a retired `cryptoKeyVersions/N` resolves to
  its PEM via the registry; the 7→8 tool-count contract test.

### Phase 4 — Public offline Proof Explorer: verify-in-browser (zero network, judge-runnable) — ~2–2.5d nominal (STRETCH #2)
A single static HTML/JS page re-implementing `verify()`'s two checks in **WebCrypto** (`crypto.subtle.
digest` sha256; `crypto.subtle.verify` ECDSA P-256/SHA-256 over the SAME canonical bytes): drop a
`proof.json` + the pubkey, see SIGNED-VERIFIED / INTEGRITY-ONLY / FAIL, and (when fed a transparency-log
export) walk the chain. Renders the committed `docs/proof/*.json` fixtures verbatim so the finals video
shows an independent client re-verifying a real Cloud KMS heal live.
- **The load-bearing sub-deliverable is byte-exact canonical parity, NOT the ~30-line port.** Python's
  `json.dumps` defaults to `ensure_ascii=True`; the committed signed fixture's 3 em-dashes make the
  canonical 2244 bytes with `\uXXXX` escapes (verified). A naive JS canonicalizer emits raw UTF-8 (2223
  bytes) → **INTEGRITY FAIL on a valid heal**. The JS MUST reproduce Python's exact escaping (em-dashes,
  control chars, non-BMP surrogate pairs). This is why the estimate is 2–2.5d, not 1.5d.
- **Canonicalization hardening rides along (a real de-risk for the auditor too):** add a `bundle_version`
  field to `proof.build`'s bundle, pin it, and add a test asserting
  `proof.build's canonical == verify-proof's _canonical == the JS spec` for a fixture that DELIBERATELY
  contains non-ASCII + high-precision floats (extend the existing signed fixture — already 3 em-dashes +
  20 floats — as the golden case). This survives a full descope of the UI: it de-risks digest drift
  across agent/verifier/auditor/explorer regardless.
- *Honesty guarded by TEST, not just UI copy:* assert the page can NEVER surface "verified correct" —
  only the three provenance/integrity states. Adversarial test: a tampered bundle and a wrong-key case
  both show FAIL (no always-green theater).
- *Invariant:* NONE to the action tier — a client-side static page (no Python, no LLM). The one code
  change (`proof.py` `bundle_version`) is in the deterministic builder that already never imports the LLM.
- *FLOOR of the page* is paste-proof.json + paste-pubkey over the committed fixtures; the `/.well-known`
  auto-fetch (Phase 3) and the chain-walk (Phase 2) are STRICTLY additive tabs that no-op cleanly if
  those phases don't ship.

### Phase 5 — Remote-MCP governance tiers (the second pre-agreed headline) — ~4d nominal
Replace the single all-or-nothing bearer token with per-caller identity, route remote heals through the
1.1 lease, and make remote `set_autonomy`/`approve` respect the trust ramp — the "other agents drove
Airbag AND we can attest exactly who did what" story that pairs with the auditor. **Conditioned (per
V5_VISION §4) on REUSING 1.1's correlation lease + the autonomy ramp — cite the seam, don't rebuild it.**
- **NEW pure module `autosre/mcp_governance.py`** (NOT logic inside `mcp_remote.py` — which imports the
  LLM via `apply_approval` and can never be AST-guarded). A caller registry doc in `state_store`
  (`mcp_callers`: `{token_hash → {max_level, tools}}`) checked in a second deterministic layer AFTER
  `BearerGate`; the effective ceiling is `min(caller_cap, service_autonomy_level)`. A "partner" token
  reads incidents/proof but `set_autonomy('L3')` requires an operator-tier caller.
- Route `airbag_trigger_heal` through `state_store.claim_service_heal` at ENQUEUE time so the remote
  response itself carries `{status:'attached', leader_incident_id}` — a truthful ack independent of the
  `STORM_COALESCE` flag (closes the same self-amplification class for A2A callers that 1.1 closed for
  alerts).
- Leave `autonomy.record_outcome`'s automatic demotion FULLY intact: a remotely-granted L3 still
  auto-demotes on a bad heal and cannot re-grant past its cap (the concrete defense of the DO-NOT
  "never weaken the ramp" line).
- Append a durable append-only actor log (`mcp_actions`: `{caller_id, tool, args-summary, incident_id,
  ts}` via `incidents.record`'s de-dup capped-append pattern). Consider including `caller_id` in the
  signed bundle so the auditor attests provenance of the TRIGGER, not just the outcome.
- **Fail-CLOSED on the control plane** is the ONE deliberate fail-open exception (an authz denial
  rejects an over-privileged/unknown caller — a control-plane reject, NEVER an in-flight rollback).
- *Scaling honesty:* the remote MCP is pinned to `--max-instances 1` (deploy.sh:139-140, FastMCP
  session_manager is in-process). ACCEPT single-instance and document it — do NOT hand-roll
  cross-instance session state (scope creep).
- *Flag `AIRBAG_MCP_TIERS` (default OFF) → BearerGate stays the sole gate, byte-identical to the 7/8-tool
  contract.* Add `mcp_governance.py` to `_action_files()` in the SAME commit (with a test asserting it
  is scanned, mirroring `test_causal_and_signals_are_in_the_scanned_set`).
- *Proof:* a remotely-granted L3 caller auto-demotes to L2 on a bad heal and can't re-grant past its cap;
  `AIRBAG_MCP_TIERS=off` → the tool-set contract is byte-identical (BearerGate sole gate); a fail-CLOSED
  authz denial returns BEFORE any `queue.enqueue_heal` (an authz reject can never touch an in-flight
  rollback).

### Phase 6 — Leader-liveness heartbeat + fencing (live-finding a) — ~3d nominal
Shrink the crashed-leader dead-lease window from ~15 min to ~1 missed heartbeat, LLM-free, behind the
existing `AIRBAG_STORM_COALESCE` flag (no new separately-flippable flag — it must not desync from
coalesce).
- Add `refresh_service_heal(service, incident_id, short_ttl)` beside `claim/settle_service_heal` — a
  `transact()` that no-ops unless still-leader and re-aims `lease_until = now + AIRBAG_SERVICE_HEARTBEAT_S`
  (~60s). Call it from `_heal_body`'s `emit()` (fires at ~15 stage transitions — a free liveness pulse).
- **Keep `SERVICE_HEAL_LEASE_S=900` as the documented OUTER crash bound** so lowering the liveness TTL
  NEVER risks taking over a live-but-slow leader — the heartbeat is what makes a short takeover safe.
- Add a monotonic **generation/epoch token** on the `service_heals` doc so a slow old leader that wakes
  after takeover is FENCED: `settle_service_heal` already rejects a stale settle by incident-id mismatch
  (state_store.py:259); the generation extends the fence to ANY late write (prevents two leaders both
  shifting traffic).
- **Self-rescuing follower:** in `_attach_to_leader`, when the leader's lease is within ε of expiry OR
  its heartbeat is stale, do NOT `finish_heal` the follower — leave its per-incident lease reclaimable so
  Cloud Tasks at-least-once redelivery re-runs `claim` and self-promotes. Guard it with the same
  lease-live/heartbeat-fresh check so a redelivered follower NEVER re-drives onto a HEALTHY (settled-good)
  leader.
- Add `state_store.py` to `_action_files()` in the same PR (it genuinely imports no LLM — makes the
  invariant claim true rather than overstated).
- Introduce a `_now()` clock-injection seam into `state_store`'s lease functions (~0.5d) so the
  heartbeat/takeover/fencing tests are deterministic (today `test_state_store.py` uses real `time.sleep`
  — the "fake-clock" reuse claim is aspirational).
- **Measure it (not just assert it):** extend `bench/storm.py` with a crash-mid-heal variant (a leader
  that never settles + fake-clock) emitting `dead_leader_takeover_seconds` + `remediation_gap`, committed
  as golden JSON (flag-off ~900s vs flag-on ~1 heartbeat), CI-ratcheted exactly like the storm
  scorecard — turning live-finding (a) into the same anecdote→scorecard story v5 shipped.
- *Proof:* fake-clock leader emits K heartbeats then stops → follower takes over after TTL not 900s;
  live leader keeps the lease across a long run; zombie old leader's settle AND a late traffic-shift
  write both rejected by generation mismatch; follower redelivery after a GOOD settle → still no-ops;
  flag-off byte-identical.

### Phase 7 — Bounded burn sampling (live-finding b) — ~2d nominal
Kill the ~10-min slow heal WITHOUT dropping the burn detector, keeping the pooled-Wilson verdict math
UNCHANGED — only when-to-stop-sampling changes. Frame the headline as the **self-DoS safety class**
(Airbag bounding its own probe blast radius so it can't exhaust the target's connection capacity or its
own heal budget — on-thesis for "safe around ITSELF"); the speedup is the symptom it cures.
- Three composable, LLM-free levers on `sample_error_windows` (gcp.py:222): (1) **early-exit** — stop as
  soon as pooled errs ≥ `BURN_MIN_ERRORS` AND windows_with_errors ≥ `SIGNAL_DEBOUNCE_WINDOWS` (a FAIL is
  already certain); (2) **deadline** — `AIRBAG_BURN_MAX_S`, checked PER-REQUEST (so a final window of 50
  slow requests can't overshoot), returning what was sampled (INCONCLUSIVE if too few); (3) **bounded
  concurrency** — issue each window's GETs with a small pool, landing LAST behind its own flag
  (parallelism against the target is the riskiest lever).
- The stop-predicate lives in `signals/engine.py` and is PASSED INTO the sampler (callback/threshold
  struct) so `gcp.py` stays a dumb bounded sampler and the FAIL math is single-sourced (no drift, clean
  collect/verdict seam). The early-exit check runs BETWEEN windows in the backend loop (it cannot live in
  `_detect_burn`, which runs post-sampling).
- Preserve observer-safe marking (`config.PROBE_HEADERS` on every parallel client) and exact per-window
  `{errs,total}` accounting under parallelism.
- *Invariant:* NONE — `gcp.py` + `signals/engine.py` are already in `_action_files()`; the early-exit
  predicate is pure integer arithmetic (no analyzer/LLM). INCONCLUSIVE-on-deadline reads as no-trigger
  (matches the benign-on-error contract at gcp.py:230) so a bounded sample can never fabricate a FAIL.
- *Proof (FLIP-FREEDOM, not just request counts):* a would-be-FAIL is never prematurely PASSed
  (early-exit fires ONLY after both pooled-FAIL AND debounce are met by sampled data); a deadline hit
  returns INCONCLUSIVE never a partial FAIL; K>1 vs K=1 produce identical pooled counts on a
  deterministic fake; flag-off issues exactly 300 serial GETs byte-identically.

### Phase 8 — Bad-IMAGE fault fixture (live-finding c) — ~0.5d nominal (LOWEST rung)
Add ONE fault mode that deploys a genuinely different container image (not just a `FAULT_MODE`
env-value toggle on the same image) so `revision_delta`'s `image_changed` field is populated in a real
committed proof bundle instead of reading `false` forever. `revision_delta.py` is UNCHANGED — a fixture +
one live capture.
- Deploy the bad-image variant as a genuinely ROUTED bad revision Airbag identifies as `bad_revision`
  (state_machine.py:345 gates the attach on `decision.get("bad_revision")`; `diff()` returns all-empty
  when it's missing). Capture on the **5xx/code-bug** heal path (cleanest bad/healthy revision pairing),
  not latency; reuse the existing `gcloud run deploy --no-traffic` staging pattern.
- Make it a **regression fixture**, not a one-off: add a test asserting `revision_delta.diff()` over the
  two real specs yields `image_changed:true` with `image_bad != image_target` — so the field can never
  silently regress to always-false again.
- *Invariant:* NONE (no code change to `revision_delta.py`, which `_action_files()` already scans). Commit
  the resulting bundle beside `live-kms-signed-latency-heal.json`.

## 4. Non-negotiable FLOOR + descope ladder
**The FLOOR (non-negotiable):** the **Phase-1 Auditor Agent core** — a separate Cloud Run service that,
against a PINNED signer identity, re-verifies a live signed bundle end-to-end (integrity + provenance),
returns the honest tri-state, AND emits ONE counter-signed, offline-verifiable attestation, live-verified
on one real heal + committed to `docs/proof/`. This IS the deferred v5 headline and the whole point of
finals week; v5 4.2 was built as its foundation.

**Descope ladder (cut in order, floor is untouchable):**
1. **Phase 8** bad-image fixture (honesty patch on ONE field, not a marquee claim).
2. **Phase 7** bounded burn (the 900s TTL / opt-out posture already works; it's a perf + self-DoS
   hardening).
3. **Phase 6** heartbeat + fencing (the 900s crash backstop already works, just coarse).
4. **Phase 4** Proof Explorer (`verify-proof.py` CLI already lets a judge verify; the browser is a
   legibility multiplier) — BUT land the `bundle_version` + shared-canonicalization test regardless (it
   survives the UI's descope and de-risks the auditor).
5. **Phase 5** MCP governance tiers (the #2 headline, cut FIRST among the headlines if the auditor needs
   the days — it's new authz surface).
6. **Phase 3** served anchor + remote proof tool (the committed PEM + raw GET already work; this is A2A
   ergonomics).
7. **Phase 2** transparency log (cuts cleanly back to attest-single-bundle-only — still novel, still
   deterministic — WITHOUT touching the auditor core).
Do NOT descope the auditor's core verify + signer-pin + counter-sign — that is the floor.

## 5. What NOT to build (cut, with reasons — incl. refuted bets)
- **Auditor scorecard as a standalone bet (REFUTED).** A committed, CI-ratcheted 5-fixture attestation
  corpus sounds like the v5 storm-scorecard honesty move, but 4 of its 5 cases (valid/tampered/wrong-key/
  unsigned) are ALREADY green boolean asserts in `test_proof_sign.py`, so the delta is golden-file
  repackaging (~0.5d), not a headline; and its only novel case — rogue-signer/pinned-identity → FAIL —
  is NOT supportable by today's `verify()` (line 57 only echoes `sig.get("key")`), so it either smuggles
  in Phase 1's actual crypto contribution or is trivial. **Fold the pinned-identity primitive into the
  auditor (Phase 1.1); book any regression-ratchet effort against the auditor, not a separate bet.**
- **Cross-service blast-radius guard (REFUTED — tautology).** "Assert the rollback target resolves to the
  same service the incident is keyed on" defends a STRUCTURALLY IMPOSSIBLE path: the target is derived
  EXCLUSIVELY from `tools.list_cloud_run_revisions(service, config.GCP_REGION)`; there is no code path
  threading a foreign service into a mutation, and region is `config.GCP_REGION` at all 16 mutation/probe
  sites — so the assertion checks `GCP_REGION == GCP_REGION`. Its only reachable branch is the no-op pass;
  its error branch can only mis-fire and ESCALATE a legitimate rollback (violates never-block-a-legit-
  rollback). A cross-service surface only becomes REAL after multi-service governance (Phase 5) exists.
  The one defensible sliver is a `assert target in {r['name'] for r in revs}` one-liner before
  `rollback_traffic_to_revision` — file it as a hardening task, not a bet.
- **A second same-model LLM verifier / LLM-judge auditor** — explicitly CUT in v5 and on the DO-NOT list.
  The auditor is DETERMINISTIC crypto by construction (the lifted kernel imports zero agent code, only
  stdlib+`cryptography`). Any proposal that makes it an LLM re-judge is rejected on sight.
- **A full DID/VC / decentralized-identity framework** — the "two agents cross-signing with two distinct
  KMS identities" A2A story is real and shippable; the DID/VC frontier (arXiv research) is out of scope
  for a hackathon finals. Don't inflate the claim.
- **Hand-rolled cross-instance MCP session state** — accept `--max-instances 1` for the MCP control plane
  and document it (Phase 5). Cross-instance session affinity is scope creep.
- **Carried DO-NOT list (unchanged):** no GKE, no RL tuning, no blame engine, no fleet UI, no causal
  candidate-walk, no topology ledger, no pre-deploy admission gate, no canary on the INITIAL
  (stop-the-bleeding) rollback. **Storm-safety scope guards:** do NOT delete or weaken the L3→L1 trust
  ramp (governance tiers layer ON TOP of demotion, never remove it); NEVER mark `_burst` demo traffic as
  probe traffic (it simulates real users). **ChatOps stays blocked** on human-provided Slack credentials.

## 6. Cadence (unchanged from v5)
TDD per item; full suite + ruff (E9,F) green before every commit; bench ratchet green; the
architecture-invariant test run on every commit touching any action-tier module — **and the new
deterministic modules ADDED to the scanned set: `transparency.py` (Phase 2), `state_store.py` (Phase 6),
`mcp_governance.py` (Phase 5) into `autosre/_action_files()`, plus the auditor's OWN
`test_auditor_invariant.py` (Phase 1.3) since it lives outside the `autosre/` glob**; adversarial review
(agy Gemini 3.1 Pro and/or a multi-agent refute-by-default workflow) BEFORE each substantial commit;
live-verify on real Cloud Run; demo baseline left HEALTHY; commit + push incrementally; ONE test count
across all docs; google-adk stays 1.x. New effort-budget note: P1–P8 ≈ 18.5d nominal against ~15 real
finals days — the same over-provision ratio v5 shipped with; the descope ladder is pre-agreed so the
FLOOR (auditor core) is protected.

## 7. Corrected/confirmed facts (verified against code during drafting)
- `verify-proof.py:verify()` does two real checks but **NEVER pins the signer** — line 57 only echoes
  `sig.get("key")`. The pinned-identity check is genuinely NEW auditor work.
- The pinned signer identity source is real: every envelope embeds the full resource name; the committed
  live fixture reads `.../airbag-proof/cryptoKeyVersions/1`.
- `mcp_remote.py` registers **7 tools**, has NO `airbag_incident_proof`, and imports `apply_approval`
  from `state_machine` (LLM-tainted) → it can never be in `_action_files()`; governance goes in a
  separate pure module.
- Proofs live in MUTABLE per-incident Firestore docs; `grep append-only|merkle|hash.chain|prev_digest|
  transparency` returns nothing. No served pubkey route (`well-known|pubkey|jwks` = 0 hits in app.py).
- `claim_service_heal` writes `lease_until = now+900` ONLY at claim/settle, never during a running heal;
  `_service_heal_live` is purely `lease_until > now` → the ~15-min crashed-leader window is real.
- `sample_error_windows` is 6×50=300 serial blocking GETs, no break/deadline/concurrency; the pooled
  verdict is computed only after all sampling.
- The canonical bundle uses `ensure_ascii=True`; the committed signed fixture is 2244 bytes with `\uXXXX`
  escapes → a naive JS canonicalizer diverges (~2223 bytes) and FAILs a valid heal. `bundle_version` +
  byte-parity test is load-bearing before a third consumer.
- `image_changed` reads `false` in the only committed signed bundle (env-toggle faults on one image).
- `infra/kms-setup.sh` is parameterized (`AIRBAG_KMS_KEYNAME`, `AGENT_SA`) so the auditor's second key
  is a config clone; `sandbox-job/` is the committed new-Cloud-Run-service precedent.

## 8. Open questions for Jason (defaults chosen; override at kickoff)
1. **Auditor key rotation posture** — fetch the pubkey from KMS `getPublicKey` at boot (live anchor) or
   pin the committed PEM (offline anchor)? **Default: pin the committed PEM + document a rotation path
   (the registry).** The auditor's own key is a second, independent identity — never reuse `airbag-proof`.
2. **Auditor as a formal A2A agent-card handshake vs a plain polling+verify+cross-sign service** — the
   agent-card wrapper is nice-to-have. **Default: ship the plain cross-signing service for the FLOOR;
   add the agent-card only if ahead** (it's ladder-adjacent to Phase 5).
3. **Include `caller_id` in the signed bundle** (Phase 5) so the auditor attests trigger provenance —
   **default: yes if Phase 5 lands, keyed on presence so a no-caller bundle stays byte-identical.**
4. **Prod flag posture after live verify** — deploy the auditor + flip `AIRBAG_TRANSPARENCY_LOG`/
   `AIRBAG_MCP_TIERS`/`AIRBAG_SERVICE_HEARTBEAT_S` ON. **Default: ON after live verify; the recorded demo
   is captured with the auditor's money-shot but the agent side stays byte-identical.**
5. **Re-run the market recon at finals kickoff** — Azure SRE / Datadog Bits both shipped autonomy in the
   last ~6 months; the "un-owned gap" framing must be re-confirmed. **Default: 30-min WebSearch sweep on
   day 1 before recording the deck.**
6. **Update SUBMISSION.md §2 competitive table** with the GA facts and pivot the moat to
   provenance + self-safety BEFORE the finals video. **Default: yes, day 1.**
