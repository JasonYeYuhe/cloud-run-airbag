# Airbag v5 — Vision & Development Plan

> Plan of record for the next stage. Produced by a 25-agent grounded planning workflow
> (4 code/market fact-finders → 6 first-principles ideation lenses → dedup-merge → per-bet
> adversarial refute-by-default verification; 14 bets survived, 0 refuted, 9 merged away) —
> then reviewed by Gemini 3.1 Pro (§0). Window: **~7 days to submission (2026-07-10)**, of which
> realistically **~4–5 dev days** (the human needs video/deck time). Finals: 2026-08-19.

## §0. Gemini 3.1 Pro review (incorporated)
First pass verdict: **CHANGES-NEEDED** — 6 findings (3 BLOCKER), every one folded in below:
1. *(BLOCKER)* Correlation-lease lifecycle: a fixed hold window just DELAYS the pileup (autoClose
   re-fires outlive any window), and followers acking their queue tasks then losing a crashed
   leader drops alerts. → 1.1 rewritten: the lease holds while the outcome is UNSETTLED (not a
   timer; TTL only as backstop), re-fires beyond it coalesce onto the 1.3 approval card
   (dependency now explicit), and a follower that finds a TERMINALLY-failed leader claims a fresh
   lease (becomes leader) instead of attaching to a corpse.
2. *(MAJOR)* Observer-exclusion is honestly scoped: UA filtering covers the DETECTION/COUNT paths
   (query_error_rate log scan + the new log-based alert metric — `httpRequest.userAgent` is in
   Cloud Run request logs). It CANNOT cover app-emitted tracebacks (`fetch_error_logs`) or the
   built-in console metric — stated plainly; RCA impact is benign (a probe-triggered traceback is
   byte-identical to a user-triggered one), and no decision keys on trace COUNTS.
3. *(BLOCKER)* Approval coalescing must not merge different incident CLASSES aimed at the same
   target (a 5xx card carries a fix-PR consequence; a latency card must not). → signature is now
   `sha256(service|kind|target|primary_signal)`.
4. *(MAJOR)* A sequential-only storm scorecard can't claim to measure a concurrent storm. →
   framing fixed: the scorecard measures OUTCOME SHAPE on a deterministic scripted replay; the
   CONCURRENT transactional safety is proven separately by threaded lease-contention tests
   (emulator + memory), the codebase's existing pattern — both are Phase-1.1/2 exit criteria.
5. *(MAJOR)* No-blind-landing as an ESCALATE contradicts the locked v3 fail-open posture ("never
   block a legit rollback" — a network blip must not abandon users mid-outage). → downgraded:
   probe-error + unwitnessed target ⇒ ONE bounded probe retry, then PROCEED fail-open with a
   first-class `blind_landing` marker on the record/events — measured (storm scorecard counts
   blind landings) and surfaced, never blocking.
6. *(BLOCKER)* A hardcoded `target-app/` allowlist breaks the generic product. → the allowlist is
   CONFIGURABLE (`AIRBAG_FIX_ALLOWLIST`, default derived from the existing `AIRBAG_FIX_FILE`
   config = `target-app/` in this deployment), still HARD-enforced (normalized, no `..`, and
   `.github/` always rejected). Chose configurable-allowlist over Gemini's blocklist suggestion —
   blocklists under-enumerate; the reviewer's real objection (hardcoding) is fixed.
7. *(MINOR)* The FLOOR now includes the 4.1 path-allowlist (it mitigates an active
   prompt-injection→workflow-write vulnerability; security is not descopeable).

## 1. Executive summary
The arc: v2 made the agent durable and governed; v3 made **detection** trustworthy; v4 made the
one reversible **action** provably correct. v5 closes the last trust gap, straight from first-hand
evidence: **the agent itself must be safe in the loop with production reality.**

On 2026-07-02, during v4's live verification, Airbag hit a real storm — **caused and amplified by
itself**: (1) the causal probe's 8 requests against a broken 0%-traffic target produced 8 REAL 5xx
that **fired the very Cloud Monitoring alert it was diagnosing**; (2) each alert delivery carries
its own incident id and every dedup keys on that id, so N deliveries for ONE broken service
spawned **N independent full heal runs**; (3) one heal failed verify → the designed trust-ramp
demoted the service L3→L1 → **every subsequent storm heal filed its own approval card**, which
piled up and expired silently while traffic sat on a bad revision; (4) a human untangled it
manually (deny ×3, re-promote, reset). Every step was *designed behavior* composing into an
undesigned outcome.

The July-2026 market sweep says this failure class is **unnamed in the entire field**: Datadog
Bits Remediation (Preview) executes pre-authored scripts behind team guardrails; Azure SRE Agent
(GA) is approval-gated allowlisted runbooks (while *marketing* "alert to verified recovery");
Gemini Cloud Assist is still officially advisory (Premium-gated since 2026-04-10); Komodor/
PagerDuty/Resolve execute human-approved plans. **Nobody addresses the agent's own observer
effect, alert-storm fanout, or self-demotion deadlock.** v5's thesis: **storm-safe autonomy — the
first remediation agent that is provably safe around ITSELF** — with the 2026-07-02 storm turned
into a committed, pre-registered, ratcheted benchmark scenario.

## 2. The gaps v5 closes (grounded in code + the live storm)
- **(a) No service-level incident correlation.** Every dedup keys on the Monitoring incident id:
  `seen_and_mark` (state_store.py:138), `claim_heal` (state_store.py:151). The only per-service
  lease in the codebase guards `complete_rollback` (pending.try_begin_complete, pending.py:39).
  Per-revision alert time series, autoClose re-fires (~30min), and the dedup-free Sentry path each
  mint fresh ids → N-for-1 heal fanout. → **Phase 1.1**
- **(b) Observer effect: Airbag's own traffic is indistinguishable from users'.** Five diagnostic
  httpx clients in backends/gcp.py (lines 109, 164, 211, 258, 339) carry no marker; the alert
  policy filters the built-in `request_count` 5xx metric (infra/alert-setup.sh:37) which cannot
  see headers; `query_error_rate`'s log scan counts probe 5xx too — so heal A's probes fire alerts
  and poison heal B's triage. (`_burst` in app.py is DEMO user-traffic and must stay unmarked.)
  → **Phase 1.2**
- **(c) Approval pileup + demotion ergonomics.** `save_approval` keys strictly on incident_id
  (autonomy.py:73-76) — no coalescing by (service, kind, target); approvals die by silent lazy TTL
  with no sweep at terminal seams; a deny settles only its own card; later L1 failures erase the
  `demoted_from` breadcrumb. One outage ⇒ a growing unattended queue. → **Phase 1.3**
- **(d) The storm is unmeasurable today.** The bench replays exactly one heal per fixture
  (harness.py); nothing can score heals-per-outage, approval cards, or self-traffic. Per the
  honesty invariant, "storm-safe" stays an anecdote until it's a committed scorecard. → **Phase 2**
- **(e) Blind landings + stale witnesses (v4 residuals).** A causal-probe ERROR reads INCONCLUSIVE
  and proceeds (causal.py — deliberate fail-open) even onto a target with ZERO positive evidence —
  exactly the storm's step that landed traffic on the bug revision. And `last_witnessed_at` is
  stored (memory.py:88) but consulted only for LRU eviction — a witness from arbitrarily long ago
  counts forever. → **Phase 3.1**
- **(f) The strongest evidence teaches nothing.** `complete_rollback`'s CLOSED branch (a fix that
  survived direct-probed canary 10/50/100) neither witnesses the fix revision in the ledger nor
  credits the trust ramp — while its canary-failure path DOES demote. Trust asymmetry. → **Phase 3.2**
- **(g) Fix-path write surface (security).** github_pr.py commits **LLM-chosen file paths**
  unvalidated (:62-64/:82-88) — a prompt-injected stack trace could write `.github/workflows/*`,
  which EXECUTES with repo secrets on push to the very `airbag/fix**` branch being written; PR
  reuse matches ANY open `airbag/fix*` PR (:47) regardless of incident. → **Phase 4.1 (hard gate)**
- **(h) Provenance gap (v4's named "top v5 candidate").** The proof bundle's sha256 proves
  integrity, not authorship (proof.py says so honestly); Azure markets "verified recovery" with
  zero cryptographic backing. → **Phase 4.2**

## 3. Phased plan (each item: LLM-free in the action tier, flag-gated default-OFF unless noted,
TDD'd, adversarially reviewed before commit, demo left HEALTHY)

### Phase 1 — Storm-safe core (the marquee) — ~4d nominal
1.1 **Service-level correlation lease** *(1.5d)* — `claim_service_heal(service)` in
    state_store.py beside `claim_heal`, same Firestore transactional pattern (mirror
    pending.try_begin_complete — the codebase's proven per-service lease). First alert = leader
    (runs the heal); followers ATTACH their incident_id to the leader's incident (transactional
    append + an `ATTACHED` event) and ack. Lifecycle (Gemini-review BLOCKER, spec'd up front):
    the lease is NOT a fixed timer — it holds while the correlated outcome is **unsettled**
    (running, or escalated/awaiting with a live approval/pending state) with a generous TTL purely
    as a crash backstop; release on `mitigated`/`noop` settle. A re-fire arriving BEYOND the lease
    coalesces onto the still-open 1.3 approval card (explicit dependency — the card, not the
    lease, is what stops late-pileup). A follower that finds the leader TERMINALLY failed
    (exhausted/manual_intervention) claims a FRESH lease and becomes the new leader — never
    attaches to a corpse. `finish` stamps `last_outcome` for late deliveries.
    Flag `AIRBAG_STORM_COALESCE` (default OFF).
    *Proof:* emulator + memory tests (N CONCURRENT claims → exactly 1 leader, N-1 attached, no
    lost ids — threaded, the existing lease-contention pattern; TTL backstop; hold-while-
    unsettled; dead-leader takeover) + a live N-alerts→1-heal verify.
1.2 **Observer-safe diagnostics** *(1.5d)* — every diagnostic client in backends/gcp.py stamps
    `User-Agent: airbag-probe/1` + `X-Airbag-Probe: 1`; `query_error_rate`'s log filter excludes
    the probe UA when `AIRBAG_SELF_TRAFFIC_EXCLUDE` is on (default OFF); `_burst` stays unmarked
    (it simulates USERS — comment + test pin this). A guard test (same spirit as the no-LLM AST
    invariant) asserts every diagnostic client in backends/ carries the marker. Alert policy:
    ship `infra/alert-setup-v2.sh` **additive** (a log-based 5xx metric excluding the probe UA +
    a policy on it); cut over live only after verification — never before the demo video.
    *Proof:* guard test + filter-construction tests + live: probe the KeyError revision with the
    flag on → alert metric unmoved; before/after captured in the storm scorecard.
1.3 **Approval coalescing + storm settlement** *(1d)* — approvals keyed by
    `sha256(service|kind|proposed_target|primary_signal)` (the signal term is the Gemini-review
    BLOCKER fix: a 5xx card carries a fix-PR consequence, a latency card must not — different
    incident classes never merge even when they propose the same target): a second gated heal
    transactionally attaches its
    incident_id + bumps a count on the SAME card ("×7"); one approve/deny settles ALL attached
    incidents (each gets its terminal event + record); heal-CLOSED/mitigated sweeps now-stale
    approval cards for that service (audit event, never silent). Demotion: keep the designed
    L3→L1 trust ramp — fix only the ergonomics: stop erasing `demoted_from` on later L1 failures,
    record the CAUSING incident id on the autonomy doc, and emit ONE operator card, not N.
    Flag `AIRBAG_APPROVAL_COALESCE` (default OFF).
    *Proof:* emulator tests (N gated heals → 1 card ×N; deny settles N; sweep is service+
    signature-scoped) + flag-off byte-identical test.

### Phase 2 — The storm scorecard (the committable proof) — ~1.5d nominal
A **scenario layer** over the existing bench harness: a scenario = an ordered script of N alert
deliveries (distinct incident ids, one service) + probe-feedback injection (the fixture world
counts marked vs unmarked probe requests) + a scripted verify-failure to trigger the designed
demotion — driving the REAL seams (webhook parse → run_self_heal → autonomy/approvals) against
FixtureBackend, **sequentially scripted** (deterministic; no thread-race flake). HONEST FRAMING
(Gemini-review): the scorecard measures the storm's OUTCOME SHAPE on a deterministic replay; the
CONCURRENT transactional safety (N simultaneous deliveries → one leader) is proven separately by
the threaded lease-contention tests in Phase 1.1 — the scorecard does not claim to reproduce
concurrency, and both proofs together are the exit criterion. Metrics:
`heals_per_outage`, `approval_cards_per_outage`, `self_traffic_counted_in_detection`,
`unattended_terminal_states`. Commit BOTH scorecards: **flag-off (the honest 2026-07-02 shape:
N heals, N cards, self-poisoning) and flag-on (1/1/0/0)** — pre-registered, CI-ratcheted, exactly
the AIRBAG_BENCH.md pattern. *This is the honesty centerpiece: the storm stops being an anecdote.*

### Phase 3 — Action-evidence residuals — ~1.5d nominal
3.1 **Witness-freshness horizon + blind-landing visibility** *(1d)* — behind
    `AIRBAG_TARGET_EVIDENCE` (default OFF, and a documented NO-OP unless `AIRBAG_CAUSAL_CHECK` is
    also on): (i) a witness older than `WITNESS_FRESH_S` (default 7d) is treated as cold in
    `_preferred_target` + the re-aim (uses the already-stored `last_witnessed_at`); (ii) causal.py
    returns a machine-readable `probe_errored` flag; on probe-error + UNWITNESSED target,
    `_mitigate` makes ONE bounded probe retry, then **PROCEEDS fail-open** with a first-class
    `blind_landing: true` marker on the record/events. (Gemini-review MAJOR: the original
    ESCALATE version contradicted the locked v3 "never block a legit rollback" posture — a
    network blip must not abandon users mid-outage. Blind landings are now MEASURED — the storm
    scorecard counts them — and surfaced in report/proof, never blocking.)
    *Proof:* bench fixtures — probe-raises+unwitnessed → proceeds WITH the marker (and the retry
    observed); probe-raises+freshly-witnessed → proceeds unmarked; stale-witness → recency
    fallback; flag-off byte-identical.
3.2 **Close-time settlement** *(0.5d)* — behind `AIRBAG_CLOSE_SETTLEMENT` (default OFF): CLOSED
    witnesses the fix revision (`memory.witness_serving`) and credits the trust ramp WITHOUT
    double-counting (the mitigate-time `record_outcome` already counted this incident — persist
    `outcome_counted` on the pending record; CLOSED credits only if unset). Canary-fail semantics
    unchanged. *Proof:* extend test_complete_rollback.py; flag-off byte-identical.

### Phase 4 — Security + provenance ribbon — ~2d nominal
4.1 **Fix-path write hardening** *(1d — the allowlist is a HARD GATE, not a flag: it's a vuln)* —
    reject any LLM-chosen `path`/`test_path` outside the **configurable** allowlist
    `AIRBAG_FIX_ALLOWLIST` (default: the directory of the existing `AIRBAG_FIX_FILE` config =
    `target-app/` here; normalized, no `..`, `.github/` rejected unconditionally — the
    Gemini-review BLOCKER fix: configurable so a real repo can point it at `src/`, hard-enforced
    so a prompt-injected trace can never write a workflow file that executes with repo secrets); PR
    reuse keyed on the RCA's error signature (both pipeline and fallback paths) so an open PR is
    reused only for the SAME incident class; loop-exit truthfulness (never ship a stale sandbox
    verdict as "verified"). Plus mechanical CI-watch fixes: thread the pipeline-discovered
    `path`/`test_path` into self-correction (today corrections hardcode config.FIX_FILE and can
    never repair the agent's own red test). NOTE: reviving the full CI self-correction loop needs
    the human to grant the GitHub token **Checks:read** — the mechanical fixes ship regardless;
    the live red→corrected→green proof is unblocked-by-Jason.
    *Proof:* adversarial unit tests (a `.github/workflows/x.yml` write is rejected + heal degrades
    gracefully; wrong-incident PR not reused; same-incident PR still reused — no PR spam).
4.2 **KMS-signed proof bundle** *(1d)* — behind `AIRBAG_PROOF_SIGN` (default OFF, fail-open:
    signing failure degrades to today's digest-only, never blocks a heal): at MITIGATED/CLOSED,
    persist the **canonical bundle snapshot** on the incident record (the record mutates later —
    sign the snapshot, not a rebuild), sign its sha256 via Cloud KMS `asymmetricSign`
    (EC_SIGN_P256_SHA256) over httpx+ADC (zero new deps — the PyGithub-to-REST precedent); commit
    the public key PEM + an offline `scripts/verify-proof.py`; `/incidents/{id}/proof` carries the
    signature envelope. Infra: `infra/kms-setup.sh` (key ring + SA granted signerVerifier only).
    *Proof:* canonicalization stability, tamper→FAIL, wrong-key→FAIL, mocked-KMS-failure→degrade;
    live: one signed real heal, verified offline, committed to docs/proof/.
    *Honesty:* the signature proves the bundle was produced by the holder of the agent's KMS
    identity — provenance, NOT correctness of the decisions inside.

### Build-only-if-ahead (pre-agreed, in order)
5.1 **Pooled-Wilson SLO burn-rate detector** *(2.5d)* — the learned baseline that blocked it in v3
    now exists; closes the pre-registered `slo_slow_burn` bench miss (single-window Wilson LB of
    1/40 ≈ 0.45% can never clear the baseline; POOLED Wilson over K windows can — 9/300 FAILs),
    debounced (errors in ≥ N windows; an all-in-one-window spike collapses to PASS). Must ship
    with 5.2. Default `AIRBAG_SIGNALS` unchanged.
5.2 **Baseline integrity guard** *(0.75d)* — fold the EMA per the `_healthy_witness` rule (PASS or
    zero-error sample; skip INCONCLUSIVE-with-errors — today a slow burn poisons the baseline it
    would be measured against, state_machine.py:113-118) + clamp per-fold drift.
5.3 **Revision-delta evidence** *(1.5d)* — LLM-free spec diff (image digest, env NAMES, limits) of
    bad vs target revision, attached to record/report/proof: the honest "what changed" forward
    story for latency incidents (which correctly get no fabricated fix-PR).

## 4. What NOT to build (cut, with reasons)
- **Auditor agent (A2A attestation)** — survived review, genuinely novel (deterministic crypto
  verification, NOT the cut LLM-verifier), but it's a NEW Cloud Run service days before
  submission; **deferred to finals week** (7/30→8/19) as the headline stretch.
- **Remote-MCP governance tiers** — conditioned on reusing 1.1's correlation window; defer with
  the auditor (same A2A story).
- Carried DO-NOT list unchanged: no GKE, no RL tuning, no blame engine, no fleet UI, no second
  same-model LLM verifier, no causal candidate-walk, no topology ledger, no pre-deploy admission
  gate, no canary on the initial rollback. **Storm-safety scope guard:** do NOT delete the trust
  ramp to fix its ergonomics (demotion stays; only its bookkeeping and card fanout change), and
  `_burst` demo traffic is never marked as probe traffic (it simulates users).
- ChatOps stays blocked on human-provided Slack credentials.

## 5. Corrected/confirmed facts (verified against code during planning)
- The ONLY per-service lease today is `pending.try_begin_complete` (complete_rollback); both
  heal dedups key on the Monitoring incident id.
- The alert policy uses the BUILT-IN `request_count` metric — header-based exclusion is
  impossible there; observer-exclusion needs an additive LOG-BASED metric (alert-setup-v2).
- Gated (L1) heals still emit triage/detection probes BEFORE the autonomy gate — a gated storm
  still amplifies itself. (1.1's attach path returns before triage; this is the main damper.)
- `demoted_from` is erased by subsequent L1 failures; approvals expire silently by lazy TTL.
- Five unmarked diagnostic httpx clients in backends/gcp.py (109/164/211/258/339); zero marking
  exists anywhere today.
- github_pr.py commits LLM-chosen paths with no validation; validate-fix.yml executes on the
  branch being written; PR reuse matches any open `airbag/fix*`.
- `last_witnessed_at` exists per ledger entry but is only used for eviction.
- Nominal effort: P1–P4 ≈ 9d against ~4–5 real days — same over-provision ratio v4 shipped with;
  the ladder below is pre-agreed.

## 6. Risks + descope ladder
Ladder (cut in order): (1) drop 5.x entirely (default posture already); (2) drop 4.2 KMS signing;
(3) drop 3.2 close-settlement; (4) descope 1.3 to coalescing-only (no sweep); (5) descope Phase 2
to the flag-off baseline scorecard only (pre-registered, labeled "mitigations pending").
**The FLOOR (non-negotiable): 1.1 + 1.2 + the Phase-2 storm scorecard + the 4.1 path allowlist**
— the marquee claim with its committed proof, plus the security fix (an active prompt-injection→
workflow-write vulnerability is not descopeable; Gemini-review MINOR). Key risks: storm scenario
determinism (mitigate: sequentially scripted
deliveries, no real threads); alert-policy change touching live alerting (mitigate: v2 is
additive; cutover only after live verify + after the video); approval-signature collisions
merging DIFFERENT decisions (mitigate: target in the key; tests); correlation lease deadlock on
escalated outcomes (mitigate: lifecycle spec'd in 1.1 + TTL backstop); KMS IAM friction
(mitigate: fail-open, degrade to digest).

## 7. Cadence (unchanged)
TDD per item; full suite + ruff (E9,F) before every commit; bench ratchet green; architecture
invariant run on every commit touching state_machine/causal/backends/signals/memory (add any new
action-tier module to `_action_files()` — Phase 1's changes live in state_store/autonomy/backends,
none of which import the LLM; the guard test for probe-marking is NEW and additive); adversarial
review (agy Gemini 3.1 Pro and/or a multi-agent workflow) BEFORE each substantial commit; live
verify on real Cloud Run; demo baseline left HEALTHY; commit+push incrementally; ONE test count
across docs; google-adk stays 1.x.

## 8. Open questions for Jason (defaults chosen; override at kickoff)
1. **Prod flag posture after live verification:** flip the three storm flags ON in deploy.sh (they
   make the live demo MORE robust — no more self-alert storms) — **default: ON after live verify;
   alert-policy v2 cutover only after the demo video is recorded.**
2. **KMS signing in prod** (creates one KMS key, ~zero cost): **default: yes** (flag ON after
   live verify; fail-open protects the demo).
3. **Burn-rate detector (5.1+5.2)** if time remains: **default: build only if P1–P4 land early.**
4. **GitHub token Checks:read** (revives the live CI self-correction proof): **default: Jason
   grants when convenient; mechanical fixes ship regardless.**
5. **Auditor agent**: **default: defer to finals week.**
6. **Fix-path allowlist as a hard unflagged gate** (it's a security fix, not a behavior choice):
   **default: yes.**
