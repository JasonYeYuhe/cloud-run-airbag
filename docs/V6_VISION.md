# Airbag v6 — Vision & Development Plan (finals week)

> Plan of record for the finals stage. Produced by a grounded planning workflow (5 code/market
> fact-finders → 6 first-principles ideation lenses → dedup → per-bet refute-by-default verification):
> **25 raw bets → 11 deduped → 9 survived both adversarial lenses, 2 refuted (§5)** — the survivors are
> transcribed here against the REAL code seams (every file/symbol re-verified against current code before
> commit). Window:
> **finals dev 2026-07-30 → 2026-08-19** (~3 weeks), **one human**. Submission 2026-07-10 is DELIVERY
> only (video/deck/writeup) — no dev. This plan assumes v5 shipped + live-verified on real Cloud Run
> (agent rev 00041, commit 8677901, 347 tests).

## §0. Adversarial review (three rounds, all folded)
The v5 discipline (§0 Gemini 3.1 Pro pass) repeats here: run the marquee (Phase 1 Auditor) past
`agy` Gemini 3.1 Pro and/or a multi-agent refute-by-default workflow BEFORE the first substantial
commit.

**Round 1 (pre-drafting refutation pass)** — findings already folded:
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
   bytes with `\uXXXX` escapes. A naive JS/second-consumer canonicalizer emits raw UTF-8 (2235 bytes)
   and would show **INTEGRITY FAIL on a genuinely valid heal** — the worst failure mode for a
   verify-it-yourself surface. Any third canonicalization consumer needs a byte-parity test + a
   `bundle_version` field before it ships.
4. *(MAJOR — folds into 2)* The one committed live signed bundle reads `image_changed: false` (verified)
   because the demo faults are `FAULT_MODE` env-value toggles on the SAME image — the 5.3 headline
   evidence field is provably dead in the artifact. Exercise it once, for real, or don't narrate it.
5. *(MAJOR — folds into 3)* `mcp_remote.py` imports `apply_approval` from `state_machine` (which
   imports the LLM), so it can NEVER go in `_action_files()`. The governance logic MUST be a SEPARATE
   pure module (`mcp_governance.py`) or the LLM-free guarantee for the new authz code is unenforceable.
6. *(MINOR — folds into Phase 1.2, which now owns it BY NAME; Round 2 #20 caught that the original
   "folds into 4.2-hardening" label left it orphaned in v6's phase list)* `sign_digest`'s httpx
   timeout is 15s AND there is a second unbounded network call (`creds.refresh`) in the same
   fail-open path; a KMS/token hang at the terminal MITIGATED stamp can extend settlement. Bound
   both; keep the same fail-open.

**Round 2 (multi-agent adversarial review, 2026-07-09 — 35 raised, 32 confirmed after
refute-by-default verification; agy/Gemini was down, so the review ran as the cadence's documented
substitute: a 40-agent workflow + Codex as second model):** ALL 32 folded into the sections below;
the load-bearing ones:
1. *(BLOCKER #10 → §1, §1b.2, §1b.4)* The Trinitite "self-signed, single-party" contrast is refuted
   by the doc's own citation — recharacterized; Gap-2 weight shifted to shipped-remediation-agent
   scoping + verifiED-vs-verifiABLE.
2. *(BLOCKER #18 → Phase 6, Phase 7, §4)* Heartbeat-on-emit would usurp a live-but-slow leader
   mid-sampling — Phase 6 redesigned around a separate `heartbeat_at` field; `lease_until` stays
   claim/settle-only; P7 becomes P6's prerequisite.
3. *(BLOCKER #28 → new Phase 9, §4 rung 0, §6)* Zero days were budgeted for the finals deliverable
   itself — a protected Delivery block (code freeze 2026-08-16) is now rung 0 of the ladder.
4. *(MAJOR #1 → §1b.3 #4/#5, Phase 1)* DSSE as spec'd would not verify with cosign — DSSE + in-toto
   merged into ONE deliverable: the payload IS the Statement, subjects stated per-Statement, second
   KMS sign over sha256(PAE) budgeted.
5. *(MAJOR #2 → Phase 2)* "ONE transact writes BOTH docs" is impossible with the single-doc
   primitive — a `transact_multi` primitive is now the phase's first deliverable (+0.25d).
6. *(MAJOR #4 → Phase 2)* A counter-signed HEAD alone misses a whole-chain rewrite between audits —
   checkpoints now CHAIN (prior `(seq, entry_hash)` containment check, +0.25d).
7. *(MAJOR #5 → Phase 4, §0.3, §7)* The canonical-parity trap was misdiagnosed as escaping-only —
   the Explorer now verifies LITERAL bytes (DSSE payload primary; number-token-preserving parser
   for legacy), and the DER→P1363 converter is spec'd.
8. *(MAJOR #6 → §2(a), Phase 1.2, Phase 3, Phase 4)* Registry-driven key resolution enabled
   cross-key type confusion — registry entries gain a `role` field, signed payloads gain in-band
   type tags, §2(a) rephrased to what the code actually does.
9. *(MAJOR #11, w/ #9 → §1, §1b.3 #1, Phase 1 (i), §4)* SCITT Receipt/Transparency-Service
   vocabulary is true only if Phase 2 ships — gated on the ladder everywhere it appears.
10. *(MAJOR #25 → §6)* "Same over-provision ratio as v5" was arithmetically false — §6 restated:
    final ~3 days (≈08-16→08-18) hard-reserved for delivery, v5 was 2.0x / v6 ≈1.8x, day-8
    checkpoint enters the ladder if P1 isn't live-verified.
11. *(MAJOR #29 → Phase 1 money shot)* The three-states beat had no mechanism — committed tamper
    script, rogue-key fixture, pinned incident id, auditor status page, ~5–10s poll; re-budgeted
    to ~1d.

**Round 3 (Codex gpt-5.5 independent pass, 2026-07-09 — verdict CHANGES-NEEDED, 13 findings, ALL
folded):** the two BLOCKERs:
1. *(BLOCKER #1 → §1, Phase 1.2, §4)* Phase 1 alone verifies ONE proof bundle fetched from the
   audited service — it cannot prove completeness, non-suppression, or log continuity. Thesis
   re-scoped: Phase 1 = *"independently verifies + counter-signs a published proof bundle"*; the
   stronger claims arrive ONLY with Phase 2. The 1.2 attestation now also binds the FETCH CONTEXT
   (raw fetched-bytes digest, agent URL, requested incident id, HTTP status, and a
   `bundle.incident_id == requested id` check).
2. *(BLOCKER #2 → §1, §1b.1)* "The only Cloud Run remediation agent…" and "verifiably empty" are one
   counterexample away from refuting the deck — de-absolutized to §1b.2's survivable form (*"we found
   no shipped remediation agent that independently verifies and counter-signs its own actions"*),
   with GitHub/search absence marked as search-limited evidence, never nonexistence proof.

The other 11 folded in place: Sello/Governing-Actions reframed as adjacent validation (§1/§1b);
configured-key-never-envelope-key verification direction stated in 1.1; auditor-OWNED checkpoint
state + anti-reset FAIL (Phase 2); generation fencing moved to the traffic-mutation call sites
(Phase 6); token→caller resolution seam as Phase 5's first deliverable; DSSE golden-fixture-in-CI
hard gate (Phase 1 borrows); Phase 1 day-3/day-5 internal checkpoints; §4 ladder swap
(P5 cut BEFORE P4); "append-only actor log" renamed durable actor audit trail (Phase 5);
EU-AI-Act Digital-Omnibus hedge (§1b); deploy.sh MCP max-instances contradiction gated +
smoke-tested (Phase 5).

## 1. Executive summary
The arc: **v2** made the agent durable and governed; **v3** made **detection** trustworthy; **v4**
made the one reversible **action** provably correct; **v5** made the agent **safe around itself**
(storm-safe autonomy — coalesce the alert storm, mark its own probes, coalesce approvals, sign the
proof). **v6 closes the last credibility gap: a claim of a safe, correct heal is only as good as an
INDEPENDENT PARTY'S ability to verify it.**

The market moved under us since submission and the finals framing must move with it — now
**primary-source verified** (a 6-modality live-web sweep, 2026-07-04→08; the full field snapshot with
URLs is §1b): autonomous prod remediation is **no longer white space** — Azure SRE Agent is GA
(approval-gated, with a permissioned autonomous mode), Datadog Bits Remediation is Preview
(guardrail-gated), AWS DevOps Agent investigates autonomously behind "immutable audit journals"
(platform trust, unsigned), PagerDuty's "Fully Autonomous Responder" hits EA in H2 2026 — and a
**Google Cloud Run hackathon writeup (Nov 2025) already published our base loop**
(Gemini-diagnoses → deterministic rollback → MTTR<3min). A judge who hears "we act on prod" in August
will call it **table stakes**. The finals pitch must never lead with the loop — lead with *"self-heal
loops exist; the unsolved problem is proving the robot did the right thing."*

Two gaps survive — under PRECISE phrasing (verdicts + evidence in §1b.2):
- **Agent self-safety / the observer effect** — alert-LEVEL grouping is commodity (PagerDuty IAG,
  Keep 12k★ — never claim it); but **remediation-level coalescing (N alerts → ONE heal) and
  observer-safe probes appear nowhere** in any shipped or announced competitor material we read; no
  vendor even names self-amplification. v5 shipped + live-verified the mitigations. Stage phrasing
  leads with the observer-effect half — *"the agent can't false-alarm itself"* — with "doesn't
  dogpile" second: coalescing alone has a composition neighbor (alert grouping + incident-scoped
  automation), pre-armed in §1b.4 #10.
- **INDEPENDENT cryptographic attestation of a remediation agent's actions** — signed AI audit logs
  now exist commercially: DeepInspect (signer inside the vendor's own stack) and Trinitite
  (hash-chained receipts, **third-party-anchored** via RFC 3161 + Sigstore Rekor,
  browser-verifiable) — but both are **horizontal governance layers, not remediation agents**: their
  verification attests log integrity, never the semantics of a heal; sigstore-a2a /
  A2A Signed Agent Cards attest agent *identity*, never agent *work*; the academic proposals
  (Sello 2026-06, "Governing Actions, Not Agents" 2026-06) are PoCs. The one sentence that survives
  every refutation: **"no shipped remediation agent PROVES its own actions to an independent
  verifier"** — never "nobody signs agent logs," and never "self-signed, single-party" of Trinitite.

v6's thesis: **provable autonomy — a Cloud Run remediation agent that is safe around ITSELF *and*
whose heals are independently verified and counter-signed by an LLM-free auditor.** Two Round-3
precision rules govern this sentence everywhere it is pitched: **scope it to what ships** (Round 3
#1) — on the Phase-1 floor the claim is *"an independent auditor verifies + counter-signs a published
proof bundle"*; "proves exactly what it did", completeness, and no-suppression arrive ONLY with
Phase 2's log — and **de-absolutize it** (Round 3 #2): never "the only"; the survivable form is
§1b.2's *"we found no shipped remediation agent that independently verifies and counter-signs its own
actions"* — search-limited evidence, stated as such. The June-2026 research wave (arXiv 2606.26298
"Governing Actions, Not Agents"; 2606.04193 "Sello") converged on an ADJACENT architecture as
*proposals* — Sello is receiver-signed receipts + transparency-log accountability, not post-hoc proof
verification, so the deck line is **adjacent validation, never "we shipped what they propose"**
(Round 3 #3): cite them FIRST, sized to what ships — reserve the "log-backed accountability"
vocabulary for a landed Phase 2; on the Phase-1 floor say *"June's papers argue agent actions need
independent cryptographic accountability — we shipped an adjacent, working form on Cloud Run."*
And **RFC 9943 (IETF SCITT, June
2026, Proposed Standard)** standardizes the flow the FULL v6 stack implements (issuer → Signed
Statement → Transparency Service → Receipt) — describe the design in SCITT vocabulary and it reads as
industry-grade, weeks-fresh, **but gate the terms on what ships**: a Receipt attests REGISTRATION in
an append-only log, so the full mapping is true only with the Phase-2 log live; on the Phase-1 floor
say "SCITT-shaped roles (Issuer/Verifier)" (watch item §1b.4 #12).

## §1b. Prior art & what we borrow (researched 2026-07-04)

*This subsection replaces the ⚠️ best-effort-recon caveat in §1. Six modality scans (commercial vendors, GitHub OSS, supply-chain/attestation standards, agent-trust standards, AI-audit products + research, hackathon/demo-craft) ran against the live web 2026-07-04→08. Every claim below marked **[V]** was read from the cited primary source; items marked **[lead]** are search-level only and must not go on a slide unverified. The two facts previously flagged for slide-blocking re-verification are now **confirmed on primary sources**: RFC 9943 (SCITT architecture, Birkholz et al., June 2026, Proposed Standard) is real — [rfc-editor.org/info/rfc9943](https://www.rfc-editor.org/info/rfc9943/) — and EU AI Act **Article 12** record-keeping for Annex III high-risk systems becomes applicable **2026-08-02**, 17 days before finals ([artificialintelligenceact.eu/article/12](https://artificialintelligenceact.eu/article/12/), [EC AI Act Service Desk](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-12)) — as currently published (the EC page carries a Digital Omnibus proposal caveat) — recheck at kickoff; regulatory-context color, not a compliance claim (Round 3 #12). Article 12 mandates the **log**, not cryptographic integrity — and a Cloud Run remediation bot is almost certainly NOT an Annex III high-risk system, so the obligation isn't even Airbag's to carry: pitch it as *the record-keeping bar regulators now expect of high-risk AI — Airbag isn't Annex III-classified, and exceeds it anyway*, never as legally required (the scope poke is pre-armed in §1b.4 #11).*

### 1b.1 Field snapshot (all [V] unless noted)

**Hyperscalers.** [Azure SRE Agent](https://learn.microsoft.com/en-us/azure/sre-agent/overview) is GA (~2026-03-10): approval-gated by default, with a shipping autonomous/privileged mode behind Allow/Ask tool policies and a pre-execution "Permission gate" — the closest thing to our deterministic-layer story, but as a policy *filter on LLM tool calls*, not our inversion (FSM acts; LLM only diagnoses; AST-enforced). Its [dedicated audit doc](https://learn.microsoft.com/en-us/azure/sre-agent/audit-agent-actions) (2026-03-18) shows auditing = unsigned platform telemetry in a customer-owned Application Insights resource, queried by KQL — purgeable, and forgeable by anyone holding the ingestion connection string; no cryptographic integrity, no offline verification. [AWS DevOps Agent](https://aws.amazon.com/blogs/devops/leverage-agentic-ai-for-autonomous-incident-response-with-aws-devops-agent/) (2026-03-31) advertises "immutable audit journals the agent cannot modify" — trust-the-platform tamper *resistance*, no signatures, no offline verification; **the most likely gap-eroder before finals**. [Gemini Cloud Assist Investigations](https://docs.cloud.google.com/gemini/docs/cloud-assist/investigations) — Google's first-party answer on Google's own platform — is verified advisory-only ("recommends the next troubleshooting steps"; its OAuth token "is never used for mutating data") and restricted to Premium Support since 2026-04-10. This is our best on-stage contrast.

**Incumbents.** [Datadog Bits Remediation](https://www.datadoghq.com/blog/dash-2026-new-feature-roundup-keynote/) (DASH 2026-06-09) is Preview, guardrail-gated, no crypto anywhere. [PagerDuty](https://support.pagerduty.com/main/docs/sre-agent)'s shipped agent is recommendation-centric; its "Fully Autonomous Responder" is EA **H2 2026 — may be announced near finals**. [Komodor Klaudia](https://komodor.com/blog/komodor-introduces-extensible-autonomous-multi-agent-architecture-for-ai-driven-site-reliability-engineering/) claims autonomy loudest with the least documented governance. [Sedai](https://sedai.io/platform) is the strongest safe-autonomy prior art (Datapilot/Copilot/Autopilot ladder, 8 patents, 25M+ actions, SLO-verified reversible changes) — but in cost/perf optimization, and its patents cover action *safety*, not action *provenance*. [Harness](https://devops.com/harness-adds-autonomous-ai-agents-to-automate-devops-workflows/) agents (GA 2026-06-30) act pre-prod inside pipelines under OPA policy — citable validation that policy must be deterministic code. [Shoreline](https://siliconangle.com/2024/06/19/nvidia-reportedly-acquires-incident-automation-startup-shoreline-100m/) was reportedly acquired by NVIDIA (~$100M, unconfirmed — the cited source itself says "reportedly") and discontinued as a standalone product — market-validation ammo, hedged to match the citation.

**OSS.** [HolmesGPT](https://github.com/HolmesGPT/holmesgpt) (CNCF Sandbox, 2.8k★, daily commits) is the biggest overlap — Operator Mode: 24/7 checks, deployment verification, fix-PRs, K8s remediation via MCP — but **the LLM invokes the remediation tools directly**, unsigned, K8s-only. [k8sgpt-operator](https://github.com/k8sgpt-ai/k8sgpt-operator)'s alpha Mutation CRD applies LLM-authored config to the cluster gated by a similarity score (LLM output *does* touch prod). [Keep](https://github.com/keephq/keep) (12k★) owns alert-level dedup as a platform — **never claim alert-level grouping**. [Keptn v1](https://github.com/keptn/keptn) — the strongest pre-LLM deterministic remediation engine — is archived (Dec 2023): *"deterministic remediation died; LLM remediation is unaccountable; Airbag fuses the two."* In the **Cloud Run niche we found no occupant** (2026-07-08 — search-limited evidence, not nonexistence proof; Round 3 #2): repo/code searches returned only 0-star projects, no GoogleCloudPlatform sample, and verified-negative on incident-agent templates in the openai/anthropics orgs — the stage form is "we searched and found nobody", which is honest AND checkable.

**Hackathon context.** The event is [verified](https://cloud.google.com/blog/ja/products/ai-machine-learning/devops-ai-agent-hackathon-2026?hl=ja): Findy × Google Cloud Japan, Google Shibuya finals, ¥2M, judged on つくる/まわす/とどける. [Series winners](https://zenn.dev/taku_sid/articles/20250403_ai_hackathon_review) reward story-first openings + one quantified metric. **[AgentOps](https://abhinav1singhal.medium.com/google-cloud-run-hackathon-049e96e8aab9)** (Nov 2025 — the global Google Cloud Run Hackathon on Devpost, a DIFFERENT event than this series) already published our base loop (Gemini diagnoses, deterministic fixer rolls back via revisions, MTTR 15–30min → <3min) — the loop is hackathon-commodity; the proof stack is not. **[RedAgent](https://about.gitlab.com/blog/gitlab-ai-hackathon-2026-meet-the-winners/)** — an agent that verifies AI findings — took "Most Impactful" at GitLab's 2026 hackathon: agent-verifies-agent demonstrably wins awards this year.

### 1b.2 The two gap claims — verdicts

**Gap 1: agent self-safety.** SPLIT — and the split is the pitch. Alert-*level* grouping is commodity (PagerDuty IAG, Keep). But **remediation-level coalescing (N alerts → 1 heal) and observer-safe probes appear nowhere** in any shipped or announced competitor material we read; no vendor even names self-amplification. Caveat (Round 2 #12): the coalescing half has a composition neighbor — alert grouping + incident-scoped automation fires ONCE per grouped incident, functionally "N alerts → one remediation" — so LEAN the stage phrasing on the observer-effect half, *"the agent can't false-alarm itself"*, which genuinely has no neighbor in any material read; the coalescing distinction (heal-time, across DISTINCT incident objects, zero-probe followers) is pre-armed in §1b.4 #10.

**Gap 2: independent cryptographic attestation of a remediation agent.** HOLDS, precisely phrased — and Round 2 #10 forced precision on the nearest neighbor. [DeepInspect](https://www.deepinspect.ai/blog/signed-audit-logs-for-ai-requests) signs AI audit records with a signer inside its own vendor stack; [Trinitite](https://trinitite.ai/solutions/auditors/) ships hash-chained, browser-verifiable receipts anchored to THIRD-PARTY trust roots (RFC 3161 timestamping + Sigstore Rekor) — so never say "self-signed, single-party" of Trinitite. What both actually are: **horizontal governance layers whose verification attests log integrity, not the semantics of a heal — and neither is a remediation agent**. Corollary (same finding): hash-chaining (Phase 2) and verify-in-browser (Phase 4) are NOT novel per se — Trinitite ships both, horizontally; pitch them as the substrate the counter-signing, domain-aware auditor walks (**verifiED vs verifiABLE**). AWS's "immutable journals" are platform-trust; [sigstore-a2a](https://github.com/sigstore/sigstore-a2a) / [AGNTCY](https://spec.identity.agntcy.org/docs/intro/) / [A2A v1.0 Signed Agent Cards](https://a2a-protocol.org/latest/specification/) attest agent *identity*, never agent *work* (runtime identity is A2A's own [open issue #1672](https://github.com/a2aproject/A2A/issues/1672) — proposing ECDSA P-256, our exact KMS curve); [nono](https://nono.sh/blog/secure-agent-audit) (by sigstore's founder, Apr 2026) hash-chains + signs its own log and names transparency logs as future work — **the Phase-2 log, if it ships, is exactly what it lacks** (Phase-2-conditional deck copy, per Round 2 #11); [Sello](https://arxiv.org/abs/2606.04193) (2026-06-02) and ["Governing Actions, Not Agents"](https://arxiv.org/abs/2606.26298) (2026-06-24) propose an ADJACENT architecture — receiver-signed receipts + transparency-log accountability, not our post-hoc proof verification (Round 3 #3: adjacent validation, never convergence-on-us) — **as prototypes/papers**. The one sentence that survives every refutation: **"no shipped remediation agent PROVES its own actions to an independent verifier"** — never "nobody signs agent logs."

### 1b.3 What we borrow (folded into phases; ranked cheap-and-loud first)

| # | Borrow | From | Into | Δ |
|---|--------|------|------|---|
| 1 | **SCITT vocabulary, gated on the log** (Round 2 #11): Airbag=issuer always; auditor=transparency service, counter-signature=Receipt, bundle+receipt=Transparent Statement, tri-state=registration outcome ONLY once Phase 2 ships — floor-only wording is "SCITT-shaped roles (Issuer/Verifier)" | [RFC 9943](https://www.rfc-editor.org/info/rfc9943/) (June 2026 — weeks old at finals) | Partial in Phase 1 naming/deck; full mapping conditional on P2 | +0d |
| 2 | **Checkpoint + witness/monitor terms**; attestation gains `{chain_intact, gaps:[]}` — auditor explicitly attests no-suppression (kills Sello's named weakness) | [RFC 9162](https://www.rfc-editor.org/info/rfc9162/), [Rekor](https://docs.sigstore.dev/about/security/) | Phase 2 | +0.25d |
| 3 | **Demo craft**: three verbs on screen, 15s outage hook + ONE metric (named at kickoff per Round 2 #32: *"alert → independently-attested recovery: Xs"*, live on the auditor card), tamper-fail beat (already in Phase 1's money shot), pre-recorded take, adversarial LLM pseudo-review vs the rubric | [Rubric](https://cloud.google.com/blog/ja/products/ai-machine-learning/devops-ai-agent-hackathon-2026?hl=ja), [series analysis](https://zenn.dev/taku_sid/articles/20250403_ai_hackathon_review), [kikagaku playbook](https://zenn.dev/kikagaku/articles/d2876e8e2e50a5) | Money shot + §0 + Phase 9 + article | +0d (naming; delivery TIME lives in Phase 9) |
| 4 | **DSSE + in-toto as ONE merged deliverable** (Round 2 #1 — was two rows): keep legacy envelope byte-identical; ADD a DSSE envelope whose payload IS an in-toto Statement (a raw-bundle payload fails cosign's `verify-blob-attestation` by design — it parses the payload as a Statement and matches the blob digest against its `subject`); the DSSE sig is over `PAE(payloadType, payload)`, i.e. a SECOND KMS sign over `sha256(PAE)`, budgeted → verify an Airbag heal with **cosign** on camera; verifying literal payload bytes de-risks Phase 4's canonical-parity trap | [DSSE](https://github.com/secure-systems-lab/dsse) | Phase 1.2 (+helps Phase 4) | +1–1.5d (merged w/ #5) |
| 5 | **in-toto Statement shapes** (the payload for #4), subjects stated PER Statement, never shared: heal-attestation Statement — predicateType `airbag.dev/heal-attestation/v1`, predicate = the bundle, subject = `{name: incident_id, digest.sha256 = sha256(canonical bundle bytes)}` (what cosign matches) plus the Cloud Run revision/image digest; audit-verdict Statement — `audit-verdict/v1`, subject = sha256 of the heal envelope | [in-toto spec](https://github.com/in-toto/attestation/blob/main/spec/README.md) | Phase 1.2 | (inside #4's merged budget) |
| 6 | **SLSA-style schema split**: `externalParameters` = Gemini's suggestion vs `internalParameters` = FSM-resolved action — the LLM-quarantine made *visible in the attestation*; pitch L1 logged / L2 signed / L3 independently audited | [SLSA v1.0](https://slsa.dev/spec/v1.0/provenance) | Phase 1 schema + deck | +0.25d |
| 7 | **Intent binding**: `trigger_evidence_digest` (sha256 of the triggering alert evidence) in the bundle | [ACAP draft](https://datatracker.ietf.org/doc/draft-yakung-oauth-agent-attestation/) | Phase 1 (pairs w/ §8 Q3) | +0.25d |
| 8 | **Domain-separation tags** (`airbag.log.entry.v1`, `airbag.checkpoint.v1`) in all hashing | [nono](https://nono.sh/blog/secure-agent-audit) | Phase 2 | +0.1d |
| 9 | **Discovery-time tool hiding + tiers as MCP-native scopes** (WWW-Authenticate, 403 insufficient_scope step-up) — and the anti-refutation framing: AWS ships tool *allowlisting*; we ship graded *autonomy* with per-tier proof obligations | [AWS MCP Gateway](https://aws.amazon.com/blogs/opensource/governing-ai-assets-at-scale-with-mcp-gateway-and-registry/), [MCP auth spec](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) | Phase 5 | +0.5d |
| 10 | **Quantified autonomy budgets** per tier (`max_heals_per_hour`, consecutive-autonomous cap); explain tiers in Sedai's observe/approve/auto vocabulary | Bifrost depth-limits **[lead]**; [Sedai](https://sedai.io/platform) [V] | Phase 5 registry | +0.25d |
| 11 | **TUF-style rotation** for `registry.json` (versioned, expiring, previous key signs next) — answers "what happens when you rotate the KMS key?" with Sigstore's own mechanism | [TUF spec](https://theupdateframework.github.io/specification/latest/) | Phase 3 | +0.5d |
| 12 | **SPIFFE-style URIs** (`spiffe://airbag.dev/agent`, `…/auditor`) as issuer IDs; call Cloud Run SA + KMS binding "workload attestation" | [SPIFFE](https://spiffe.io/docs/latest/spiffe-about/overview/) | Phase 1.2/3 | +0.1d |
| 13 | **Red-herring refusal beat**: decoy alert → causal pre-check declines the rollback, on camera; optional ITBench scenario port, AIOpsLab-style per-stage reporting | [OpenSRE](https://github.com/Tracer-Cloud/opensre), [ITBench](https://github.com/itbench-hub/ITBench), [AIOpsLab](https://github.com/microsoft/AIOpsLab) | Phase 8 / demo | +0.5d (stretch) |
| 14 | **Nonce challenge-response** auditor↔healer (liveness + key possession) | [arXiv 2512.17259](https://arxiv.org/abs/2512.17259) | Phase 1 stretch — **cut first** | +0.5d |

Items 1–3 are free at the naming level (the delivery TIME behind item 3 is not free — Phase 9 owns it; Round 2 #28). Items 4–8 total ≈2d and buy verification-by-cosign, a standards-shaped schema, and replay/suppression defenses. Items 9–12 live inside existing phase budgets. 13–14 are sacrificial. Sequencing rule (Round 2 #26): NO borrow — including the +0d naming ones — merges before the Phase-1 floor is live-verified and its cross-attested bundle committed to `docs/proof/`.

### 1b.4 Threats & watch items before 2026-08-19

1. **AWS DevOps Agent** adding signatures to its "immutable journals" — watch AWS announcements; pre-armed line: *"they promise the agent can't rewrite history; we let YOU verify it, offline, trusting no platform."*
2. **PagerDuty Fully Autonomous Responder** EA may land near finals (no crypto in its docs — the proof story survives).
3. **Azure's Permission Gate** rhetorical collision — rehearse the filter-vs-inversion distinction.
4. **AgentOps-shaped entries in this very hackathon** — never lead with the loop; lead with *"self-heal loops exist — the unsolved problem is proving the robot did the right thing."*
5. **"Signed audit logs" no longer unique** (DeepInspect, Trinitite, SecureAuth) — and Trinitite in particular ships third-party-anchored (RFC 3161 + Rekor), browser-verifiable receipts, so even "independent-verifier" needs the remediation-agent scoping. Pre-armed for *"isn't this just Trinitite for Cloud Run?"* (Round 2 #10): Trinitite makes any agent's log verifiABLE — generic anchoring, integrity of the record; Airbag's auditor makes THIS heal verifiED — a second agent with its own KMS identity re-running domain-aware checks (signer pin, tri-state, revision delta) and counter-signing the verdict.
6. **AWS MCP Gateway** refutes any "nobody restricts MCP tools per caller" phrasing — Phase 5 is autonomy grading, not allowlisting.
7. **Academic adjacency** (Sello, Governing-Actions, "audit agents" in 2512.17259) — cite them FIRST, as ADJACENT validation, never convergence-on-us (Round 3 #3): receiver-signed receipts differ from post-hoc proof verification, so never *"we shipped what they propose"*; the "log-backed accountability" framing stays Phase-2-conditional (Round 2 #11) — with the log live, *"the accountability substrate June's papers call for"*; on the floor, *"an adjacent, working form of the independent-auditor direction they argue for."*
8. **つくる risk**: "why must this be an agent?" — answer is the two-agent story: Gemini diagnosis + an adversarially-independent auditor agent; two services, two KMS identities, neither trusts the other. For the same-operator poke (Round 2 #7): *"independent by construction — separate service, separate SA, separate key, zero shared code or write path, deployed in a SECOND GCP project (Phase 1.2); in production the auditor runs under a different administrative domain."*
9. **"Why not just Sigstore/Rekor?"** — pre-arm: offline verifiability, private infra, Cloud Run-native KMS, pinned-signer trust model; public-Rekor anchoring is Trinitite-adjacent table stakes, not a novelty flourish (Round 2 #10) — if it ships, frame it as substrate.
10. **Gap-1 composition refutation** (Round 2 #12): PagerDuty IAG (or Keep) groups N alerts into ONE incident, and incident-scoped automation (Automation Actions/Rundeck, Event Orchestration) fires ONCE per grouped incident — functionally "N alerts → one remediation," one docs-URL away. Pre-armed distinction: Airbag coalesces at HEAL time across DISTINCT incident objects via the per-service heal lease (works even when upstream alert-grouping failed or alerts arrive on separate policies), and followers emit ZERO diagnostic probes — lead with the observer-effect half.
11. **EU-AI-Act scope poke** (Round 2 #17): *"a Cloud Run remediation bot isn't Annex III high-risk — regulatory-washing!"* Pre-armed: correct — Airbag isn't Annex III-classified, and we say so; the pitch is exceeding the record-keeping bar regulators now expect of high-risk AI, never compliance.
12. **SCITT-literate judge asks where the append-only log is** (Round 2 #11): with Phase 2 shipped, point at the chain + counter-signed checkpoint; if Phase 2 was cut, the vocabulary has already degraded per the §4 ladder to "SCITT-inspired counter-signed attestation (Receipt semantics arrive with the transparency log)" — never let a slide say Receipt without the log.

### 1b.5 Honesty ledger (what is NOT verified)

- Bifrost/MintMCP/ContextForge gateway details, web3 TEE-agent production claims (Phala/Marlin/Automata), the IETF AUDIT BoF thread, the Kiteworks/hermes-agent/Sakura Sky demand signals, and the "Proof-of-Guardrail" paper (2603.05786) are **search-level leads** — usable as directional color, not slide facts.
- "No competitor has X" claims are **verified absence in the documents we read** (primary docs for every major vendor + OSS repo scans via GitHub API 2026-07-08), not omniscience — phrase as "we found no…" on stage, which is both honest and checkable.
- RFC 9943 and the EU AI Act 2026-08-02 date, previously flagged, are now **primary-source verified** (2026-07-08) and safe for slides — the AI-Act date only WITH the Round-3 #12 hedge attached: as currently published (the EC page carries a Digital Omnibus proposal caveat), recheck at kickoff; regulatory-context color, not a compliance claim.

## 2. The gaps v6 closes (grounded in code + live findings)
- **(a) Provenance is single-bundle and self-attested.** v5 4.2 signs each bundle, and the offline
  `verify()` (verify-proof.py:31-63) does two real checks — INTEGRITY (recompute sha256 over
  `_canonical(bundle)`) + PROVENANCE (ECDSA-P256 over the committed PEM). But **verify() never pins
  the SIGNER**: line 57 only *echoes* `sig.get("key")` — verify() verifies against whatever PEM it is
  handed and never checks signer IDENTITY. Precision (Round 2 #6): a rogue keypair already FAILS
  against the committed PEM today — the shipped wrong-key test is green; the pin becomes load-bearing
  the moment key resolution goes NAME-driven via Phase 3's registry/rotation. And the whole thing is
  Airbag verifying Airbag — no second party, no independent identity. → **Phase 1 (Auditor Agent,
  the marquee)**
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

### Phase 1 — Auditor Agent: independent A2A cryptographic attestation (THE marquee) — ~4.5d nominal
A **SECOND Cloud Run service** (`auditor/`, deployed in a SECOND GCP project — administrative-domain
separation, not just per-key IAM (Round 2 #7); its OWN least-privilege SA + its OWN KMS key) that polls
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
    Direction of trust, stated once (Round 3 #4): the verifier verifies against the CONFIGURED
    key/PEM (auditor config, later Phase 3's registry) — NEVER against a key looked up FROM the
    envelope's `signature.key`, which is unsigned metadata — and then REPORTS the configured identity
    as the verified signer; the envelope's key field is compared-to, never resolved-from.
    Honest tri-state: **SIGNED-VERIFIED** (integrity_ok AND signature_ok AND signer pinned) /
    **INTEGRITY-ONLY** (unsigned/pre-4.2, `signature_ok=None` — e.g. `live-5xx-heal-recency.json`) /
    **FAIL**. One refinement (Round 2 #8): INTEGRITY-ONLY alone would launder a signature-STRIP as
    honest — `sign_digest` is fail-open, so a post-cutover unsigned bundle is ambiguous between a KMS
    hiccup and an adversarial strip. The auditor computes `signed_expected = (incident ts >= the
    active key's not_before` from Phase 3's registry`)` and surfaces a post-cutover unsigned bundle
    as **DEGRADED — signature expected but absent** (distinct wording/color from the pre-4.2 badge;
    still non-blocking — fail-open stays legitimate). Never surface "verified correct" — provenance +
    integrity only (mirrors proof.py:88).
    *Proof (TDD, lifts test_proof_sign.py's baseline):* valid→SIGNED-VERIFIED; tamper one byte INSIDE
    `bundle` (e.g. `rolled_back_to`, NOT the outer `note`)→FAIL; wrong keypair→FAIL; **valid signature,
    wrong `cryptoKeyVersions/2`→FAIL** (the NEW case, absent from v5's wrong-*keypair* test);
    pre-cutover unsigned→INTEGRITY-ONLY; **post-cutover unsigned (stripped or KMS-hiccup)→DEGRADED,
    never plain INTEGRITY-ONLY** (the strip case v5's suite never covered).
1.2 **Auditor's second KMS identity + counter-signed attestation** *(1d)* — clone `infra/kms-setup.sh`
    into `infra/auditor-kms-setup.sh`, run against a **SECOND GCP project** (~1h extra; the auditor
    needs only its own KMS key + outbound HTTPS to the agent's public URL — the administrative-domain
    separation a security-background judge will actually grant as "independent", Round 2 #7): a NEW
    key `airbag-auditor`, `signerVerifier` granted to the AUDITOR SA ONLY (NEVER reuse `airbag-proof`
    — independence is load-bearing; the script is already parameterized via
    `AIRBAG_KMS_KEYNAME`/`AGENT_SA`). Two clone deltas the 1d must include (Round 2 #26): parameterize
    the hardcoded `PEM_OUT` (kms-setup.sh:43 — or the clone overwrites
    `scripts/airbag-proof-pubkey.pem`) and CREATE the auditor SA first (the script's default resolves
    the AGENT's SA, lines 31-34; SA-creation precedent: sandbox-job-setup.sh:27). The auditor
    canonicalizes an attestation `{attestation_version, incident_id, subject_digest, tri_state,
    signed_expected, verified_at, expected_key}` PLUS the fetch context it verified under (Round 3
    #1): `fetch: {raw_fetched_digest, agent_url, requested_incident_id, http_status}` —
    `raw_fetched_digest` = sha256 of the raw response bytes — with a verify-path check that
    `bundle.incident_id == requested_incident_id` (FAIL on mismatch): the attestation must bind WHAT
    was fetched, from WHERE, for WHICH request, or a valid bundle for incident A could answer a query
    for incident B. The type tag INSIDE the signed bytes is
    load-bearing, not cosmetic (Round 2 #6): once Phase 3's registry lists BOTH keys, an untagged
    attestation could be re-wrapped as a SIGNED-VERIFIED "heal" on any registry-driven verify surface;
    `bundle_version` (pulled forward from Phase 4, +0.1d — Round 2 #20) plays the same self-describing
    role for heal bundles. KMS-signs it, and commits `scripts/auditor-pubkey.pem` so the attestation
    is itself offline-verifiable by the same kernel. Attestation is READ-ONLY and out-of-band — it
    NEVER writes to the heal FSM. **FAIL-OPEN:** a failed/unreachable attestation surfaces as
    "unattested"; it structurally cannot block a heal (no write path in). Phase 1.2 also owns, BY
    NAME, Round-1 #6 (Round 2 #20 found it orphaned): bound BOTH `sign_digest`'s httpx call AND
    `creds.refresh` (the second unbounded call in the same fail-open path) — mandatory before the
    DSSE borrow doubles the terminal-stamp KMS exposure.
1.3 **New-service infra + own AST guard** *(1d)* — `auditor/Dockerfile` + `gcloud run deploy` with a
    zero-role SA (mirror the shipped `sandbox-job/` + `infra/sandbox-job-setup.sh` new-service
    precedent). The auditor lives OUTSIDE the agent's `_action_files()` AST scan (which globs `autosre/`
    only), so ship `auditor/tests/test_auditor_invariant.py` — but NOT as a naive denylist mirror
    (Round 2 #21): a mirrored `_FORBIDDEN` would pass `from autosre import proof` and silently void
    the independence claim the marquee rests on. For `auditor/verify.py`, enforce an import ALLOWLIST
    (stdlib + `cryptography` only — the same ~10-line ast walk, proving the STRONGER "zero agent
    imports" property the intro claims); keep the denylist for the rest of the auditor service (which
    legitimately needs httpx/google-auth); add one repo-level parity test in `agent/tests` asserting
    the auditor's forbidden set is a superset of `test_architecture_invariant._FORBIDDEN` (read the
    file by path — no import coupling, no drift). Two more 1.3 deliverables: a minimal auditor
    **status page** (single HTML route listing the latest attestations — the money shot's
    second-browser surface; Round 2 #29), and the standing-cost posture (Round 2 #31): the auditor
    runs scale-to-zero between demo windows, pinned warm only for recording/finals.
~1d **The money shot + honest coverage** *(re-budgeted 0.5d → ~1d — Round 2 #29 caught that the
    beats were named but the MACHINERY wasn't)* — next to Airbag's green "healed" card, the
    independently deployed auditor (different URL, different project, different identity; rendered on
    the 1.3 status page in a second browser window, **poll interval ~5–10s** so a flip lands within
    one camera beat) shows "AUDITOR: inc-X **SIGNED-VERIFIED** — provenance confirmed against pinned
    key `.../cryptoKeyVersions/1`, integrity OK". Demo ALL THREE states, each with a rehearsable
    mechanism: point at a PINNED pre-4.2 unsigned incident id (fallback if it ages out of Firestore:
    scripted recreation via one heal with `AIRBAG_PROOF_SIGN` briefly off) → **INTEGRITY-ONLY** shown
    as a first-class honest outcome (the strongest anti-hype signal — the opposite of Azure's
    "verified recovery" marketing; a post-cutover strip shows DEGRADED, never this badge); tamper a
    byte inside `bundle` live via a committed `scripts/demo-tamper.sh` (rewrites one byte of the
    stored proof doc through Firestore — the agent SA already holds datastore access — and restores it
    after) → **FAIL** flips on camera; swap the signer via a committed **rogue-key-signed fixture** (a
    local ECDSA keypair no other phase produces) → FAIL flips. Commit one real cross-attested heal to
    `docs/proof/`.
    *Flag posture:* the FLOOR needs no agent-side flag (the proof GET is already public); the auditor
    is simply **not-deployed by default** — the strongest form of default-OFF, and the recorded demo
    stays byte-identical. Every BORROW that touches the envelope is individually flagged or
    presence-keyed (Round 2 #20) — postures named per borrow in the standards paragraph below.

**Internal schedule gates (Round 3 #10 — Phase 1's ~4.5d is the one-person estimate most likely to
slip, and day 8 is too late to learn it):** **day-3 — offline verifier + signer pin green** (1.1's
full tri-state/pin test set passing) and **day-5 — counter-signed attestation live** (1.2's second
KMS identity signing a real attestation), both feeding the existing §6 day-8 checkpoint. Miss day-3 →
the 1.3 status page degrades to a static JSON route; miss day-5 → the borrows are presumptively cut
and day-8 becomes a formal ladder entry, not a review.

**Standards alignment riding Phase 1 (borrows — details + URLs in §1b.3; ~1.5–2d inside/adjacent to
the phase budget). Sequencing rule (Round 2 #26): NO borrow — including the +0d naming ones — merges
before the Phase-1 floor is live-verified and its cross-attested bundle committed to `docs/proof/`;
the DSSE emit in particular is a separate post-floor commit, because it touches `proof.py`, the
demo-critical path whose committed fixtures must stay byte-identical.** (i) describe the design in
**SCITT (RFC 9943) vocabulary, gated on the ladder (Round 2 #11)** — Airbag=issuer always;
auditor=transparency service, counter-signature=Receipt, tri-state=registration outcome ONLY once the
Phase-2 log ships; floor-only wording is "SCITT-shaped roles (Issuer/Verifier)" (+0d, naming only);
(ii) **DSSE + in-toto as ONE merged deliverable (Round 2 #1)**: keep the existing envelope
byte-identical (protects committed fixtures) and ADDITIONALLY emit a DSSE envelope whose **payload IS
an in-toto Statement** — NOT the raw canonical bundle: cosign's `verify-blob-attestation` parses the
payload as a Statement and matches the blob digest against its `subject`, so a raw-bundle payload
with payloadType `application/vnd.in-toto+json` fails the on-camera beat by design. Subjects, stated
explicitly PER Statement: the heal-attestation Statement (predicateType
`airbag.dev/heal-attestation/v1`, predicate = the bundle) carries subject `{name: incident_id,
digest.sha256 = sha256(canonical bundle bytes)}` — what cosign matches — plus the Cloud Run
revision/image digest; the audit-verdict Statement (`audit-verdict/v1`) carries subject = sha256 of
the heal envelope. The DSSE signature is over `PAE(payloadType, payload)` — a **SECOND KMS
`asymmetricSign` over `sha256(PAE)`** at the terminal stamp, budgeted here, emitted BESIDE (never
inside) the legacy envelope, gated behind `AIRBAG_PROOF_DSSE` (default OFF) within `_persist_proof`'s
existing fail-open try/except, with both network calls bounded first (Round-1 #6, owned by 1.2). The
on-camera verify: `cosign verify-blob-attestation --key scripts/airbag-proof-pubkey.pem --type
airbag.dev/heal-attestation/v1 --signature dsse.json --insecure-ignore-tlog canonical-bundle.json`
(+1–1.5d merged) — **HARD GATE (Round 3 #9): a golden fixture signed via the SAME KMS path, with
`cosign verify-blob-attestation` passing IN CI, is the prerequisite for promising the cosign camera
beat** — hand-rolled DSSE fails on PAE, payload type, predicate type, subject digest, key id, or
ECDSA encoding in ways prose review never catches; if the golden fixture is not green in CI by the
borrow's scheduled day, CUT DSSE outright (it's a borrow, not a phase — the auditor without DSSE
still wins); (iii) **SLSA-style parameter split** in the predicate — `externalParameters` = what
Gemini SUGGESTED vs `internalParameters` = what the deterministic FSM resolved/clamped — the
LLM-quarantine made VISIBLE in the attestation (+0.25d); (iv) **intent binding** —
`trigger_evidence_digest` = sha256 of the triggering alert evidence (anti-replay; pairs with §8 Q3) —
lands only AFTER `bundle_version` is in (1.2): evidence is ALWAYS present on the alert path, so
"keyed on presence" alone would NOT preserve prior bytes (Round 2 #20) (+0.25d); (v) **SPIFFE-style
issuer URIs** (`spiffe://airbag.dev/agent|auditor`) as identity strings — do NOT deploy SPIRE
(+0.1d). The FIRST borrow that touches `proof.py` adds `proof.py` to `_action_files()` + the
scanned-set test in the SAME commit (Round 2 #24 — the module that BUILDS and SIGNS the bundle
deserves the guard more than the diff that rides it; its imports are clean today, but "clean" must be
enforced through a 15-day sprint of edits to exactly this file). The nonce challenge-response
(§1b.3 #14) is a sacrificial LAST rung — only if P1–P3 land early.

### Phase 2 — Hash-chained transparency log: the spine the auditor walks — ~2.5d nominal (STRETCH #1; +0.25d transact_multi, +0.25d checkpoint chain)
The auditor graduates from "verify ONE bundle" to "walk the whole history, prove no LOGGED incident
was deleted, reordered, or back-dated." First deliverable (Round 2 #2, +0.25d): the existing
`transact(collection, doc_id, mutator)` is strictly SINGLE-document in both backends (the mutator
returns one doc; `_transact_firestore` writes exactly one ref) — so `state_store` gains a
**`transact_multi` primitive**: the mutator returns `[(collection, doc_id, new_doc), ...]`; the
Firestore side does `txn.set` on all refs inside one `@firestore.transactional` (read-before-write
order already satisfied — only `log_head` is read); the memory backend applies all writes under the
existing `_lock`; plus a crash-between-writes test proving atomicity. Without it, a container kill
between head-advance and entry-write (exactly the crash class Phase 6 documents as real) leaves a
permanent seq gap the auditor would attest as suppression — a FALSE tamper alarm, the worst failure
mode for the attestation surface. Then: new LLM-free `autosre/transparency.py` with `append(entry)`
that, inside ONE `transact_multi` on the `log_head` doc, reads `prev_entry_hash`, computes
`entry_hash = sha256(canonical({seq, prev_entry_hash, incident_id, service, bundle_digest, signature,
ts}))`, and writes BOTH the head pointer and an immutable `log_entries/{seq}` doc atomically.
Called from the ALREADY flag-gated + fail-open `_persist_proof` (state_machine.py:723-736), inheriting
its `try/except`. The auditor walks the chain, recomputes every link, confirms no seq gaps, confirms
each `bundle_digest` matches its committed signature, and **counter-signs the chain HEAD** (not
per-entry — per-entry would couple the heal path to a live auditor and break fail-open).
- *Design decision spec'd up front (folded from refutation):* `_persist_proof` fires at MULTIPLE
  terminal transitions for one incident (MITIGATED at 365/387/517 AND CLOSED at 628). Append **BOTH**
  links — most honest, most tamper-evident. Do NOT promise adjacency (Round 2 #3): the chain is one
  GLOBAL head across all services, so another service's heal can legitimately take the seq between an
  incident's mitigated entry and its hours-later closed entry — the auditor accepts two NON-adjacent
  seqs sharing an incident_id, and no test bakes "seq N / seq N+1" in.
- *Retry-safety (folded, re-keyed by Round 2 #3):* the Firestore `@transactional` path re-runs the
  mutator on contention. Compute `entry_hash` from the head read INSIDE the mutator (never captured
  outside). The idempotency key is **`(incident_id, terminal_status)`** — NOT incident_id alone,
  which would see the mitigated entry already committed and silently KEEP-drop the CLOSED link
  (state_machine.py:628 fires hours after 365/387/517). Store the last few committed
  `(incident, status)` pairs ON the `log_head` doc so the KEEP check is a pure in-transaction read
  (no query/index needed); if the pair already committed, return `KEEP` (idempotent, no dup seq).
  Chain-walk note: entries store `bundle_digest` + signature, not the bundle bytes, so the auditor's
  per-entry signature check uses the `Prehashed(SHA256)` verify variant of the kernel.
- *Honesty:* the chain ALONE proves internal consistency; a single writer controlling both chain and
  key only proves "consistent with itself." The genuine independent teeth are the AUDITOR's SEPARATE
  KMS identity counter-signing the head — and a HEAD counter-signature alone is STILL not enough
  (Round 2 #4, +0.25d): anyone with Firestore write access can rewrite the ENTIRE chain (or
  truncate-and-regrow it) between two auditor visits, and the rewritten chain walks clean. So
  checkpoints CHAIN: the auditor persists its last attested `(seq, entry_hash)` and embeds the
  previous checkpoint's hash in each new counter-signed checkpoint, FAILing attestation if the
  current chain does not contain exactly that `(seq, entry_hash)` below the new head — detects
  rewrite, truncation, and fork/split-view; the attestation carries BOTH prior and current checkpoint
  refs. Trust boundary of that memory, pinned down (Round 3 #5): the auditor's last-attested
  `(seq, entry_hash)` state lives in AUDITOR-OWNED durable storage — the auditor project's own
  bucket/Firestore, NEVER agent-owned Firestore, or the party being audited could reset its auditor's
  memory — and attestation FAILs if prior checkpoint state for a known service is MISSING (anti-reset:
  blank auditor memory for a previously-attested service is a tamper signal, not a fresh start).
  Stage claim, worded precisely: *"no LOGGED incident deleted, reordered, or back-dated since
  the first counter-signed checkpoint."*
- *Flag `AIRBAG_TRANSPARENCY_LOG` (default OFF) → no log doc, proof snapshot byte-identical.* Add
  `transparency.py` to `_action_files()` in the SAME commit that adds the module (mirror
  `revision_delta.py`).
- *Standards alignment (borrows §1b.3 #2/#8, +0.35d):* name the counter-signed head a **checkpoint**
  (RFC 9162 / Rekor's signed-tree-head concept — and with the Round-2 #4 prior-checkpoint containment
  check, the borrow is the consistency SEMANTICS, not just the name) and the auditor's two roles in
  CT terms (AUDITOR = append-only/consistency checks; MONITOR = content re-verification); the
  attestation gains `{chain_intact: bool, gaps: [], unlogged: []}` — `gaps` detects post-commit
  deletion; `unlogged` (Round 2 #22) cross-checks chain COVERAGE against the `GET /incidents` listing
  the auditor already polls, because the fail-open append + the `PROOF_SIGN` early-return mean an
  incident can honestly NEVER enter the chain (note, stated once so nobody flips one flag without the
  other: `AIRBAG_TRANSPARENCY_LOG` is effectively AND-ed with `PROOF_SIGN`). Scoped claim: the
  auditor attests that no APPENDED entry was deleted/reordered/back-dated AND names what never got
  logged — narrower than Sello's ideal no-suppression property, and honest about it; prefix every
  hash with a **domain-separation tag** (`airbag.log.entry.v1`, `airbag.checkpoint.v1`) so
  entry/checkpoint/attestation digests can never be cross-replayed (nono's technique).
- *Proof:* append 3 heals → chain verifies; tamper one entry's bundle → link break; delete seq=2 → gap;
  reorder → prev-hash mismatch; mitigate-then-close with an interleaved foreign-service heal → TWO
  valid NON-adjacent links for one incident_id, closed link never KEEP-dropped; mutator re-run → no
  dup/skipped seq; crash between head-advance and entry-write → atomic (no half-append, no false
  gap); whole-chain rewrite between audits → prior-checkpoint containment FAILs it; auditor checkpoint
  state wiped for a known service → attestation FAILs (the Round-3 #5 anti-reset case); append raises →
  heal unaffected AND the next attestation lists the incident in `unlogged`; flag-off → byte-identical.

### Phase 3 — Served trust anchor + remote proof tool: make the auditor a first-class A2A peer — ~1d nominal
Two small, read-only, LLM-free API-tier seams the auditor (and any third party) needs.
- **Served anchor** — `GET /.well-known/airbag-proof-pubkey.pem` serving the committed PEM bytes, plus
  a versioned key **registry** `{key_version_resource_name, role: heal-proof-signer|attestation-signer,
  algorithm, not_before, status:active|retired}` generated at setup time (extend `infra/kms-setup.sh`
  to emit `registry.json` alongside the PEM — NOT a live KMS call in the request path). Because every
  envelope embeds the full `cryptoKeyVersions/N`, a verifier maps an old heal's `signature.key` to the
  RIGHT (possibly retired) key — rotation-ready non-repudiation. **The `role` field is load-bearing
  (Round 2 #6):** the registry lists BOTH the agent and auditor keys, and both sign
  canonical-JSON→sha256→ECDSA-P256 — without a role-vs-artifact-type check, a legitimate auditor
  attestation re-wrapped as `{bundle: attestation, digest, signature: {key: <auditor key resource>}}`
  recomputes, resolves, and verifies: SIGNED-VERIFIED on a fabricated "heal". So EVERY verify surface
  that resolves keys by NAME (the auditor, the Explorer's auto-fetch tab) MUST match `role` against
  artifact type AND check the in-payload type tag (`bundle_version` / `attestation_version` from
  Phase 1.2). *Honesty:* the COMMITTED PEM pinned offline is authoritative; the served endpoint is
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
- *Standards alignment (borrow §1b.3 #11, +0.5d):* make `registry.json` a mini **TUF-style root** —
  `{version, expires, keys:[signer, auditor], threshold}` with each new version SIGNED BY the previous
  version's key, so rotation is VERIFIABLE, not just declared (answers "what happens when you rotate
  the KMS key?" with sigstore's own mechanism; 2-role only — full 4-role TUF is out of scope, say so).
- *Proof:* served PEM bytes == committed file byte-for-byte; `verify-proof.py` accepts a real bundle
  using the SERVED key (round-trip, registry can't drift); a retired `cryptoKeyVersions/N` resolves to
  its PEM via the registry; an auditor-key-signed payload presented as a heal proof → REJECTED by
  role/type mismatch (the Round-2 #6 confusion case); the 7→8 tool-count contract test.

### Phase 4 — Public offline Proof Explorer: verify-in-browser (zero network, judge-runnable) — ~2–2.5d nominal (STRETCH #2)
A single static HTML/JS page re-implementing `verify()`'s two checks in **WebCrypto** (`crypto.subtle.
digest` sha256; `crypto.subtle.verify` ECDSA P-256/SHA-256 over the SAME canonical bytes): drop a
`proof.json` + the pubkey, see SIGNED-VERIFIED / INTEGRITY-ONLY / FAIL, and (when fed a transparency-log
export) walk the chain. Renders the committed `docs/proof/*.json` fixtures verbatim so the finals video
shows an independent client re-verifying a real Cloud KMS heal live.
- **The load-bearing sub-deliverable is byte-exact verification — and escaping is only HALF the trap
  (Round 2 #5).** Python's `json.dumps` defaults to `ensure_ascii=True`; the committed signed
  fixture's 3 em-dashes make the canonical 2244 bytes with `\uXXXX` escapes (verified); a naive JS
  canonicalizer emits raw UTF-8 (2235 bytes) → **INTEGRITY FAIL on a valid heal**. But no escaping
  fix is sufficient: the fixture contains `"rate":0.0`, and `JSON.parse`→`JSON.stringify` destroys
  the token (`0.0` → `0`); Python and JS also diverge on exponent forms (`1e-07` vs `1e-7`, `1e+16`
  vs `10000000000000000`). So the Explorer does NOT re-implement the canonicalizer over parsed
  values — it verifies **literal bytes**: (i) PRIMARY input = the DSSE envelope's base64 payload
  bytes (the §1b.3 #4 borrow's exact de-risk), and (ii) for legacy envelopes, a
  number-token-PRESERVING JSON parser (keep raw number/string lexemes, sort keys, re-emit) — never
  parse→stringify. Second half of the trap: the fixture signature is 70-byte DER (`3044…`) but
  `crypto.subtle.verify` for ECDSA accepts only raw IEEE-P1363 `r||s` (64 bytes) — the ~20-line
  **DER→P1363 converter** is an explicit spec item, or every valid bundle shows SIGNATURE FAIL.
  This is why the estimate is 2–2.5d, not 1.5d.
- **Canonicalization hardening rides along (a real de-risk for the auditor too):** the
  `bundle_version` field itself is pulled FORWARD into Phase 1.2 (Round 2 #20 — the P1 borrows need
  it first); Phase 4 keeps the parity TEST: assert
  `proof.build's canonical == verify-proof's _canonical == the JS spec` for a fixture that DELIBERATELY
  contains non-ASCII, `0.0`, an exponent-form float, and a non-BMP character (extend the existing
  signed fixture — already 3 em-dashes + 20 floats — as the golden case), with the golden byte counts
  DERIVED by running the parity test against the fixture, never transcribed into prose (Round 2 #23).
  This survives a full descope of the UI: it de-risks digest drift across
  agent/verifier/auditor/explorer regardless.
- *Honesty guarded by TEST, not just UI copy:* assert the page can NEVER surface "verified correct" —
  only the three provenance/integrity states. Adversarial test: a tampered bundle and a wrong-key case
  both show FAIL (no always-green theater).
- *Invariant:* NONE to the action tier — a client-side static page (no Python, no LLM). The one agent
  code change this phase used to carry (`proof.py` `bundle_version`) moved to Phase 1.2, where
  `proof.py` also joins `_action_files()` (Round 2 #20/#24) — so Phase 4 ships zero agent-side code.
- *FLOOR of the page* is paste-proof.json + paste-pubkey over the committed fixtures; the `/.well-known`
  auto-fetch (Phase 3) and the chain-walk (Phase 2) are STRICTLY additive tabs that no-op cleanly if
  those phases don't ship — and the auto-fetch tab carries the SAME expected-signer pin + registry
  role/type check as the auditor (Round 2 #6): a registry-driven Explorer without role binding is
  exactly the surface the cross-key confusion attack targets.

### Phase 5 — Remote-MCP governance tiers (the second pre-agreed headline) — ~4d nominal
Replace the single all-or-nothing bearer token with per-caller identity, route remote heals through the
1.1 lease, and make remote `set_autonomy`/`approve` respect the trust ramp — the "other agents drove
Airbag AND we can attest exactly who did what" story that pairs with the auditor. **Conditioned (per
V5_VISION §4) on REUSING 1.1's correlation lease + the autonomy ramp — cite the seam, don't rebuild it.**
- **First deliverable: a token→caller resolution seam (Round 3 #7).** Today `BearerGate`
  (mcp_remote.py:98) validates ONE static token and the tool functions receive NO caller identity —
  there is nothing for a registry check to key on. Thread caller identity from the gate into the tool
  functions via request/session context BEFORE building any registry logic, and TDD the ordering
  end-to-end: an authz denial returns BEFORE `queue.enqueue_heal` is ever reached.
- **NEW pure module `autosre/mcp_governance.py`** (NOT logic inside `mcp_remote.py` — which imports the
  LLM via `apply_approval` and can never be AST-guarded). A caller registry doc in `state_store`
  (`mcp_callers`: `{token_hash → {max_level, tools}}`) checked in a second deterministic layer AFTER
  `BearerGate`; the effective ceiling is `min(caller_cap, service_autonomy_level)`. A "partner" token
  reads incidents/proof but `set_autonomy('L3')` requires an operator-tier caller.
- Route `airbag_trigger_heal` through `state_store.claim_service_heal` at ENQUEUE time so the remote
  response itself carries `{status:'attached', leader_incident_id}` (closes the same
  self-amplification class for A2A callers that 1.1 closed for alerts). For that ack to be truthful
  INDEPENDENT of `STORM_COALESCE`, settlement must be flag-independent too (Round 2 #19):
  `run_self_heal` today claims AND settles the per-service lease only under the flag (claim at
  state_machine.py:93-97, settle at :106-108/:87-88) — an enqueue-time claim with the flag off would
  NEVER be settled, leaving a 15-min corpse lease whose "attached" acks point at a FINISHED heal on a
  possibly still-broken service (a control-plane-minted mirror of §2(f)). So: settle `service_heals`
  whenever the doc names this incident_id as leader, REGARDLESS of `config.STORM_COALESCE` (the
  settle is already a no-op KEEP for non-leaders, state_store.py:259).
- Leave `autonomy.record_outcome`'s automatic demotion FULLY intact: a remotely-granted L3 still
  auto-demotes on a bad heal and cannot re-grant past its cap (the concrete defense of the DO-NOT
  "never weaken the ramp" line).
- Append a **durable actor audit trail** (`mcp_actions`: `{caller_id, tool, args-summary, incident_id,
  ts}` via `incidents.record`'s de-dup capped-append pattern) — mutable-store honesty (Round 3 #8):
  `incidents.record` is a merge-update on mutable Firestore state, so this must NEVER be pitched as
  "append-only". Two honest options, the rename preferred for the sub-floor: (a) call it what it is —
  a durable actor audit trail; (b) if Phase 2's transparency log ships, route actor events through it
  for genuinely tamper-evident actor history. Consider including `caller_id` in the
  signed bundle so the auditor attests provenance of the TRIGGER, not just the outcome.
- **Fail-CLOSED on the control plane** is the ONE deliberate fail-open exception (an authz denial
  rejects an over-privileged/unknown caller — a control-plane reject, NEVER an in-flight rollback).
- **Go/no-go + internal sub-floor (Round 2 #27 — P5 is the likeliest estimate-blower: 4d as scoped
  is realistically 5.5–7d with discovery-hiding in):** P5 starts ONLY if, at the §6 day-8 checkpoint,
  P1 is live-verified AND ≥5 dev days remain before the Phase-9 delivery block. P5's own internal
  floor = the token→caller resolution seam (Round 3 #7) + caller registry +
  `min(caller_cap, service_autonomy_level)` + lease routing + `403 insufficient_scope`. On the §4
  ladder P5 now sits ABOVE Phase 4 — cut earlier (Round 3 #11): new authz surface, likeliest
  estimate-blower. **Discovery-time `tools/list` hiding is demoted to a stretch**: FastMCP
  registers all 7 tools globally on the module-level `mcp` instance (mcp_remote.py:34-84) and serves
  `tools/list` inside the session manager, so per-caller filtering means intercepting JSON-RPC
  responses in the BearerGate ASGI layer — a research-y sub-task hiding inside the 4d; the
  graded-autonomy story survives without it. No P5 deck slide until its proof tests pass.
- *Scaling honesty:* the remote MCP requires `--max-instances 1` (FastMCP session_manager is
  in-process) — and deploy.sh currently CONTRADICTS itself (Round 3 #13): the comment at deploy.sh:139
  demands max-instances 1 when MCP is on, while the deploy command at :141 pins `--max-instances 3`,
  so deployed session/auth state can behave differently than anything tested. P5 must gate the deploy
  flags on MCP mode (`--max-instances 1` iff MCP is enabled) and smoke-test the DEPLOYED
  max-instances value, not the script's intent. Then ACCEPT single-instance and document it — do NOT
  hand-roll cross-instance session state (scope creep).
- *Flag `AIRBAG_MCP_TIERS` (default OFF) → BearerGate stays the sole gate, byte-identical to the 7/8-tool
  contract.* Add `mcp_governance.py` to `_action_files()` in the SAME commit (with a test asserting it
  is scanned, mirroring `test_causal_and_signals_are_in_the_scanned_set`).
- *Standards alignment (borrows §1b.3 #9/#10, +0.75d inside the budget) + the anti-refutation framing:*
  AWS MCP Gateway (open source, 2026-06) already ships per-caller tool ALLOWLISTING — so Phase 5 must
  never be pitched as "nobody restricts MCP tools"; the differentiator is **graded AUTONOMY with
  per-tier proof obligations**. Borrow what's now MCP-spec-native: (i) [STRETCH — see the go/no-go
  bullet; Round 2 #27] low-tier callers don't merely
  get DENIED act-capable tools — they never SEE them in `tools/list` (discovery-time hiding shrinks the
  prompt-injection surface); (ii) express tiers as OAuth-style scopes with `403 insufficient_scope`
  step-up semantics per the MCP authorization spec; (iii) make a tier a QUANTIFIED autonomy budget —
  `max_heals_per_hour`, `max_consecutive_autonomous` in the caller registry, enforced deterministically
  (explain in Sedai's observe/approve/auto vocabulary).
- *Proof:* a remotely-granted L3 caller auto-demotes to L2 on a bad heal and can't re-grant past its cap;
  `AIRBAG_MCP_TIERS=off` → the tool-set contract is byte-identical (BearerGate sole gate); a fail-CLOSED
  authz denial returns BEFORE any `queue.enqueue_heal` (an authz reject can never touch an in-flight
  rollback); remote trigger with `STORM_COALESCE=off` → heal completes → an immediate second remote
  trigger becomes a FRESH leader, never "attached" to the finished heal (Round 2 #19); a low-tier
  caller's `tools/list` omits act-capable tools entirely (stretch-proof, with discovery-hiding).

### Phase 6 — Leader-liveness heartbeat + fencing (live-finding a) — ~3d nominal
Shrink the crashed-leader dead-lease window from ~15 min to ~K missed heartbeats, LLM-free, behind the
existing `AIRBAG_STORM_COALESCE` flag (no new separately-flippable flag — it must not desync from
coalesce). **Prerequisite: Phase 7** (Round 2 #18 — its bounded sampling caps the worst-case
inter-emit gap the takeover math below depends on; the §4 ladder cuts P6 BEFORE P7 accordingly).
- **Liveness is a SEPARATE field, never a lease rewrite (the Round-2 #18 BLOCKER):** `lease_until =
  now + 900` stays written ONLY at claim/settle; add a `heartbeat_at` field refreshed by
  `refresh_service_heal(service, incident_id)` — a `transact()` that no-ops unless still-leader. The
  originally drafted design (re-aim `lease_until = now + AIRBAG_SERVICE_HEARTBEAT_S` ≈60s at each
  emit) would make 900 bound NOTHING after the first heartbeat — and the shipped code has inter-emit
  gaps far beyond 60s exactly where a leader is live-but-slow: ZERO emits between TRIAGED
  (state_machine.py:130) and ANALYZED (:140) while `sample_latency_windows` runs 4×8 serial GETs at
  timeout=10s (up to ~320s, and latency IS in the pinned live signal set), and the Gemini/ADK
  decision call is similarly unbounded. A live leader usurped mid-sampling means a second concurrent
  heal PLUS a second probe burst against a degraded service — the exact self-amplification class v5
  closed — and generation fencing would then reject the ORIGINAL leader's settle after it may already
  have shifted traffic. **Takeover therefore requires `(lease_until expired) OR (heartbeat_at stale
  by K misses, with K×TTL provably > the worst-case inter-emit gap — COMPUTED from
  SIGNAL_WINDOWS/BURN settings, not assumed)`.** Pulse from `_heal_body`'s `emit()` (fires at ~15
  stage transitions) AND from inside the samplers (a per-window `refresh` call in gcp.py — already in
  `_action_files()`, and `state_store` imports no LLM, so the invariant holds).
- **Keep `SERVICE_HEAL_LEASE_S=900` as the documented OUTER crash bound** — untouched by heartbeats,
  so the liveness signal can NEVER take over a live-but-slow leader that the K-miss math protects;
  the heartbeat is what makes a short takeover safe, the lease is what bounds a total crash.
- Add a monotonic **generation/epoch token** on the `service_heals` doc so a slow old leader that wakes
  after takeover is FENCED: `settle_service_heal` already rejects a stale settle by incident-id mismatch
  (state_store.py:259); the generation extends the fence to ANY late write (prevents two leaders both
  shifting traffic). **And the fence must gate the SIDE EFFECT, not just the settle (Round 3 #6):**
  today's mutators call Cloud Run directly (`rollback_traffic_to_revision` / `set_traffic_split`,
  backends/gcp.py:361/:372), so a stale worker could still shift traffic before its stale settle is
  ever rejected — add an `assert_current_service_heal(service, incident_id, generation)` check
  IMMEDIATELY BEFORE every Cloud Run traffic mutation in the heal path, and re-assert after long
  sampling windows, so the late write is refused AT the mutation site.
- **Self-rescuing follower:** in `_attach_to_leader`, when the leader's lease is within ε of expiry OR
  its `heartbeat_at` is stale by K misses (the SAME computed threshold as takeover — never a tighter
  one), do NOT `finish_heal` the follower — leave its per-incident lease reclaimable so
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
  as golden JSON (flag-off ~900s vs flag-on ~K heartbeats), CI-ratcheted exactly like the storm
  scorecard — turning live-finding (a) into the same anecdote→scorecard story v5 shipped.
- *Proof:* fake-clock leader emits heartbeats then stops → follower takes over after K missed
  heartbeats, not 900s; **a live leader blocked inside a FULL-LENGTH latency+burn sampling burst is
  NOT taken over — the follower attaches** (the Round-2 #18 case the original TDD list would never
  have caught); zombie old leader's settle AND a late traffic-shift
  write both rejected by generation mismatch — the traffic-shift rejection firing at the pre-mutation
  `assert_current_service_heal` guard, BEFORE the Cloud Run call (Round 3 #6); follower redelivery
  after a GOOD settle → still no-ops; flag-off byte-identical.

### Phase 7 — Bounded burn + latency sampling (live-finding b) — ~2d nominal
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
- **Scope now includes `sample_latency_windows` (Round 2 #18):** the deadline + early-exit levers
  apply to the latency sampler too (4×8 serial GETs at timeout=10s — up to ~320s, in the pinned live
  signal set, and the largest inter-emit gap Phase 6's K×TTL takeover math must bound). This is why
  Phase 7 is a stated PREREQUISITE of Phase 6 and sits below it on the §4 ladder.
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

### Phase 9 — Delivery (protected): deck, recording, rehearsal, run-of-show — 2–3d, NOT engineering
Hard code-freeze **2026-08-16**; the final ~3 calendar days (≈08-16 → 08-18) are a protected NON-dev
block (Round 2 #28 — the plan previously booked the entire delivery layer at +0d inside a "free"
borrow; v5 needed DECK_OUTLINE.md + VIDEO_SCRIPT.md as real work products, and v6 produces their
finals successors here, for a FIXED stage slot at Google Shibuya).
- Updated deck, rubric-mapped (つくる/まわす/とどける), moat pivoted per §8 Q6.
- A pre-recorded BACKUP take of the full money shot (a live heal takes minutes — log-ingestion lag +
  `DEMO_HEAL_DELAY_S` + the verify loop — variance a fixed slot cannot absorb).
- ≥2 timed rehearsals against the ACTUAL stage-slot length; the venue-network fallback decision (live
  vs recording as primary) made IN ADVANCE, not on stage.
- The ONE quantified metric (Round 2 #32), chosen at kickoff and instrumented from day 1:
  **"alert → independently-attested recovery: Xs"**, shown live on the auditor card — detection, heal,
  and attestation in a single number (v5's MTTR<3min is the loop; §1b.4 #4 says never lead with it).
- Language (Round 2 #32): decide JP vs JP+EN deck on DAY 1 and pre-translate the five load-bearing
  terms (issuer / receipt / counter-sign / tri-state / pinned signer) with the demo captions — a
  non-crypto judge must absorb them in seconds, and the eve of finals is too late to discover they
  don't translate.
This block is **rung 0 of the descope ladder — protected exactly like the auditor FLOOR**: under
schedule pressure, engineering phases get cut; rehearsal and recording time never does.

## 4. Non-negotiable FLOOR + descope ladder
**The FLOOR (non-negotiable):** the **Phase-1 Auditor Agent core** — a separate Cloud Run service that,
against a PINNED signer identity, re-verifies a live signed bundle end-to-end (integrity + provenance),
returns the honest tri-state, AND emits ONE counter-signed, offline-verifiable attestation, live-verified
on one real heal + committed to `docs/proof/`. Scope honesty (Round 3 #1): the FLOOR independently
verifies + counter-signs a PUBLISHED proof bundle — never claim it "proves exactly what the agent
did"; completeness/no-suppression claims belong to Phase 2's rung and degrade with it. This IS the
deferred v5 headline and the whole point of
finals week; v5 4.2 was built as its foundation.

**Descope ladder (cut in order, floor is untouchable):**
0. **NEVER cut: Phase 9 delivery days** (code freeze 2026-08-16) — protected exactly like the FLOOR;
   trading rehearsal/recording time for dev days is the pre-named one-human failure mode (Round 2 #28).
1. **Phase 8** bad-image fixture (honesty patch on ONE field, not a marquee claim).
2. **Phase 6** heartbeat + fencing (the 900s crash backstop already works, just coarse) — cut BEFORE
   Phase 7, whose bounded sampling is P6's stated prerequisite (Round 2 #18): shipping a heartbeat
   over unbounded sampler gaps is how a live leader gets usurped.
3. **Phase 7** bounded burn + latency sampling (the 900s TTL / opt-out posture already works; it's a
   perf + self-DoS hardening).
4. **Phase 5** MCP governance tiers (the #2 headline, cut FIRST among the headlines if the auditor needs
   the days — it's new authz surface AND the likeliest estimate-blower; its own internal sub-floor +
   day-8 go/no-go live in Phase 5). Swapped ABOVE the Explorer (Round 3 #11): Phase 4 supports judge
   SELF-verification of the marquee, so it outlives the new-surface bet.
5. **Phase 4** Proof Explorer (`verify-proof.py` CLI already lets a judge verify; the browser is a
   legibility multiplier) — BUT land the shared-canonicalization parity test regardless (it survives
   the UI's descope and de-risks the auditor; `bundle_version` itself already lives in Phase 1.2).
6. **Phase 3** served anchor + remote proof tool (the committed PEM + raw GET already work; this is A2A
   ergonomics).
7. **Phase 2** transparency log (cuts cleanly back to attest-single-bundle-only — still novel, still
   deterministic — WITHOUT touching the auditor core). **The SCITT vocabulary degrades WITH this rung
   (Round 2 #9/#11):** without the log, decks and stage copy say "SCITT-inspired counter-signed
   attestation (Receipt semantics arrive with the transparency log)" — never Receipt / Transparency
   Service / registration outcome.
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

## 6. Cadence (v5's, plus the Round-2/Round-3 corrections)
TDD per item; full suite + ruff (E9,F) green before every commit; bench ratchet green; the
architecture-invariant test run on every commit touching any action-tier module — **and the new
deterministic modules ADDED to the scanned set: `transparency.py` (Phase 2), `state_store.py` (Phase 6),
`mcp_governance.py` (Phase 5), and `proof.py` (with the FIRST Phase-1 borrow that touches it —
Round 2 #24) into `autosre/_action_files()`, plus the auditor's OWN allowlist-based
`test_auditor_invariant.py` (Phase 1.3) since it lives outside the `autosre/` glob — with the pytest
invocation in Makefile/CI growing to `pytest agent/tests auditor/tests` in the SAME commit that
creates `auditor/tests`, and the ONE-test-count rule counting the combined suite (Round 2 #21 — a
mirror that exists but never runs enforces nothing)**; adversarial review
(agy Gemini 3.1 Pro and/or a multi-agent refute-by-default workflow) BEFORE each substantial commit;
live-verify on real Cloud Run; demo baseline left HEALTHY; commit + push incrementally; ONE test count
across all docs; google-adk stays 1.x.

Effort budget, restated (Round 2 #25/#26/#29): P1–P8 ≈ 19.5–20d nominal (the money shot re-budgeted
to ~1d; Phase 2 +0.5d for `transact_multi` + the checkpoint chain), plus ≈2–3d of §1b.3
standards-alignment borrows folded into P1/P2/P3/P5 (each individually cuttable — naming/schema
moves, never load-bearing) ≈ **22d nominal**. The final ~3 calendar days (≈08-16 → 08-18) are
HARD-RESERVED for Phase 9 (video/deck/rehearsal — non-dev), so effective dev capacity is **~12 days:
≈1.8x over-provision — NOT "the same ratio v5 shipped with" (v5 was 2.0x: ≈9d planned against ~4–5
real, per V5_VISION §6); v6's margin is thinner, and the plan now says so.** Hence the **day-8
checkpoint** — itself fed by Phase 1's internal day-3 (offline verifier + pin green) and day-5
(counter-signed attestation live) gates (Round 3 #10): if Phase 1 is not live-verified by day 8,
enter the descope ladder IMMEDIATELY (P5/P4/P2 presumed cut, in the §4 ladder's post-swap cut
order — Round 3 #11) rather than when the days run out — the pre-agreed ladder protects the FLOOR (auditor
core) only if it is entered early enough. When cutting, drop borrows before phases — and never MERGE
a borrow before the Phase-1 floor is live-verified (Round 2 #26): the auditor without DSSE still
wins; DSSE without the auditor is decoration.

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
  escapes → a naive JS canonicalizer diverges (2235 bytes raw-UTF-8 — the draft's earlier byte figure
  was itself wrong, corrected by Round 2 #16/#23; float-token loss, `0.0`→`0`, diverges a
  parse→stringify port even further per Round 2 #5) and FAILs a valid heal. `bundle_version`
  (Phase 1.2) + byte-parity test is load-bearing before a third consumer.
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
   `AIRBAG_MCP_TIERS`/`AIRBAG_PROOF_DSSE`/`AIRBAG_SERVICE_HEARTBEAT_S` ON. **Default: ON after live
   verify; the recorded demo is captured with the auditor's money-shot but the agent side stays
   byte-identical.**
5. **Market recon: DONE 2026-07-04→08 on primary sources (§1b)** — what remains is a DELTA check at
   kickoff on the §1b.4 watch items, headlined by: AWS adding signatures to its "immutable journals",
   PagerDuty's Fully-Autonomous-Responder EA announcement, sigstore extending agent-IDENTITY signing
   to agent ACTIONS, and any AgentOps-shaped entry in this event's finalist list. **Default: 30-min
   delta sweep on day 1; the pre-armed counter-lines in §1b.4 are already written.**
6. **Update SUBMISSION.md §2 competitive table** with the GA facts and pivot the moat to
   provenance + self-safety BEFORE the finals video. **Default: yes, day 1.**
7. **Day-1 environment re-verification (Round 2 #30)** — the live env sits untouched 07-10→07-30, and
   `infra/alert-setup-v2.sh`'s video-gate opened right after 07-10 yet the cutover was owned by NO
   plan line. Checklist: (a) run the alert-policy v2 cutover (disable v1) and live-verify a probe
   burst does NOT fire the v2 alert — until then, any alert-triggered live moment contradicts *"the
   agent can't false-alarm itself"* on stage (the v1 policy was observed firing on Airbag's own probe
   5xx, 2026-07-02); (b) full smoke of `/demo/run` + `/demo/run-latency` end-to-end with a signed
   proof emitted; (c) token/key expiry audit — GitHub fine-grained token (fix-PR path), Gemini
   key/tier, demo/webhook/internal/MCP secrets, KMS key version still 1; (d) confirm rev 00041 still
   serving and the demo baseline "healthy is newest" invariant survived the idle gap. **Default: all
   four BEFORE any v6 commit.**
8. **Billing guardrail (Round 2 #31)** — the agent runs `--min-instances 1 --no-cpu-throttling`
   (always-allocated vCPU, roughly $50+/month) on a personal pay-as-you-go account with no GCP
   student credits (per `~/Documents/credits.md`, the ledger of record); a silent trial-credit
   exhaustion during the idle gap or finals week takes the live-verified deployment down with no
   warning. Day-0: verify the billing account is upgraded off trial (or record remaining credit +
   expiry in the ledger, per its update rule), create a GCP budget alert (e.g. ¥5k/¥10k thresholds
   emailing the billing owner), and decide + WRITE DOWN the idle-gap posture (keep-live vs
   `teardown.sh` + redeploy); the auditor itself is scale-to-zero between demo windows (Phase 1.3).
   **Default: budget alert + ledger entry on day 0; keep-live only if the credit math clears finals
   week.**
9. **Headline metric + deck language (Round 2 #32)** — **Default: metric = "alert →
   independently-attested recovery: Xs" on the auditor card, instrumented from day 1; JP-first deck
   with EN technical captions, the five load-bearing terms (issuer / receipt / counter-sign /
   tri-state / pinned signer) pre-translated at kickoff — Phase 9 consumes these, it never invents
   them.**
