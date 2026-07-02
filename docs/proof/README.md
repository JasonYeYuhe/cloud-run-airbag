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
