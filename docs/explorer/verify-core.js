// Airbag Proof Explorer — verification core (v6 Phase 4). ZERO network, ZERO dependencies.
// Runs in the browser (WebCrypto) AND under Node (for the byte-parity test). UMD-ish export at the end.
//
// The load-bearing part is BYTE-EXACT canonicalization matching Python's
//   json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str)   # ensure_ascii=True
// A naive JSON.parse -> JSON.stringify FAILS on a VALID heal, two independent ways (V6_VISION §0/§7):
//   (1) ensure_ascii: the bundle's em-dashes canonicalise to — escapes (2244 bytes), but a JS
//       re-serialiser emits raw UTF-8 (2235 bytes) -> INTEGRITY FAIL.
//   (2) number tokens: "rate":0.0 -> JSON.parse -> 0 -> JSON.stringify -> "0", and 1e-07 vs 1e-7,
//       1e+16 vs 10000000000000000 diverge. Python float repr is not reproducible via parse->stringify.
// So we DO NOT parse numbers into JS Numbers: a custom tokenizer keeps the raw number LEXEME verbatim,
// decodes string values, sorts object keys (by Unicode code point), and re-emits with Python's
// ensure_ascii escaping rules.
//
// DESIGN NOTE (Round-2 #5): lexeme preservation is byte-exact for any proof PRODUCED BY Python
// json.dumps (every real Airbag heal/attestation), because the file's number tokens already are
// Python's float/int repr. A HAND-CRAFTED number in a non-Python-repr form (e.g. `-0`, which Python
// normalises to `0`) canonicalises verbatim and would recompute a different digest — but that only ever
// yields a conservative INTEGRITY-FAIL (a false REJECT), never a false ACCEPT: the claimed digest was
// computed by Python, so a divergent recompute can only mismatch, never spuriously match a tampered
// bundle. Reproducing Python's float repr exactly is intentionally out of scope (it is not expressible
// via JS Number.toString: 1e+16 vs 10000000000000000, 1e-07 vs 1e-7).

'use strict';

// A number token kept as its raw source lexeme (never converted to a JS Number).
class RawNum {
  constructor(lex) { this.lex = lex; }
}

// Parse JSON text into a tree of {plain objects, arrays, JS strings (decoded), RawNum, true/false/null}.
function parseJson(text) {
  let i = 0;
  const n = text.length;
  const isWs = (c) => c === ' ' || c === '\t' || c === '\n' || c === '\r';
  function ws() { while (i < n && isWs(text[i])) i++; }
  function fail(m) { throw new Error(`JSON parse error at ${i}: ${m}`); }

  function value() {
    ws();
    const c = text[i];
    if (c === '{') return obj();
    if (c === '[') return arr();
    if (c === '"') return str();
    if (c === 't') { expect('true'); return true; }
    if (c === 'f') { expect('false'); return false; }
    if (c === 'n') { expect('null'); return null; }
    if (c === '-' || (c >= '0' && c <= '9')) return num();
    fail(`unexpected '${c}'`);
  }
  function expect(lit) {
    if (text.substr(i, lit.length) !== lit) fail(`expected '${lit}'`);
    i += lit.length;
  }
  function obj() {
    i++; ws(); const o = {};
    if (text[i] === '}') { i++; return o; }
    for (;;) {
      ws();
      if (text[i] !== '"') fail('expected string key');
      const k = str(); ws();
      if (text[i] !== ':') fail("expected ':'");
      i++; o[k] = value(); ws();
      if (text[i] === ',') { i++; continue; }
      if (text[i] === '}') { i++; return o; }
      fail("expected ',' or '}'");
    }
  }
  function arr() {
    i++; ws(); const a = [];
    if (text[i] === ']') { i++; return a; }
    for (;;) {
      a.push(value()); ws();
      if (text[i] === ',') { i++; continue; }
      if (text[i] === ']') { i++; return a; }
      fail("expected ',' or ']'");
    }
  }
  function str() {
    i++; let s = '';
    for (;;) {
      const ch = text[i];
      if (ch === undefined) fail('unterminated string');
      if (ch === '"') { i++; return s; }
      if (ch === '\\') {
        const e = text[i + 1];
        if (e === 'u') { s += String.fromCharCode(parseInt(text.substr(i + 2, 4), 16)); i += 6; continue; }
        const map = { '"': '"', '\\': '\\', '/': '/', b: '\b', f: '\f', n: '\n', r: '\r', t: '\t' };
        if (!(e in map)) fail(`bad escape \\${e}`);
        s += map[e]; i += 2; continue;
      }
      s += ch; i++;
    }
  }
  function num() {
    const start = i;
    if (text[i] === '-') i++;
    while (i < n && '0123456789'.includes(text[i])) i++;
    if (text[i] === '.') { i++; while (i < n && '0123456789'.includes(text[i])) i++; }
    if (text[i] === 'e' || text[i] === 'E') {
      i++; if (text[i] === '+' || text[i] === '-') i++;
      while (i < n && '0123456789'.includes(text[i])) i++;
    }
    return new RawNum(text.slice(start, i));
  }

  const v = value(); ws();
  if (i !== n) fail('trailing content');
  return v;
}

// Escape a string EXACTLY as Python json.dumps(ensure_ascii=True): short escapes for the standard
// control chars, \uXXXX (lowercase, 4 hex) for everything else < 0x20 or > 0x7e (matches Python's
// ESCAPE_ASCII = /[\\"]|[^ -~]/). Non-BMP chars are already surrogate pairs in a JS string, so each
// code unit escapes to \uXXXX — matching Python's surrogate-pair output.
function escapeString(s) {
  let out = '"';
  for (let k = 0; k < s.length; k++) {
    const c = s.charCodeAt(k);
    if (c === 0x22) out += '\\"';
    else if (c === 0x5c) out += '\\\\';
    else if (c === 0x08) out += '\\b';
    else if (c === 0x0c) out += '\\f';
    else if (c === 0x0a) out += '\\n';
    else if (c === 0x0d) out += '\\r';
    else if (c === 0x09) out += '\\t';
    else if (c < 0x20 || c > 0x7e) out += '\\u' + c.toString(16).padStart(4, '0');
    else out += s[k];
  }
  return out + '"';
}

// Compare two strings by Unicode code point (matches Python string ordering / sort_keys=True).
function cmpCodePoint(a, b) {
  const ca = Array.from(a); const cb = Array.from(b); // Array.from iterates by code point
  const n = Math.min(ca.length, cb.length);
  for (let k = 0; k < n; k++) {
    const d = ca[k].codePointAt(0) - cb[k].codePointAt(0);
    if (d !== 0) return d;
  }
  return ca.length - cb.length;
}

// Serialise a parsed tree to the canonical bytes-string (sorted keys, minimal separators, raw numbers).
function canonicalize(node) {
  if (node === null) return 'null';
  if (node === true) return 'true';
  if (node === false) return 'false';
  if (node instanceof RawNum) return node.lex;
  if (typeof node === 'string') return escapeString(node);
  if (Array.isArray(node)) return '[' + node.map(canonicalize).join(',') + ']';
  // object: sort keys by Unicode CODE POINT (Python sort_keys=True). JS's default Array.sort compares
  // by UTF-16 code UNIT, which diverges from Python for non-BMP (astral) keys — a surrogate pair sorts
  // before a BMP key >= U+E000. Real Airbag bundle keys are ASCII field names, but this keeps the
  // byte-parity invariant true for ANY pasted proof.
  const keys = Object.keys(node).sort(cmpCodePoint);
  return '{' + keys.map((k) => escapeString(k) + ':' + canonicalize(node[k])).join(',') + '}';
}

// Canonical bytes of a bundle given the RAW proof text (so number lexemes survive). Returns a string
// whose UTF-8 encoding is the exact bytes Python hashed.
function canonicalBundleFromText(proofText) {
  const proof = parseJson(proofText);
  if (!proof || typeof proof !== 'object' || Array.isArray(proof)) throw new Error('proof is not a JSON object');
  const bundle = proof.bundle;
  if (bundle === undefined) throw new Error('proof has no "bundle" field');
  return canonicalize(bundle);
}

// --- crypto helpers (browser WebCrypto). Node maps globalThis.crypto.subtle since v20. -------------
function b64ToBytes(b64) {
  if (typeof atob === 'function') {
    const bin = atob(b64); const out = new Uint8Array(bin.length);
    for (let k = 0; k < bin.length; k++) out[k] = bin.charCodeAt(k);
    return out;
  }
  return new Uint8Array(Buffer.from(b64, 'base64')); // node fallback
}

// DER ECDSA sig (SEQUENCE{INTEGER r, INTEGER s}) -> raw r||s (IEEE P1363, 64 bytes for P-256), which is
// what crypto.subtle.verify(ECDSA) requires. STRICT (matches OpenSSL / Python cryptography, so the
// Explorer's verdict stays byte-for-byte parity with the auditor): rejects trailing bytes, a SEQUENCE
// length that doesn't consume the buffer, non-minimal INTEGER encodings, and negative INTEGERs — a
// lenient decoder would show a false-green SIGNED-VERIFIED for a non-canonically-encoded signature the
// authoritative auditor FAILs.
function derToP1363(der) {
  let i = 0;
  if (der[i++] !== 0x30) throw new Error('bad DER: expected SEQUENCE');
  let len = der[i++];
  if (len & 0x80) {
    const nb = len & 0x7f;
    if (nb < 1 || nb > 2) throw new Error('bad DER: bad SEQUENCE length form');
    len = 0; for (let j = 0; j < nb; j++) len = (len << 8) | der[i++];
  }
  if (i + len !== der.length) throw new Error('bad DER: SEQUENCE length mismatch / trailing bytes');
  function readInt() {
    if (der[i++] !== 0x02) throw new Error('bad DER: expected INTEGER');
    const l = der[i++];
    if (l < 1) throw new Error('bad DER: empty INTEGER');
    const bytes = der.slice(i, i + l); i += l;
    if (bytes[0] & 0x80) throw new Error('bad DER: negative INTEGER');
    if (l > 1 && bytes[0] === 0x00 && (bytes[1] & 0x80) === 0) throw new Error('bad DER: non-minimal INTEGER');
    const v = bytes[0] === 0x00 ? bytes.slice(1) : bytes;   // strip the single leading sign byte, if any
    if (v.length > 32) throw new Error('bad DER: INTEGER too large for P-256');
    const out = new Uint8Array(32);
    out.set(v, 32 - v.length); // left-pad
    return out;
  }
  const r = readInt(); const s = readInt();
  if (i !== der.length) throw new Error('bad DER: trailing bytes after INTEGERs');
  const sig = new Uint8Array(64); sig.set(r, 0); sig.set(s, 32);
  return sig;
}

function pemToDer(pem) {
  const b64 = pem.replace(/-----BEGIN [^-]+-----/, '').replace(/-----END [^-]+-----/, '').replace(/\s+/g, '');
  return b64ToBytes(b64);
}

async function sha256Hex(str) {
  const data = new TextEncoder().encode(str);
  const buf = await crypto.subtle.digest('SHA-256', data);
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function verifySignature(pem, sigBase64, canonicalStr) {
  const key = await crypto.subtle.importKey('spki', pemToDer(pem),
    { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
  const sig = derToP1363(b64ToBytes(sigBase64));
  return crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, key, sig, new TextEncoder().encode(canonicalStr));
}

const SIGNED_VERIFIED = 'SIGNED-VERIFIED';
const INTEGRITY_ONLY = 'INTEGRITY-ONLY';
const FAIL = 'FAIL';

// Full attestation of a pasted proof, mirroring auditor/verify.py (integrity + provenance + optional
// pin). `expectedKey` (optional) is the pinned signer resource name; when given, a signature that
// claims a different key FAILs even if the crypto verifies. Never returns "verified correct".
async function attest(proofText, pemStr, expectedKey) {
  const messages = [];
  const proof = parseJson(proofText);
  const canonical = canonicalBundleFromText(proofText);
  const recomputed = 'sha256:' + await sha256Hex(canonical);
  const claimed = proof.digest || '';
  const integrityOk = recomputed === claimed;
  messages.push(`INTEGRITY ${integrityOk ? 'OK' : 'FAIL'}: ${claimed}` +
    (integrityOk ? '' : ` (recomputed ${recomputed})`));

  const sig = proof.signature;
  let signatureOk = null;
  let signerPinned = null;
  if (!sig) {
    messages.push('UNSIGNED: digest-only bundle (no signature to verify)');
  } else if (!pemStr || !pemStr.trim()) {
    messages.push('SIGNED but no public key supplied — provenance NOT checked');
  } else {
    try {
      signatureOk = await verifySignature(pemStr, sig.signature, canonical);
      messages.push(signatureOk ? `SIGNATURE OK (${sig.algorithm}, claimed key ${sig.key})`
        : 'SIGNATURE FAIL: does not verify against this public key');
    } catch (e) {
      signatureOk = false;
      messages.push('SIGNATURE FAIL: ' + e.message);
    }
    if (expectedKey && expectedKey.trim()) {
      signerPinned = sig.key === expectedKey.trim();
      if (!signerPinned) messages.push(`SIGNER PIN FAIL: claims ${sig.key}, pinned ${expectedKey.trim()}`);
    }
  }

  let tri;
  if (!integrityOk) tri = FAIL;
  else if (sig) {
    const pinnedOk = (expectedKey && expectedKey.trim()) ? signerPinned : true;
    tri = (signatureOk && pinnedOk) ? SIGNED_VERIFIED : FAIL;
    if (tri === SIGNED_VERIFIED) messages.push('ATTEST SIGNED-VERIFIED (provenance + integrity)');
  } else tri = INTEGRITY_ONLY;

  return { tri_state: tri, integrity_ok: integrityOk, signature_ok: signatureOk,
    signer_pinned: signerPinned, canonical_bytes: new TextEncoder().encode(canonical).length, messages };
}

const _api = { RawNum, parseJson, escapeString, canonicalize, canonicalBundleFromText,
  derToP1363, pemToDer, sha256Hex, verifySignature, attest,
  SIGNED_VERIFIED, INTEGRITY_ONLY, FAIL };

if (typeof module !== 'undefined' && module.exports) module.exports = _api;   // Node
if (typeof window !== 'undefined') window.AirbagVerify = _api;                // browser
