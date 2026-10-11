#!/usr/bin/env node
// make-profile — a Stream Advisor device profile for any Android TV, from what the TV itself reports.
//
//   node make-profile.mjs 192.168.1.40            # show what the TV supports and the profile it implies
//   node make-profile.mjs 192.168.1.40 --write    # save it as ~/.stremio-local-addon/device-profile.json
//
// It reads, over ADB, the decoders Android has registered (`dumpsys media.player`: each codec, the profiles
// it accepts — every Dolby Vision profile separately — and its maximum picture size) and the HDR formats the
// screen can show (`dumpsys display`). That predicted this project's TCL TV correctly: no Dolby Vision
// profile 7 in the decoder (the remux failed), DTS listed (it played), no TrueHD (it crashed). A listed
// decoder is still not a play, so codecs that are rare in practice stay at 4 stars until one has played.
//
// On the TV: Settings → Device Preferences → About → select "Build" 7 times, then Developer options →
// USB debugging (and Network debugging), and accept the "Allow debugging?" prompt on the first connect.

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

/** Media type -> registered decoders, from `dumpsys media.player`. Secure (DRM) variants are left out. */
export function parseDecoders(dump) {
  const start = dump.indexOf('Decoder infos by media types:');
  if (start === -1) return new Map();
  const end = dump.indexOf('Encoder infos by media types:', start);
  const out = new Map();
  let type = null, dec = null, inProfiles = false;
  for (const line of dump.slice(start, end === -1 ? undefined : end).split('\n')) {
    let m;
    if ((m = /^Media type '([^']+)':/.exec(line))) {
      type = m[1].toLowerCase();
      dec = null;
      continue;
    }
    if ((m = /^\s{2}Decoder "([^"]+)" supports/.exec(line)) && type) {
      dec = null;
      if (m[1].endsWith('.secure')) continue;
      dec = { name: m[1], hw: null, softwareOnly: null, profiles: [], maxSize: null };
      if (!out.has(type)) out.set(type, []);
      out.get(type).push(dec);
      continue;
    }
    if (!dec) continue;
    if ((m = /hw-accelerated: (\d)/.exec(line))) dec.hw = m[1] === '1';
    if ((m = /software-only: (\d)/.exec(line))) dec.softwareOnly = m[1] === '1';
    if (/profile\/levels: \[/.test(line)) inProfiles = true;
    if (inProfiles) {
      for (const p of line.replace(/^.*profile\/levels: \[/, '').matchAll(/(\d+)\/\s*(\d+)/g)) dec.profiles.push(Number(p[1]));
      if (line.includes(']')) inProfiles = false;
      continue;
    }
    if ((m = /size-range = "\d+x\d+-(\d+)x(\d+)"/.exec(line))) dec.maxSize = { w: Number(m[1]), h: Number(m[2]) };
  }
  return out;
}

/** HDR types the screen supports, from `dumpsys display`: 1 Dolby Vision, 2 HDR10, 3 HLG, 4 HDR10+. */
export function parseHdrTypes(display) {
  const m = /mSupportedHdrTypes=\[([^\]]*)\]/.exec(display);
  return m ? m[1].split(',').map((x) => Number(x.trim())).filter((x) => Number.isFinite(x) && x > 0) : [];
}

// android.media.MediaCodecInfo.CodecProfileLevel
const DV_PROFILE_BIT = { 4: 0x10, 5: 0x20, 7: 0x80, 8: 0x100, 9: 0x200, 10: 0x400 };
const AVC_HIGH10 = 0x10;
const HDR10 = 2, HLG = 3;

const AUDIO = [
  ['eac3', 'E-AC3', ['audio/eac3', 'audio/eac3-joc']],
  ['ac3', 'AC3', ['audio/ac3']],
  ['aac', 'AAC', ['audio/mp4a-latm']],
  ['dts', 'DTS', ['audio/vnd.dts']],
  ['dts-hd', 'DTS-HD MA', ['audio/vnd.dts.hd']],
  ['truehd', 'TrueHD', ['audio/true-hd']],
  ['opus', 'Opus', ['audio/opus']],
  ['flac', 'FLAC', ['audio/flac']],
  ['mp3', 'MP3', ['audio/mpeg']],
];

/**
 * The device-profile overrides a TV's own report implies. Every TV-specific rule is set explicitly (null =
 * no limit), so nothing learned on another TV leaks in; the generic rules (bitrate, peers, release tags,
 * MP4 subtitles) still come from the shipped profile.
 */
export function profileFromTv({ decoders, hdrTypes = [], props = {} }) {
  if (!decoders.size) throw new Error('no decoder list in the dump — is this an Android TV?');
  const findings = [];
  const note = (s) => findings.push(s);

  const of = (types) => types.flatMap((t) => decoders.get(t) ?? []);
  const isHw = (d) => d.hw === true || (d.softwareOnly === false && !/^(c2\.android|OMX\.google)\./.test(d.name));
  const kind = (types) => {
    const list = of(types);
    return !list.length ? 'none' : list.some(isHw) ? 'hardware' : 'software';
  };
  const videoRule = (types, label, common = false) => {
    const k = kind(types);
    note(`${label.padEnd(9)} ${k === 'none' ? 'no decoder' : `${k} decoder (${of(types).map((d) => d.name).join(', ')})`}`);
    if (k === 'none') return { cap: 1, why: `${label} — no decoder on this TV` };
    if (k === 'software') return { cap: 2, why: `${label} — software decoder only` };
    return common ? null : { cap: 4, why: `${label} — decoder present, not played yet` };
  };

  const video = {
    hevc: videoRule(['video/hevc'], 'HEVC', true),
    avc: videoRule(['video/avc'], 'H.264', true),
    av1: videoRule(['video/av01'], 'AV1'),
    vp9: videoRule(['video/x-vnd.on2.vp9'], 'VP9'),
    vc1: videoRule(['video/vc1', 'video/wvc1'], 'VC-1'),
    mpeg2: videoRule(['video/mpeg2'], 'MPEG-2'),
  };
  const high10 = of(['video/avc']).some((d) => isHw(d) && d.profiles.some((p) => p & AVC_HIGH10));
  video.avc10 = high10 ? null : { cap: 2, why: '10-bit H.264 — no hardware decoder' };
  note(`H.264 10-bit (Hi10P) ${high10 ? 'listed' : 'not listed'}`);

  // 4K and 8K: the largest picture a hardware HEVC decoder accepts.
  const sizes = of(['video/hevc']).filter(isHw).map((d) => d.maxSize).filter(Boolean);
  const max = sizes.sort((a, b) => b.w * b.h - a.w * a.h)[0] ?? null;
  const fits = (w, h) => max && max.w >= w && max.h >= h;
  note(`largest HEVC picture ${max ? `${max.w}x${max.h}` : 'unknown'}`);
  const resolution = {
    '2160p': !max || fits(3840, 2160) ? null : { cap: 1, why: `4K — this TV's decoder stops at ${max.w}x${max.h}` },
    '4320p': fits(7680, 4320) ? null : { cap: 1, why: '8K — beyond this TV' },
  };

  // Dolby Vision: decoders list each profile they accept. Without one, profile 8 still plays as HDR10
  // through the HEVC decoder; profiles 5 and 7 have no such fallback.
  const dv = new Set(of(['video/dolby-vision']).flatMap((d) => d.profiles));
  const dvHas = (p) => [...dv].some((x) => x & DV_PROFILE_BIT[p]);
  note(`Dolby Vision profiles ${[4, 5, 7, 8, 9, 10].filter(dvHas).join(', ') || 'none'}`);
  const dolbyVision = {
    5: dvHas(5) ? { cap: 4, why: 'Dolby Vision profile 5 — decoder lists it, not played yet' }
      : { cap: 1, why: 'Dolby Vision profile 5 — no decoder for it (wrong colours)' },
    7: dvHas(7) ? { cap: 4, why: 'Dolby Vision profile 7 — decoder lists it, not played yet' }
      : { cap: 1, why: 'Dolby Vision profile 7 (Blu-ray) — this TV\'s decoder doesn\'t support it' },
    8: dvHas(8) || kind(['video/hevc']) === 'hardware' ? null : { cap: 1, why: 'Dolby Vision profile 8 — no decoder for it' },
    unknown: dvHas(5) ? null : { cap: 2, why: 'Dolby Vision profile unknown — profile 5 would show wrong colours here' },
  };

  // Audio: a listed decoder played on the TCL TV (DTS), a missing one failed (TrueHD).
  const audio = { pcm: null };   // raw PCM needs no decoder
  for (const [key, label, types] of AUDIO) {
    const present = types.some((t) => decoders.has(t));
    audio[key] = present ? null : { cap: 2, why: `${label} audio — no decoder on this TV` };
    note(`${label.padEnd(9)} ${present ? 'decoder listed' : 'NO decoder'}`);
  }

  const hdr = {
    hdr10: hdrTypes.includes(HDR10) ? null : { cap: 2, why: 'HDR10 — this screen can\'t show HDR' },
    hlg: hdrTypes.includes(HLG) ? null : { cap: 3, why: 'HLG — this screen can\'t show it' },
  };
  const hdrNames = { 1: 'Dolby Vision', 2: 'HDR10', 3: 'HLG', 4: 'HDR10+' };
  note(`screen HDR ${hdrTypes.map((t) => hdrNames[t] ?? t).join(', ') || 'none'}`);

  const name = [`${props.manufacturer ?? ''} ${props.model ?? ''}`.trim() || 'Android TV',
    props.release && `Android ${props.release}`, props.soc].filter(Boolean).join(' · ');
  return {
    profile: {
      name: `${name} (generated)`,
      about: [
        `Generated by make-profile.mjs on ${new Date().toISOString().slice(0, 10)} from the TV's registered decoders`,
        '(adb shell dumpsys media.player) and the HDR formats its screen supports (dumpsys display). A listed',
        'decoder usually plays; rare codecs stay at 4 stars until one has. Edit freely — null removes a rule.',
      ],
      video, dolbyVision, audio, resolution, hdr,
    },
    findings,
  };
}

// ---------------------------------------------------------------- command line

function adb(args) {
  try {
    return execFileSync('adb', args, { encoding: 'utf8', timeout: 30000, stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (e) {
    if (e.code === 'ENOENT') throw new Error('adb is not installed — on a Mac: brew install --cask android-platform-tools');
    throw new Error(`adb ${args.join(' ')} failed: ${(e.stderr || e.message).toString().trim()}`);
  }
}

function main(argv) {
  const args = argv.slice(2);
  const target = args.find((a) => !a.startsWith('--'));
  if (!target) {
    console.error('usage: node make-profile.mjs <tv-ip[:port] or adb serial> [--write]');
    process.exit(1);
  }
  const serial = /^\d+\.\d+\.\d+\.\d+$/.test(target) ? `${target}:5555` : target;
  if (/^\d+\.\d+\.\d+\.\d+:\d+$/.test(serial)) {
    const out = adb(['connect', serial]);
    if (!/connected/.test(out)) throw new Error(`could not connect to ${serial}: ${out.trim()} — is debugging on?`);
  }
  const sh = (cmd) => adb(['-s', serial, 'shell', cmd]).replaceAll('\r', '');
  const state = adb(['-s', serial, 'get-state']).trim();
  if (state !== 'device') throw new Error(`the TV is "${state}" — accept the "Allow debugging?" prompt on it, then retry`);

  const prop = (k) => sh(`getprop ${k}`).trim();
  const props = {
    manufacturer: prop('ro.product.manufacturer'), model: prop('ro.product.model'),
    release: prop('ro.build.version.release'),
    soc: [prop('ro.soc.manufacturer'), prop('ro.soc.model')].filter(Boolean).join(' ') || prop('ro.board.platform'),
  };
  const { profile, findings } = profileFromTv({
    decoders: parseDecoders(sh('dumpsys media.player')),
    hdrTypes: parseHdrTypes(sh('dumpsys display')),
    props,
  });
  const stremio = sh('pm list packages com.stremio').trim();

  console.log(`${profile.name.replace(' (generated)', '')}`);
  for (const f of findings) console.log(`  ${f}`);
  if (!stremio) console.log('  ! Stremio is not installed on this TV');
  const json = `${JSON.stringify(profile, null, 2)}\n`;
  if (!args.includes('--write')) {
    console.log(`\n${json}`);
    console.log('Run again with --write to save it as ~/.stremio-local-addon/device-profile.json');
    return;
  }
  const file = path.join(os.homedir(), '.stremio-local-addon', 'device-profile.json');
  fs.mkdirSync(path.dirname(file), { recursive: true });
  if (fs.existsSync(file)) {
    const bak = `${file}.bak-${new Date().toISOString().replace(/[:.]/g, '-')}`;
    fs.copyFileSync(file, bak);
    console.log(`\nkept your previous profile as ${bak}`);
  }
  fs.writeFileSync(file, json);
  console.log(`saved ${file} — the Stream Advisor picks it up on the next stream list`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(fs.realpathSync(process.argv[1])).href) {
  try {
    main(process.argv);
  } catch (e) {
    console.error(`make-profile: ${e.message}`);
    process.exit(1);
  }
}
