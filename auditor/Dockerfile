# Airbag Auditor — the SECOND, adversarially-independent Cloud Run service. Mirrors agent/Dockerfile.
#
# Build context is auditor/. The committed trust-anchor PEM(s) — scripts/airbag-proof-pubkey.pem (the
# agent's pinned signer) and scripts/auditor-pubkey.pem (once minted) — are staged into ./keys at
# deploy time and pointed at via AIRBAG_AGENT_PUBKEY_PEM / AIRBAG_AUDITOR_PUBKEY_PEM. Without the agent
# PEM the service still RUNS (provenance shows unchecked — fail-safe), but SIGNED-VERIFIED needs it.
# Deploy under a dedicated ZERO-role SA (infra/auditor-kms-setup.sh) with ONLY signerVerifier on the
# airbag-auditor key + outbound HTTPS to the agent's public URL — never any agent/Firestore access.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Cloud Run injects $PORT (default 8080).
ENV PORT=8080
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT}
