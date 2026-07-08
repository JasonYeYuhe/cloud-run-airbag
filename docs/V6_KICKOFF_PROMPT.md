# v6 kickoff prompt — paste the fenced block below into a fresh dev session

Plan of record: `docs/V6_VISION.md` (grounded planning workflow → primary-source market/prior-art
sweep folded in (§1b) → THREE adversarial review rounds, ALL folded (§0): Round 1 pre-drafting
refutation; Round 2 = a 40-agent refute-by-default workflow (35 raised → 32 confirmed); Round 3 =
an independent Codex gpt-5.5 pass (CHANGES-NEEDED → 13 findings folded). agy/Gemini was down on
2026-07-08/09, so the review ran on the cadence's documented substitutes → GO). This prompt is the
self-contained handoff.

```
You are continuing development of "Airbag" — an autonomous release safety net for Google Cloud Run,
built for the DevOps × AI Agent Hackathon 2026 (required stack: Gemini + ADK + Cloud Run). v2–v5 are
COMPLETE, LIVE, and live-verified on real Cloud Run. Your job is v6 — the FINALS build (finals
2026-08-19; nominal dev window 2026-07-30 → 08-19 with a HARD code freeze 2026-08-16, but start
immediately if Jason says go). The July-10 submission (video/deck) is Jason's own deliverable, NOT
yours — your only obligation to it is DO NOT DESTABILIZE the live demo (see GUARDRAILS).

ORIENT FIRST (read before doing anything):
- Repo: /Users/jason/Documents/AI Agent Hackathon/cloud-run-airbag (branch main; working tree clean).
- Read docs/V6_VISION.md END TO END — the plan of record. §0 holds THREE folded review rounds
  (Round 2 confirmed 32 findings; Round 3 (Codex) added 13 — every phase spec below already
  incorporates all of them; do NOT re-litigate). §1b is a primary-source market/prior-art sweep
  (2026-07-04→08) with
  per-claim [V]/[lead] flags, a borrow table, pre-armed counter-lines (§1b.4), and an honesty
  ledger (§1b.5). docs/V5_VISION.md is the prior stage; docs/AIRBAG_BENCH.md is the measuring stick.
- Your auto-memory has the full v2–v6-planning history (airbag-v5-progress.md) — trust it but
  VERIFY every file/line ref against current code (anchor on function NAMES, not line numbers).
- PYTHON ENV: the repo-root venv — run tests as `cd agent && ../.venv-demo/bin/python -m pytest -q`.
  Anaconda is NOT the project env; never pip-install into it.

THE INVARIANT (load-bearing, do NOT break): Gemini DIAGNOSES, a deterministic FSM ACTS. The action
tier (backends/*, signals/*, tools.py, causal.py, memory.py, reversibility.py, revision_delta.py)
NEVER imports the LLM; agent/tests/test_architecture_invariant.py (AST-based) enforces it via
_action_files(). v6 additions to that scanned set, each in the SAME commit that creates/touches the
module: proof.py (first Phase-1 borrow that touches it), transparency.py (P2), mcp_governance.py
(P5), state_store.py (P6). The auditor/ service lives OUTSIDE that glob: auditor/verify.py gets an
import ALLOWLIST test (stdlib + cryptography ONLY), the rest of auditor/ a denylist mirror, plus a
repo-level parity test asserting the auditor's forbidden set ⊇ the agent's (V6_VISION Phase 1.3).

THE v6 THESIS (docs/V6_VISION.md §1, Round-3-scoped — pitch it EXACTLY this way): "provable
autonomy — a Cloud Run remediation agent that is safe around ITSELF and whose heals are
INDEPENDENTLY VERIFIED AND COUNTER-SIGNED by an LLM-free auditor." Two precision rules: scope the
claim to what SHIPS (completeness/no-suppression claims arrive only with Phase 2's log), and
de-absolutize ("we found no shipped..." — never "the only").
Verified market context (§1b, primary sources): the base self-heal loop is table stakes (Azure SRE
Agent GA, Datadog Bits Remediation Preview, a Google Cloud Run hackathon writeup from Nov 2025
already published our loop) — NEVER lead with the loop. The two surviving gaps, PRECISELY phrased
(§1b.2 — the exact wordings survived adversarial review; do not paraphrase them looser):
(1) remediation-LEVEL coalescing + observer-safe probes ("the agent doesn't dogpile, and can't
false-alarm itself") — alert-level grouping is commodity, never claim it; (2) "no shipped
remediation agent PROVES its own actions to an independent verifier" — signed SELF-audit logs exist
(DeepInspect, Trinitite — the latter third-party-anchored; characterize carefully per §1b.2),
identity-signing exists (sigstore-a2a); independent attestation of agent WORK does not. SCITT
vocabulary (RFC 9943) is conditional: full Receipt/Transparency-Service terms ONLY if Phase 2
ships; otherwise "SCITT-inspired counter-signed attestation" (§4 rung 7).

BUILD ORDER (details + verified file:symbol seams in V6_VISION §3 — follow it; every agent-side
item flag-gated default-OFF; the auditor is default-not-deployed; NO borrow merges before the
Phase-1 FLOOR is live-verified):
  Phase 1 — THE MARQUEE: Auditor Agent (~4.5d). A SECOND Cloud Run service (auditor/, its OWN
    least-priv SA + its OWN KMS key via infra/auditor-kms-setup.sh — NEVER reuse airbag-proof;
    optionally a second GCP project for maximal independence, else pre-arm the §1b.4 line) that
    polls the agent's public GET /incidents/{id}/proof, lifts scripts/verify-proof.py:verify()
    VERBATIM as its kernel, and adds the genuinely-new crypto: a PINNED expected-signer check
    (verify() today only ECHOES sig.get("key") — a valid signature from an unexpected
    cryptoKeyVersion must FAIL attestation). DIRECTION OF TRUST (Round-3 #4): verify against the
    CONFIGURED key/PEM — never a key resolved FROM the envelope's signature.key (unsigned
    metadata); report the configured identity as the verified signer. Honest tri-state
    SIGNED-VERIFIED / INTEGRITY-ONLY / FAIL, plus Round-2 #8: a post-cutover unsigned bundle
    (registry not_before says a signature was EXPECTED) surfaces as DEGRADED — visibly distinct
    from the legitimate pre-4.2 INTEGRITY-ONLY. Counter-sign the attestation with the auditor's
    key; commit scripts/auditor-pubkey.pem; the attestation BINDS THE FETCH CONTEXT (Round-3 #1:
    raw fetched-bytes digest, agent URL, requested incident id, HTTP status, and a
    bundle.incident_id == requested-id check → FAIL on mismatch); READ-ONLY out-of-band —
    structurally cannot block a heal. INTERNAL GATES (Round-3 #10): day-3 = offline verifier+pin
    green; day-5 = counter-signed attestation live; both feed the day-8 checkpoint. TDD proof set:
    valid→SIGNED-VERIFIED; tamper INSIDE bundle→FAIL; wrong keypair→FAIL; valid signature + WRONG
    cryptoKeyVersions/2→FAIL (the NEW case); pre-4.2→INTEGRITY-ONLY; post-cutover-unsigned→
    DEGRADED; incident-id mismatch→FAIL. Money shot (~1d, Round-2 #29 mechanics):
    scripts/demo-tamper.sh (rewrites ONE byte of the stored bundle via Firestore + restores), a
    committed rogue-key-signed fixture, a pinned INTEGRITY-ONLY incident id, a minimal auditor
    status surface, 5–10s poll cadence — demo ALL states on camera. AFTER the floor is
    live-verified: the standards borrows land as separate commits — DSSE + in-toto as ONE merged
    deliverable behind AIRBAG_PROOF_DSSE, HARD-GATED (Round-3 #9) on a golden fixture signed via
    the SAME KMS path passing cosign verify-blob-attestation IN CI (no CI pass → cut DSSE, it's a
    borrow not a phase) (default OFF; the DSSE payload IS an in-toto Statement:
    subject = {name: incident_id, digest.sha256 = sha256(canonical bundle bytes)}, predicateType
    airbag.dev/heal-attestation/v1, predicate = the bundle; a SECOND KMS sign over sha256(PAE);
    verify on camera with cosign verify-blob-attestation --key ... --type ... — the audit-verdict
    Statement's subject is the sha256 of the heal ENVELOPE); bundle_version lands in 1.2 BEFORE
    trigger_evidence_digest + the SLSA externalParameters(Gemini-suggested)/internalParameters
    (FSM-resolved) split + spiffe://airbag.dev/{agent,auditor} issuer URIs; §0 Round-1 #6 (bound
    BOTH creds.refresh and the KMS httpx call in sign paths) is owned by Phase 1.2. Every signed
    payload carries an in-band type tag (attestation_version / bundle_version) — domain separation
    so an attestation can never replay as a heal proof (Round-2 #6).
  Phase 2 — hash-chained transparency log (~2.5d, STRETCH #1, flag AIRBAG_TRANSPARENCY_LOG): FIRST
    deliverable is a transact_multi primitive in state_store (the existing transact() is strictly
    single-doc — writing log_head + log_entries/{seq} atomically is otherwise impossible; a crash
    between two writes would forge a "tamper" gap; +0.25d, crash-between-writes test). append()
    from the already fail-open _persist_proof; idempotency key is (incident_id, terminal_status) —
    NOT incident_id alone (MITIGATED + CLOSED are two links; auditor accepts two NON-adjacent seqs
    per incident). Auditor walks the chain, checks append-only consistency, counter-signs the HEAD
    as a CHECKPOINT — and checkpoints CHAIN (each embeds the prior (seq, entry_hash); containment
    check detects whole-chain rewrite/truncation between audits, +0.25d; the auditor's last-attested
    checkpoint state lives in AUDITOR-OWNED durable storage — never agent-owned Firestore — and a
    missing prior checkpoint for a known service FAILs attestation, Round-3 #5). Coverage cross-check:
    terminal incidents absent from the chain surface as unlogged:[ids] in the attestation (the
    chain proves "no APPENDED entry deleted/reordered/back-dated" — scope the claim exactly so).
    Domain-separation tags on every hash (airbag.log.entry.v1, airbag.checkpoint.v1).
  Phase 3 — served trust anchor + remote proof tool (~1d + 0.5d TUF borrow): GET
    /.well-known/airbag-proof-pubkey.pem (serves the COMMITTED PEM — the committed file stays the
    trust root) + registry.json as a mini TUF-style root (version/expires/threshold; new version
    signed by previous key) where EVERY entry carries a role field (signer=heal-proof,
    auditor=attestation) and every verify surface MUST match role against artifact type (Round-2
    #6); port airbag_incident_proof to mcp_remote.py (7→8 tools — flip the locked count test
    DELIBERATELY).
  Phase 4 — offline Proof Explorer (~2–2.5d, STRETCH #2): static HTML/JS WebCrypto page. Round-2 #5
    redesign: verify LITERAL bytes, never re-implement the canonicalizer — primary input is the
    DSSE envelope (verify base64 payload bytes directly); legacy bundles get a number-token-
    PRESERVING parser (keep raw lexemes, sort keys, re-emit — Python float repr makes parse→
    stringify impossible); WebCrypto needs the ~20-line DER→P1363 ECDSA signature converter.
    bundle_version + the byte-parity test (proof.build == verify-proof == JS spec; the committed
    fixture's canonical is 2244 bytes ensure_ascii vs 2235 naive-UTF-8) land REGARDLESS of the UI.
  Phase 5 — remote-MCP governance tiers (~4d, 2nd headline, flag AIRBAG_MCP_TIERS): go/no-go at the
    day-8 checkpoint (start ONLY if P1 is live-verified AND ≥5 dev days remain before the freeze).
    NEW pure module autosre/mcp_governance.py (mcp_remote.py imports apply_approval → LLM-tainted →
    can never be AST-scanned). FIRST deliverable (Round-3 #7): a token→caller resolution seam —
    today BearerGate validates one static token and tool functions receive NO caller identity;
    test a denial happens BEFORE queue.enqueue_heal. Internal sub-floor: caller registry
    (token_hash → {max_level, tools, max_heals_per_hour, max_consecutive_autonomous}) + effective
    ceiling = min(caller_cap, service_autonomy_level) + lease-routed airbag_trigger_heal + 403
    insufficient_scope step-up; discovery-time tools/list hiding is the STRETCH on top, not the
    floor. Settlement must be flag-independent (Round-2 #19: settle_service_heal whenever the
    service_heals doc names this incident as leader, regardless of config.STORM_COALESCE — else an
    enqueue-time claim leaves a 15-min corpse lease when coalesce is off). Trust-ramp demotion
    stays FULLY intact; a DURABLE ACTOR AUDIT TRAIL (honest name — incidents.record is a
    merge-update, not append-only; route through the P2 log if it ships, Round-3 #8); fail-CLOSED
    on the control plane only. DEPLOY GOTCHA (Round-3 #13): deploy.sh's comment demands
    --max-instances 1 when MCP is on but the command pins 3 — gate deploy flags on MCP mode +
    smoke-test the deployed value. Framing: AWS MCP Gateway ships tool ALLOWLISTING — ours is
    graded AUTONOMY with quantified budgets.
  Phase 6 — leader-liveness heartbeat + fencing (~3d, behind AIRBAG_STORM_COALESCE; PREREQUISITE:
    Phase 7): liveness is a SEPARATE heartbeat_at field — lease_until stays written ONLY at
    claim/settle (Round-2 #18 BLOCKER: re-aiming the lease on emit would let a live-but-slow leader
    be usurped mid-sampling — sample_latency_windows alone can gap ~320s). Takeover requires
    (lease_until expired) OR (heartbeat_at stale by K misses, K×TTL COMPUTED > worst-case inter-emit
    gap from SIGNAL_WINDOWS/BURN settings). Pulse from _heal_body's emit() AND per-window inside the
    samplers. Monotonic generation token fences zombie leaders — and the fence must gate the SIDE
    EFFECT, not just the settle (Round-3 #6): assert_current_service_heal(service, incident_id,
    generation) IMMEDIATELY before every rollback_traffic_to_revision/set_traffic_split call in the
    heal path + after long sampling windows. Self-rescuing follower (don't finish_heal a follower
    whose leader's heartbeat is stale). _now() clock-injection seam in
    state_store for deterministic tests. Extend bench/storm.py with a crash-mid-heal scenario
    emitting dead_leader_takeover_seconds (flag-off ~900s vs flag-on ~K misses), CI-ratcheted.
  Phase 7 — bounded burn + latency sampling (~2d): early-exit once pooled FAIL is certain (predicate
    in signals/engine.py, PASSED INTO the sampler), AIRBAG_BURN_MAX_S per-request deadline
    (INCONCLUSIVE on timeout — never a partial FAIL), bounded concurrency LAST behind its own flag;
    covers the latency sampler's gaps too (it feeds P6's takeover math). Verdict math UNCHANGED.
    Frame as self-DoS safety.
  Phase 8 — bad-IMAGE fault fixture (~0.5d, LOWEST rung): one fault revision from a genuinely
    different image so revision_delta's image_changed:true appears in a real committed signed
    bundle; capture on the 5xx path; regression test.
  Phase 9 — DELIVERY (PROTECTED, 2–3d, rung 0 — never traded for dev): code freeze 2026-08-16;
    rubric-mapped deck (つくる/まわす/とどける verbs on screen; 15s outage hook); ONE headline metric
    ("alert → independently-attested recovery: Xs" on the auditor card); pre-recorded backup take of
    the full money shot; ≥2 timed rehearsals; venue-network fallback decision; JP vs JP+EN deck
    decided day 1 with the five load-bearing terms pre-translated.

THE FLOOR (non-negotiable if time binds): Phase 1's auditor core — separate service, pinned-signer
verify, tri-state(+DEGRADED), ONE counter-signed offline-verifiable attestation binding the fetch
context, live-verified on one real heal + committed to docs/proof/. Descope ladder (V6_VISION §4,
cut in order; rung 0 = Phase-9 delivery days are NEVER cut): P8 → P6 → P7 → P5 → P4-UI (parity
test lands regardless; Round-3 #11 swapped P5 before P4 — the Explorer supports judge
self-verification of the marquee, P5 is new authz surface) → P3 → P2 (SCITT vocabulary degrades
with P2's rung). Gates: P1 day-3/day-5 internal; day-8 checkpoint — if P1 isn't live-verified by
then, enter the ladder immediately. Drop borrows before phases.

CORRECTED FACTS (verified against code 2026-07-04→09 — do not re-derive wrongly):
- verify-proof.py:verify() checks integrity + signature but only ECHOES sig.get("key") — no pin.
- mcp_remote.py registers exactly 7 tools, lacks airbag_incident_proof, imports apply_approval.
- state_store.transact() is strictly SINGLE-document in both backends (hence transact_multi).
- claim_service_heal writes lease_until ONLY at claim/settle — never refreshed mid-heal (the
  observed ~15-min dead-leader window); _service_heal_live is purely lease_until > now.
- proof.py canonical JSON is ensure_ascii=True (committed fixture: 2244 bytes; naive UTF-8: 2235).
- No /.well-known|pubkey|jwks route exists in app.py; no append-only/hash-chain anywhere in repo.
- incidents.record is a MERGE-update (capped-append for events) on one mutable doc per incident.
- _persist_proof fires at MITIGATED and again at CLOSED (two snapshots per closed incident).
- infra/kms-setup.sh is parameterized (AIRBAG_KMS_KEYNAME, AGENT_SA) but 1.2 must ALSO
  parameterize the PEM output path + create the auditor SA first (two clone deltas, Round-2 #26).
- sandbox-job/ is the committed new-Cloud-Run-service precedent.

CURRENT LIVE STATE (after the 2026-07-04 live verification): agent rev 00041 (project
airbag-hack-260628, asia-northeast1) with AIRBAG_STORM_COALESCE=1, AIRBAG_SELF_TRAFFIC_EXCLUDE=1,
AIRBAG_PROOF_SIGN=1 + AIRBAG_KMS_KEY (keyring airbag, key airbag-proof, EC_SIGN_P256_SHA256),
AIRBAG_REVISION_DELTA=1, AIRBAG_SIGNALS=5xx,latency (burn deliberately OFF live — the 10-min
slow-heal), AIRBAG_CAUSAL_CHECK=1; state=firestore, events=pubsub, max-instances 3. deploy.sh
reproduces this (env vars use a ^@^ delimiter). Demo baseline: airbag-target-00024 (healthy, 100%,
NEWEST) + 00023 (slow, 0%) + 00022 (bug, 0%) — ALWAYS leave it that way. Deploy agent-only:
PROJECT=airbag-hack-260628 AGENT_ONLY=1 ./deploy.sh. 347 tests (339 agent + 8 mcp-server), CI
green — auditor tests will ADD a suite (pytest agent/tests AND auditor/tests; keep ONE total count
across docs, SUBMISSION.md ~line 88 canonical). Live signed proof:
docs/proof/live-kms-signed-latency-heal.json (verify: python scripts/verify-proof.py <file>).
gcloud + gh authed. agy (Gemini CLI) was DOWN on 2026-07-08/09 (hangs, 0 bytes — killed; skipped
per Jason) — use a multi-agent Workflow review and/or codex exec (pipe the prompt via STDIN, it
hangs on long argv) as the adversarial reviewers. Secrets in Secret Manager + agent/.env
(gitignored) — NEVER print or commit secrets. Open fix-PRs #8/#9 are Jason's to close — leave them.

GUARDRAILS (until Jason confirms the submission video is recorded): do NOT redeploy airbag-agent,
do NOT change its env flags, do NOT touch the target baseline, do NOT cut over alert-policy v2.
The auditor is a SEPARATE service — building + deploying IT is safe at any time (it only READS
public endpoints); agent-side changes (P2/P3/P5/P6/P7 flags, DSSE emit) are DEV+TEST-only until the
video is confirmed, then live-verify + flip per the cadence. AFTER the video: §8 Q7's day-1
checklist applies (alert-policy v2 cutover + live probe-burst verify, /demo/run + /demo/run-latency
smokes, token/key expiry audit, baseline invariant check) and §8 Q8's billing guardrail (budget
alert; auditor scale-to-zero between demo windows; record credit state in ~/Documents/credits.md).

HOW TO WORK (the established cadence, unchanged): TDD each item (tests alongside; full suite + ruff
E9,F before every commit); bench ratchet + architecture-invariant test on every commit touching
state_machine/state_store/backends/signals/memory/causal/tools/proof + the new modules; adversarial
review (multi-agent refute-by-default Workflow; agy if it recovers) BEFORE each substantial commit
and APPLY confirmed findings; live-verify on real Cloud Run where it matters; commit+push to main
incrementally; google-adk stays 1.x. NON-REDUNDANCY: never build a check a deterministic gate
already enforces. Respect V6_VISION §5's cut list (no LLM-judge auditor, no DID/VC framework, no
cross-instance MCP sessions, no full 4-role TUF; the carried v5 DO-NOT list stands).

The §8 open questions are answered at their defaults (pin the committed PEM as trust root; plain
polling auditor for the FLOOR; caller_id keyed-on-presence if P5 lands; flags ON after live verify;
30-min market DELTA sweep day 1 on the §1b.4 watch items; SUBMISSION.md competitive table updated
from §1b before the finals deck; Q7 day-1 checklist; Q8 billing guardrail; Q9 headline metric + JP
terms day 1). Only ask Jason on a NEW decision the code/docs can't resolve.

Start by reading docs/V6_VISION.md end to end, then verify the Phase-1 seams against current code
(verify-proof.py, proof.py, app.py /incidents/{id}/proof, infra/kms-setup.sh, state_store.transact),
then begin Phase 1.1 (the auditor verify core + signer pin) TDD-first.
```
