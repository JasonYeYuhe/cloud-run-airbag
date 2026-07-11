// Byte-parity + verify test for the Proof Explorer core (v6 Phase 4). Run: node docs/explorer/parity-test.js
// Proves the JS canonicalizer emits byte-identical output to Python's json.dumps(sort_keys, (",",":"),
// ensure_ascii=True) for EVERY committed fixture, and that the full attest() reproduces the auditor's
// verdicts (SIGNED-VERIFIED / INTEGRITY-ONLY / FAIL) against the real committed keys. This is the
// canonical-drift guard V6_VISION requires REGARDLESS of the UI.
'use strict';
const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const V = require('./verify-core.js');

const REPO = path.resolve(__dirname, '..', '..');
// Reference canonical is computed with stdlib json+hashlib only (no deps), so any python3 works.
const _venvPy = path.join(REPO, '.venv-demo', 'bin', 'python');
const PY = process.env.PYTHON || (fs.existsSync(_venvPy) ? _venvPy : 'python3');
const AGENT_KEY = 'projects/airbag-hack-260628/locations/asia-northeast1/keyRings/airbag/' +
  'cryptoKeys/airbag-proof/cryptoKeyVersions/1';
const AUDITOR_KEY = 'projects/airbag-hack-260628/locations/asia-northeast1/keyRings/airbag/' +
  'cryptoKeys/airbag-auditor/cryptoKeyVersions/1';

const read = (rel) => fs.readFileSync(path.join(REPO, rel), 'utf8');
const jsSha = (str) => crypto.createHash('sha256').update(Buffer.from(str, 'utf8')).digest('hex');

// Python's canonical bundle -> {len, sha} (the reference the digest was computed over).
function pyCanonical(rel) {
  const code = 'import json,hashlib,sys\n' +
    'd=json.load(open(sys.argv[1]))\n' +
    'c=json.dumps(d["bundle"],sort_keys=True,separators=(",",":"),default=str)\n' +
    'b=c.encode("utf-8")\n' +
    'print(len(b));print(hashlib.sha256(b).hexdigest())';
  const out = execFileSync(PY, ['-c', code, path.join(REPO, rel)], { encoding: 'utf8' }).trim().split('\n');
  return { len: parseInt(out[0], 10), sha: out[1].trim() };
}

const FIXTURES = [
  'docs/proof/live-kms-signed-latency-heal.json',
  'docs/proof/rogue-signer-FAIL-demo.json',
  'docs/proof/auditor-attestation-inc-7d44556f.json',
  'docs/proof/live-5xx-heal-recency.json',
  'docs/proof/live-latency-heal-ledger.json',
];

let failures = 0;
console.log('--- byte-parity (JS canonical == Python canonical) ---');
for (const f of FIXTURES) {
  const jsCanon = V.canonicalBundleFromText(read(f));
  const jsLen = Buffer.byteLength(jsCanon, 'utf8');
  const py = pyCanonical(f);
  const ok = jsLen === py.len && jsSha(jsCanon) === py.sha;
  console.log(`${ok ? 'OK  ' : 'FAIL'} ${f}  js=${jsLen}B py=${py.len}B  digest ${ok ? 'match' : 'MISMATCH'}`);
  if (!ok) failures++;
}

(async () => {
  console.log('--- verify (attest reproduces the auditor verdicts) ---');
  const agentPem = read('scripts/airbag-proof-pubkey.pem');
  const auditorPem = read('scripts/auditor-pubkey.pem');

  const cases = [
    ['real heal -> SIGNED-VERIFIED', 'docs/proof/live-kms-signed-latency-heal.json', agentPem, AGENT_KEY, V.SIGNED_VERIFIED],
    ['rogue signer -> FAIL', 'docs/proof/rogue-signer-FAIL-demo.json', agentPem, AGENT_KEY, V.FAIL],
    ['auditor attestation -> SIGNED-VERIFIED', 'docs/proof/auditor-attestation-inc-7d44556f.json', auditorPem, AUDITOR_KEY, V.SIGNED_VERIFIED],
    ['unsigned heal -> INTEGRITY-ONLY', 'docs/proof/live-5xx-heal-recency.json', agentPem, AGENT_KEY, V.INTEGRITY_ONLY],
    ['wrong pinned key version -> FAIL', 'docs/proof/live-kms-signed-latency-heal.json', agentPem, AGENT_KEY.replace('/1', '/2'), V.FAIL],
  ];
  for (const [name, f, pem, key, expected] of cases) {
    const r = await V.attest(read(f), pem, key);
    const ok = r.tri_state === expected;
    console.log(`${ok ? 'OK  ' : 'FAIL'} ${name}  (got ${r.tri_state})`);
    if (!ok) { failures++; console.log('     messages: ' + r.messages.join(' | ')); }
  }

  // the rogue is integrity-OK but signature-FAIL (a valid-looking impersonation the pin/crypto rejects)
  const rogue = await V.attest(read('docs/proof/rogue-signer-FAIL-demo.json'), agentPem, AGENT_KEY);
  assert.strictEqual(rogue.integrity_ok, true);
  assert.strictEqual(rogue.signature_ok, false);

  console.log('--- parity edge cases (confirmed review fixes) ---');
  // [1] non-BMP key ordering: JS code-point sort must equal Python sort_keys (surrogate-pair key)
  const synthetic = '{"bundle":{"\u{1F600}":2,"ﬁ":1,"z":3}}';
  const pyCode = 'import json,hashlib,sys\n' +
    'b=json.loads(sys.stdin.read())["bundle"]\n' +
    'c=json.dumps(b,sort_keys=True,separators=(",",":"),default=str)\n' +
    'print(hashlib.sha256(c.encode("utf-8")).hexdigest())';
  const pySha = execFileSync(PY, ['-c', pyCode], { input: synthetic, encoding: 'utf8' }).trim();
  const nbmpOk = jsSha(V.canonicalBundleFromText(synthetic)) === pySha;
  console.log(`${nbmpOk ? 'OK  ' : 'FAIL'} non-BMP key ordering matches Python sort_keys`);
  if (!nbmpOk) failures++;

  // [2] strict DER: a non-canonically-encoded (trailing-byte) signature must FAIL, matching OpenSSL/Python
  const realJson = read('docs/proof/live-kms-signed-latency-heal.json');
  const realSig = JSON.parse(realJson).signature.signature;
  const mangled = Buffer.concat([Buffer.from(realSig, 'base64'), Buffer.from([0x00])]).toString('base64');
  const mangledJson = realJson.replace(realSig, mangled);
  const mr = await V.attest(mangledJson, agentPem, AGENT_KEY);
  const derOk = mr.tri_state === V.FAIL && mr.signature_ok === false && mr.integrity_ok === true;
  console.log(`${derOk ? 'OK  ' : 'FAIL'} strict DER rejects a trailing-byte signature (got ${mr.tri_state})`);
  if (!derOk) failures++;

  // [3] v6 bundle_version: a NEW bundle carrying the permanent type tag must canonicalize byte-identically
  // in JS and Python ALONGSIDE the known traps (em-dash -> \uXXXX escape, "rate":0.0 lexeme preserved).
  // Byte length is DERIVED at runtime here, never transcribed into prose (Round 2 #23).
  const bvBundle = '{"bundle":{"bundle_version":"airbag.heal/v1","em":"—","rate":0.0,"z":3}}';
  const bvJsCanon = V.canonicalBundleFromText(bvBundle);
  const bvPySha = execFileSync(PY, ['-c', pyCode], { input: bvBundle, encoding: 'utf8' }).trim();
  const bvOk = jsSha(bvJsCanon) === bvPySha && bvJsCanon.includes('"bundle_version":"airbag.heal/v1"');
  console.log(`${bvOk ? 'OK  ' : 'FAIL'} bundle_version canonicalizes byte-identically (js=${Buffer.byteLength(bvJsCanon, 'utf8')}B, tag in-band)`);
  if (!bvOk) failures++;

  console.log(failures ? `\nFAILED (${failures})` : '\nALL PASS');
  process.exit(failures ? 1 : 0);
})();
