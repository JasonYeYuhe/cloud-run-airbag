# Airbag v6 — next-session plan (provenance-depth arc)

> Delta plan for the session that picks up **after the Phase-1 marquee FLOOR + Phase-4 Explorer are
> DONE, deployed, and live-verified**. This is NOT a restatement of `docs/V6_VISION.md` — read that
> end-to-end first (it is the plan of record; §3 has the full per-phase specs + verified seams). This
> file only captures (a) the current ground truth, (b) the video-gate posture, and (c) the next
> coherent chunk with seams re-verified against current code on 2026-07-10.
>
> **Reviewed by codex (gpt-5.5, xhigh, refute-by-default, 2026-07-10):** 4 findings folded — the
> flag-off/byte-identical overclaim (§1), the missing Phase-2 auditor read seam (§3.B.4), the DSSE
> step ordering (§3.A), and the `sign_digest` bounding wording (§2). Core analysis CONFIRMED sound:
> `bundle_version` does not invalidate stored proofs, the `(incident_id, terminal_status)` idempotency
> key is right, the 7→8 tool count holds, and the role/type cross-key defense is correct.

## 0. Ground truth (what SHIPPED this arc)

Live on `airbag-hack-260628` / `asia-northeast1`; agent rev **00041 untouched** throughout.

- **Phase 1 FLOOR** — the `airbag-auditor` Cloud Run service (own zero-role SA; own `airbag-auditor`
  KMS key, distinct from `airbag-proof`) polls the agent's public `GET /incidents/{id}/proof`,
  re-verifies each heal against a **pinned expected-signer** (`auditor/verify.py:attest`), returns the
  honest tri-state (SIGNED-VERIFIED / INTEGRITY-ONLY / DEGRADED / FAIL), and **counter-signs** its
  verdict with its own KMS identity, binding the fetch context. Live-verified on 25 real incidents;
  `docs/proof/auditor-attestation-inc-7d44556f.json` is the committed cross-attested proof.
- **Money-shot machinery** — `scripts/demo-tamper.sh` (integrity / signer-pin / restore),
  `docs/proof/rogue-signer-FAIL-demo.json`, `docs/DEMO_AUDITOR.md` run-of-show.
- **Phase 4 Explorer** — `docs/explorer/` (WebCrypto verify-in-browser; byte-exact canonicalizer;
  CI byte-parity gate `docs/explorer/parity-test.js`), served live from the auditor at `/explorer`.
- Commits `ba344e6 → d320598 → a6c6669 → 9754c52 → 81d6962 → 6f21165 → 5571d62`, all on `main`.
- Test surface: **64 auditor + 340 agent + 8 mcp + node byte-parity**. Auditor scaled to `min-instances 0`.

**Known flaky (do NOT chase as a regression):** the CI `firestore-emulator` job's
`test_state_store.py` leader-contention test can report "expected exactly ONE leader, got 0" under the
emulator's aggressive transaction lock-contention (all N claims abort). Identical agent code passed on
4 other runs. If Jason approves an agent-side edit, de-flake with `assert len(leaders) >= 1` or a
bounded retry; otherwise just re-run the job.

## 1. THE GATE (read before writing any agent code)

The remaining phases are **agent-side**. Two postures, do not conflate them:

- **DEV+TEST agent-side work is ALLOWED now** — you may write + TDD + review + **commit to `main`**
  freely (`main` is not deployed, so the LIVE demo is untouched). ⚠️ **Do NOT rely on "flag-off =
  byte-identical" (codex catch #1):** `bundle_version` (§3.A) is a PERMANENT schema field, so a flag-off
  *deploy* would NOT be proof-byte-identical — a re-healed proof gains `bundle_version` + a new
  signature. **The real guard is: NO agent redeploy and NO flag-flip until Jason confirms the video is
  recorded.** Already-STORED proofs (the committed fixtures + the demo incidents' snapshots) are
  unaffected — `GET /incidents/{id}/proof` serves `rec["proof"]` verbatim (app.py:102) before ever
  calling `build()`, so they keep verifying.
- **Live-verify + flag-flip on the agent waits on Jason confirming the submission video is recorded**
  (submission is 2026-07-10; nominal finals dev window 2026-07-30 → 08-19). Until then: do NOT
  redeploy `airbag-agent`, do NOT change its env flags, do NOT touch the target baseline, do NOT cut
  over alert-policy v2.
- **Auditor-side work is safe at any time** (separate service, read-only). Phase 2's chain-walk +
  checkpoint logic lives in `auditor/` and may be deployed whenever.

If Jason has already confirmed the video: proceed straight through dev+test → live-verify per the v5
cadence. If not: build everything dev+test, commit, and leave a "flip after video" checklist.

## 2. Verified seams (re-checked against current code 2026-07-10 — anchor on NAMES)

- `agent/autosre/proof.py`: `build(rec)` (canonical `json.dumps(sort_keys, separators=(",",":"),
  default=str)`, `ensure_ascii` **defaults True**), `sign_digest(digest)` (KMS asymmetricSign,
  fail-open; **one UNBOUNDED token refresh (`creds.refresh`) + a per-op-bounded KMS POST
  (`httpx.post(timeout=15.0)`) with NO total wall-clock deadline** — codex #4), `build_signed(rec)`.
  **No `bundle_version`, no DSSE.**
- `agent/autosre/state_machine.py:_persist_proof(incident_id)` (line ~723): fires at MITIGATED
  (3 sites) + CLOSED (1 site); `if not config.PROOF_SIGN: return`; wraps `incidents.record(id,
  {"proof": proof.build_signed(rec)})` in try/except (fail-open). **Phase 2 `append()` hooks HERE.**
- `agent/autosre/state_store.py:transact(collection, doc_id, mutator)`: strictly **single-document**
  in both backends. **`transact_multi` is genuinely new** (Phase 2's first deliverable).
- `agent/autosre/config.py`: only `PROOF_SIGN` + `STORM_COALESCE` among v6 flags exist.
  `AIRBAG_PROOF_DSSE`, `AIRBAG_TRANSPARENCY_LOG`, `AIRBAG_PUBKEY_ROUTE`, `AIRBAG_MCP_TIERS` — **absent**.
- `agent/autosre/mcp_remote.py`: exactly **7 `@mcp.tool()`** (airbag_incidents, airbag_incident,
  airbag_autonomy, airbag_memory, airbag_trigger_heal, airbag_approve, airbag_set_autonomy); `from
  .state_machine import apply_approval` (**LLM-tainted → can never be AST-scanned**); `BearerGate` +
  `gated_mcp_app`. **No `airbag_incident_proof`.**
- `mcp-server/airbag_mcp.py:airbag_incident_proof` (line ~89) — the Phase-3 port SOURCE.
- `agent/app.py`: **no** `/.well-known`, `/pubkey`, `/jwks`, or registry route.
- `agent/tests/test_architecture_invariant.py:_action_files()` globs `backends/*` + `signals/*` +
  {tools, causal, memory, reversibility, revision_delta}. **`proof.py` is NOT yet scanned** — the
  first borrow that touches it MUST add it (Round 2 #24), same commit.
- Auditor invariant: `auditor/tests/test_auditor_invariant.py` has the verify.py allowlist +
  the service denylist + the repo-level parity test. New auditor modules join the denylist glob
  automatically (it globs `auditor/*.py` except verify.py).

## 3. The next chunk — in build order (each: TDD, adversarial review, flag default-OFF, commit)

### A. DSSE + in-toto borrow (Phase-1 standards alignment) — ~1.5–2d
Unblocked: the floor is live-verified (Round 2 #26 sequencing satisfied). See V6_VISION §3 "Standards
alignment riding Phase 1" for the full spec. Order within this item:

**Ordering reworked per codex #3 — bound first, gate before emit, DSSE emit LAST:**

1. **Bound BOTH `sign_digest` network calls FIRST** (Round 1 #6 — MANDATORY before DSSE doubles the
   terminal-stamp KMS exposure): the UNBOUNDED `creds.refresh` AND add a total wall-clock deadline
   around the (already `timeout=15`) `httpx.post`. Reuse the auditor's proven pattern
   (`auditor/attestation.py:_bounded` + explicit `httpx.Timeout`). Its own small commit; fail-open
   preserved. **Invariant:** this is the FIRST commit touching `proof.py` → add `proof.py` to
   `_action_files()` + the scanned-set test in the SAME commit (Round 2 #24). proof.py imports are
   clean today (config, report, httpx, google.auth) — keep them clean.
2. **`bundle_version`** (proof.py) — a permanent, self-describing in-band type tag
   (`"bundle_version": "airbag.heal/v1"` or similar). *Design decision to nail up front (Round 2 #20):*
   it is **NOT flag-gated and NOT keyed-on-presence** — a schema field on every built bundle, so NEW
   bundles differ from OLD. codex CONFIRMED this is safe for the FLOOR: (i) committed `docs/proof/*` +
   the demo incidents' stored snapshots keep verifying (`/proof` returns `rec["proof"]` verbatim before
   calling `build()`, app.py:102); (ii) canonicalizers are generic, so bundles with/without it both
   verify; (iii) the auditor's cache keys include the raw-bytes digest, so it re-audits rather than
   falsely reusing. The only real effect: a re-healed proof (or an old incident with no stored proof
   served on the fallback) gains `bundle_version` → new bytes → this is why "no deploy before video"
   (§1) is the guard, NOT "flag-off byte-identical". Extend `docs/explorer/parity-test.js` +
   `test_proof.py` with a fresh golden bundle CONTAINING `bundle_version` (re-derive byte counts);
   confirm the Explorer's embedded fixtures still verify.
3. **cosign golden fixture + CI gate BEFORE the emit path** (Round 3 #9 HARD GATE): stand up a
   `cosign`-in-CI job with a golden fixture signed via the SAME KMS path passing
   `cosign verify-blob-attestation --key … --type airbag.dev/heal-attestation/v1 …`. If it can't go
   green, **cut DSSE** (it's a borrow, not a phase; the auditor without DSSE still wins). Do this
   BEFORE writing the emit path so you never build on an un-gated beat.
4. **`trigger_evidence_digest`** (sha256 of the triggering alert evidence) — lands AFTER
   `bundle_version` (evidence is always present on the alert path, so presence-keying alone would break
   prior bytes) — plus the **SLSA-style split** in the predicate: `externalParameters` (Gemini-suggested)
   vs `internalParameters` (FSM-resolved).
5. **DSSE envelope emit LAST**, behind `AIRBAG_PROOF_DSSE` (default OFF), inside `_persist_proof`'s
   existing fail-open try/except, emitted BESIDE the legacy envelope (never inside it). Payload IS an
   in-toto Statement (`predicateType airbag.dev/heal-attestation/v1`, `subject = {name: incident_id,
   digest.sha256 = sha256(canonical bundle bytes)}`); DSSE sig is a **second KMS sign over
   `sha256(PAE(payloadType, payload))`**. **Test (codex #3):** with `AIRBAG_PROOF_DSSE` OFF,
   `_persist_proof` leaves `rec["proof"]` byte-identical to pre-DSSE apart from the intentional
   `bundle_version` schema field — assert the legacy envelope is untouched.
6. **SPIFFE-style issuer URIs** (`spiffe://airbag.dev/{agent,auditor}`) as identity strings (+0.1d).

### B. Phase 2 — hash-chained transparency log — ~2.5d (STRETCH #1)
Full spec: V6_VISION §3 Phase 2. Order:

1. **`transact_multi` in state_store FIRST** (+0.25d) — the mutator returns `[(coll, id, doc), …]`;
   Firestore side does `txn.set` on all refs inside one `@firestore.transactional`; memory backend
   under the existing `_lock`. **Crash-between-writes test** proving atomicity (a container kill
   between head-advance and entry-write must not forge a "tamper" gap). Add `state_store.py` to
   `_action_files()` in this commit.
2. **`autosre/transparency.py`** (new, LLM-free, joins `_action_files()` SAME commit): `append(entry)`
   inside ONE `transact_multi` on `log_head` — reads `prev_entry_hash`, computes
   `entry_hash = sha256(domain-tag + canonical({seq, prev_entry_hash, incident_id, service,
   bundle_digest, signature, ts}))`, writes head pointer + immutable `log_entries/{seq}` atomically.
   Domain-separation tags (`airbag.log.entry.v1`, `airbag.checkpoint.v1`).
3. **Wire from `_persist_proof`** (already flag-gated + fail-open). Flag `AIRBAG_TRANSPARENCY_LOG`
   default OFF (effectively AND-ed with `PROOF_SIGN`). Idempotency key **`(incident_id,
   terminal_status)`** — NOT incident_id alone (MITIGATED + CLOSED are two links); store last-committed
   pairs on `log_head` for a pure in-transaction KEEP check. Do NOT promise adjacency (one global head).
4. **Agent READ SEAM — FIRST, before any auditor chain-walk (codex #2, a missing prerequisite):** the
   auditor is outbound-HTTPS-only with **no agent/Firestore access** (`auditor/Dockerfile`), and the
   log (`log_head` / `log_entries`) lives in agent Firestore with **no public route** (app.py exposes
   only `/incidents` + `/incidents/{id}/proof`). So Phase 2 MUST add read-only agent routes —
   `GET /transparency/head` + `GET /transparency/log?from=&to=` (or one `/transparency/export`) —
   serving the head pointer + entries. Additive → byte-identical demo → agent-side. Do NOT grant the
   auditor agent-Firestore access (that voids the independence story). This route is the seam the
   auditor walks; build it in the same agent-side arc as the log.
5. **Auditor side** (over that read seam): walk the chain, recompute links, check append-only
   consistency + coverage cross-check (`unlogged:[ids]` vs the `GET /incidents` listing); counter-sign
   the HEAD as a **checkpoint**. Checkpoints **CHAIN** (each embeds prior `(seq, entry_hash)`;
   containment check FAILs on whole-chain rewrite/truncation). Checkpoint state lives in
   **AUDITOR-OWNED durable storage** (its own bucket/Firestore, never agent-owned); a missing prior
   checkpoint for a known service FAILs attestation (Round 3 #5 anti-reset).
6. Attestation gains `{chain_intact, gaps:[], unlogged:[]}`. SCITT vocabulary graduates to full
   Receipt/Transparency-Service terms once this lands (Round 2 #11).

### C. Phase 3 — served anchor + remote proof tool — ~1d (+0.5d TUF)
Full spec: V6_VISION §3 Phase 3. Two read-only API-tier seams:

1. **`GET /.well-known/airbag-proof-pubkey.pem`** serving the committed PEM bytes (the committed file
   stays the trust root) + `registry.json` (mini TUF: `{version, expires, keys:[{resource, role, algo,
   not_before, status}], threshold}`; new version signed by previous key). Generated at setup time
   (extend `infra/kms-setup.sh`), NOT a live KMS call in the request path. **`role` is load-bearing
   (Round 2 #6):** every verify surface that resolves keys by NAME must match `role` (heal-proof vs
   attestation) against artifact type AND the in-payload type tag. New route → additive → byte-identical
   demo; keep `AIRBAG_PUBKEY_ROUTE` for cadence-consistency.
2. **Port `airbag_incident_proof`** from `mcp-server/airbag_mcp.py` to `mcp_remote.py` (7→8 tools).
   DELIBERATELY flip `test_mcp_remote.py`'s locked 7-tool count → 8. Read-only, one tool.
3. This unblocks the auditor pulling proofs over A2A/MCP; also lets the auditor's `signed_not_before`
   (its DEGRADED signal) come from the registry's `not_before` instead of a hardcoded config.

## 4. Sequencing, descope, cadence

- **Descope ladder (V6_VISION §4, unchanged):** if time binds, cut borrows before phases; drop in
  order P8 → P6 → P7 → P5 → P3 → P2, and DSSE degrades the SCITT vocabulary with P2's rung. The
  Phase-1 floor (done) is never cut.
- **After this arc:** V6_VISION build order continues to **Phase 5** (MCP governance tiers — go/no-go
  at the day-8 checkpoint, likeliest estimate-blower), **Phase 6** (heartbeat+fencing, prereq Phase 7),
  **Phase 7** (bounded burn), **Phase 8** (bad-image fixture, needs a deploy). All agent-side.
- **Cadence (unchanged):** TDD each item; full suite + `ruff --select E9,F` before every commit;
  architecture-invariant test on every commit touching an action-tier module or the new modules; bench
  ratchet where relevant; **adversarial refute-by-default review (multi-agent Workflow; or `codex exec`
  piping the prompt via STDIN — it hangs on long argv) BEFORE each substantial commit, APPLY confirmed
  findings**; commit + push to `main` incrementally; ONE test count across docs; google-adk stays 1.x.
- **Non-redundancy:** never build a check a deterministic gate already enforces. Respect V6_VISION §5's
  cut list (no LLM-judge auditor, no DID/VC framework, no cross-instance MCP sessions, no full 4-role
  TUF).

## 5. Open design decisions for the session to resolve up front

1. **`bundle_version` schema evolution** — confirm the "not flag-gated, snapshots unaffected, parity
   fixtures extended" analysis in §3.A.1 holds against `test_proof.py` + the Explorer embedded
   fixtures. This is the load-bearing first decision; get it reviewed before writing.
2. **DSSE cosign-in-CI** — stand up the `cosign` CI job + golden fixture BEFORE building the emit path;
   if it can't go green, cut DSSE early (per the hard gate).
3. **Auditor checkpoint storage** — provision the auditor's own durable store (a second bucket or the
   auditor project's Firestore) for checkpoint state; NEVER agent-owned Firestore.
4. **Video gate** — confirm status with Jason on day 1; it decides whether live-verify/flip happen in
   this session or are deferred to a checklist.
