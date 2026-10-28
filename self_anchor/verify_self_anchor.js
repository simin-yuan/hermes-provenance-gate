#!/usr/bin/env node
/**
 * One command: verify a self-anchored post.
 *
 *   node verify_self_anchor.js ~/shiqing-state/self_anchor/2026-10-25_x_2114253846482665752
 *
 * Checks, in order, and prints each result:
 *   1. sha256(body.txt) bytes  ==  manifest.digest           (nothing drifted)
 *   2. sha256(body.txt) bytes  ==  digest inside body.txt.ots (the proof is about THIS file)
 *   3. each Bitcoin attestation's terminal op chain value == merkle_root of the block header it names
 *   4. sha256d(80-byte block header) == the block hash the chain says (header self-validates)
 *
 * Nothing here trusts the timestamping service: the proof file, the local body,
 * and public block headers are enough. Needs curl on PATH and HTTPS_PROXY if
 * the machine reaches the internet through a proxy.
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execFileSync } = require('child_process');

const otsPath = (() => {
  const fromEnv = process.env.OTS_JS;
  if (fromEnv) return fromEnv;
  try {
    return path.dirname(require.resolve('javascript-opentimestamps/package.json'));
  } catch (e) {
    console.error('javascript-opentimestamps not found: set OTS_JS to the package directory (or its ots-cli.js),');
    console.error('or install it where Node can resolve it (npm install javascript-opentimestamps).');
    process.exit(2);
  }
})();
let ots;
try {
  ots = require(otsPath);
} catch (e) {
  console.error(`javascript-opentimestamps not found at ${otsPath}`);
  console.error('Set OTS_JS to the package directory, or to its ots-cli entry (npm install javascript-opentimestamps).');
  process.exit(2);
}
const { DetachedTimestampFile } = ots;

const dir = process.argv[2];
if (!dir) {
  console.error('usage: node verify_self_anchor.js <self_anchor dir>');
  process.exit(2);
}

const sha256 = (buf) => crypto.createHash('sha256').update(buf).digest('hex');
const sha256d = (buf) =>
  crypto.createHash('sha256').update(
    crypto.createHash('sha256').update(buf).digest()
  ).digest('hex');

function curlJSON(url) {
  const out = execFileSync('curl', ['-s', '--max-time', '40', url], { encoding: 'utf8' });
  if (!out.trim()) throw new Error('empty response from ' + url);
  return JSON.parse(out);
}

const bodyPath = path.join(dir, 'body.txt');
const otsFile = path.join(dir, 'body.txt.ots');
const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
const body = fs.readFileSync(bodyPath);
const digest = sha256(body);

let ok = true;
const fail = (m) => { ok = false; console.log('  FAIL  ' + m); };

console.log(`dir        ${dir}`);
console.log(`body       ${body.length} bytes`);
console.log(`sha256     ${digest}`);

// 1
if (digest === manifest.digest) console.log('  ok    body.txt matches manifest.digest');
else fail(`body.txt hashes to ${digest}, manifest says ${manifest.digest}`);

// 2
const det = DetachedTimestampFile.deserialize(fs.readFileSync(otsFile));
const proofDigest = Buffer.from(det.fileDigest()).toString('hex');
if (proofDigest === digest) console.log('  ok    body.txt.ots was built over this exact file');
else fail(`proof digest is ${proofDigest}, file hashes to ${digest}`);

// 3
const leaves = [];
function walk(node, ops) {
  if (!node) return;
  for (const a of (node.attestations || [])) {
    leaves.push({ att: a.toString(), ops, msg: Buffer.from(Array.from(node.msg).reverse()).toString('hex') });
  }
  const o = node.ops;
  if (!o) return;
  if (typeof o.forEach === 'function' && !Array.isArray(o)) {
    o.forEach((child, op) => walk(child, ops.concat([op.toString()])));
  } else if (o.timestamp) {
    walk(o.timestamp, ops.concat([o.toString()]));
  }
}
walk(det.timestamp, []);

const blockRe = /BitcoinBlockHeaderAttestation\((\d+)\)/;
const pending = leaves.filter((l) => !blockRe.test(l.att));
const confirmed = leaves.filter((l) => blockRe.test(l.att));

for (const p of pending) console.log(`  ..    pending: ${p.att} (not in a block yet)`);

for (const c of confirmed) {
  const height = Number(c.att.match(blockRe)[1]);
  console.log(`\nblock ${height}  path ${c.ops.length} ops  merkle_root_from_proof ${c.msg}`);
  let hash;
  hash = execFileSync(
    'curl', ['-s', '--max-time', '40', `https://mempool.space/api/block-height/${height}`],
    { encoding: 'utf8' }
  ).trim();
  const header = execFileSync(
    'curl', ['-s', '--max-time', '40', `https://mempool.space/api/v1/block/${hash}`],
    { encoding: 'utf8' }
  );
  const block = JSON.parse(header);

  // 4: rebuild the 80-byte header and check it hashes to the block hash
  const h = Buffer.from([
    ...le32(block.version),
    ...Buffer.from(block.previousblockhash || '', 'hex').reverse(),
    ...Buffer.from(block.merkle_root, 'hex').reverse(),
    ...le32(block.timestamp),
    ...le32(block.bits),
    ...le32(block.nonce),
  ]);
  const rebuilt = Buffer.from(sha256d(h), 'hex').reverse().toString('hex');
  if (rebuilt === hash) console.log(`  ok    header ${h.length} B hashes to ${rebuilt}`);
  else fail(`header hashes to ${rebuilt}, chain says ${hash}`);

  // 3
  if (c.msg === block.merkle_root) console.log(`  ok    proof commits into this block's merkle root`);
  else fail(`proof ends at ${c.msg}, block merkle_root is ${block.merkle_root}`);
}

function le32(n) {
  const b = Buffer.alloc(4);
  b.writeUInt32LE(Number(n) >>> 0);
  return [...b];
}

console.log('');
if (confirmed.length === 0) {
  console.log('RESULT  pending — proof is valid but not yet confirmed by a Bitcoin block.');
  console.log('        re-run later with `node ots-cli.js upgrade body.txt.ots` then this script.');
  process.exit(ok ? 0 : 1);
}
console.log(ok ? 'RESULT  verified: bytes -> digest -> merkle root -> block header' : 'RESULT  FAILED');
process.exit(ok ? 0 : 1);
