#!/usr/bin/env node
/**
 * Walk a DEUSPROOF .ots proof and print the op chain that leads to each Bitcoin
 * attestation, plus the value it terminal-computes to (the block's merkle root).
 *
 *   npm install javascript-opentimestamps
 *   node ots_path.js proof.ots
 *
 * Compare the printed msg_be of each attestation with the merkle_root field of
 * the corresponding block header (see check_headers.py). Equal => the digest
 * inside the proof is committed into that block's merkle tree.
 */
const fs = require('fs');
const ots = require('javascript-opentimestamps');
const { DetachedTimestampFile } = ots;

const det = DetachedTimestampFile.deserialize(fs.readFileSync(process.argv[2]));
console.log('digest in proof:', Buffer.from(det.fileDigest()).toString('hex'));

const results = [];
function walk(node, depth, ops) {
  if (!node) return;
  for (const a of (node.attestations || [])) {
    const s = a.toString();
    if (/Bitcoin/.test(s)) {
      results.push({
        att: s, depth, ops,
        msg_be: Buffer.from(Array.from(node.msg).reverse()).toString('hex'),
      });
    }
  }
  const o = node.ops;
  if (!o) return;
  if (typeof o.forEach === 'function' && !Array.isArray(o)) {
    o.forEach((child, op) => walk(child, depth + 1, ops.concat([op.toString()])));
  } else if (o.timestamp) {
    walk(o.timestamp, depth + 1, ops.concat([o.toString()]));
  }
}
walk(det.timestamp, 0, []);

for (const r of results) {
  console.log('-'.repeat(60));
  console.log(r.att, '| path length', r.ops.length);
  console.log('  first 4:', r.ops.slice(0, 4).join(' | '));
  console.log('  last 4 :', r.ops.slice(-4).join(' | '));
  console.log('  merkle root (big-endian):', r.msg_be);
}
