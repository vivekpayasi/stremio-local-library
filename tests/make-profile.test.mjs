// Run with:  node --test   (from the repo root)
//
// tcl-dumpsys-*.txt are real `adb shell dumpsys media.player` / `dumpsys display` output from the TCL TV
// the shipped profile was learned on, trimmed to the decoder list and the HDR capabilities.

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

import { parseDecoders, parseHdrTypes, profileFromTv } from '../src/make-profile.mjs';
import { defaultProfile, mergeProfile, rate } from '../src/rating.mjs';

const fixture = (f) => fs.readFileSync(new URL(`./fixtures/${f}`, import.meta.url), 'utf8');
const TCL = {
  decoders: parseDecoders(fixture('tcl-dumpsys-media-player.txt')),
  hdrTypes: parseHdrTypes(fixture('tcl-dumpsys-display.txt')),
  props: { manufacturer: 'TCL', model: 'Smart TV Pro', release: '12', soc: 'Amlogic AMLT963D4' },
};

// A made-up 1080p box: no Dolby Vision, no DTS, no AV1, HEVC only to 1080p, an SDR screen.
const BOX = `Decoder infos by media types:
=============================

Media type 'video/hevc':
  Decoder "OMX.vendor.hevc.decoder" supports
    attributes: 0xa: [
      encoder: 0,
      vendor: 1,
      software-only: 0,
      hw-accelerated: 1 ]
    profile/levels: [
          1/ 2097152 (Main/Main5.1),
          2/ 2097152 (Main10/Main5.1) ]
    details: AMessage(what = 0x00000000) = {
        string size-range = "64x64-1920x1088"
      }
  Decoder "OMX.vendor.hevc.decoder.secure" supports
    attributes: 0xa: [
      hw-accelerated: 1 ]
    profile/levels: [
          1/ 2097152 (Main/Main5.1) ]
    details: AMessage(what = 0x00000000) = {
        string size-range = "64x64-3840x2160"
      }

Media type 'video/avc':
  Decoder "OMX.vendor.avc.decoder" supports
    attributes: 0xa: [
      software-only: 0,
      hw-accelerated: 1 ]
    profile/levels: [
          8/   32768 (High/5.1),
         16/   32768 (High10/5.1) ]
    details: AMessage(what = 0x00000000) = {
        string size-range = "64x64-1920x1088"
      }

Media type 'video/av01':
  Decoder "c2.android.av1.decoder" supports
    attributes: 0x4: [
      software-only: 1,
      hw-accelerated: 0 ]
    profile/levels: [
          1/    8192 (Main8/5.1) ]

Media type 'audio/ac3':
  Decoder "OMX.vendor.ac3.decoder" supports
    profile/levels: []

Media type 'audio/eac3':
  Decoder "OMX.vendor.eac3.decoder" supports
    profile/levels: []

Encoder infos by media types:
=============================

Media type 'audio/vnd.dts':
  Encoder "OMX.not.a.decoder" supports
`;
const BOX_TV = {
  decoders: parseDecoders(BOX),
  hdrTypes: parseHdrTypes('HdrCapabilities{mSupportedHdrTypes=[], mMaxLuminance=0.0}'),
  props: { manufacturer: 'Acme', model: 'Box', release: '11', soc: '' },
};

test('the registered decoders are read per media type, secure variants and encoders left out', () => {
  const dv = TCL.decoders.get('video/dolby-vision');
  assert.deepEqual(dv.map((d) => d.name), [
    'OMX.amlogic.dolby-vision.dvhe.decoder.awesome2',
    'OMX.amlogic.dolby-vision.dvav.decoder.awesome2',
    'OMX.amlogic.dolby-vision.dav1.decoder.awesome2',
  ]);
  assert.deepEqual(dv[0].profiles, [16, 32, 256]);
  assert.deepEqual(TCL.decoders.get('video/hevc')[0].maxSize, { w: 3840, h: 2160 });
  assert.ok(TCL.decoders.has('audio/vnd.dts.hd'));
  assert.ok(!TCL.decoders.has('audio/true-hd'));
  assert.ok(!BOX_TV.decoders.has('audio/vnd.dts'), 'encoders are not decoders');
  assert.deepEqual(BOX_TV.decoders.get('video/hevc').map((d) => d.name), ['OMX.vendor.hevc.decoder']);
});

test('the screen HDR types are read from dumpsys display', () => {
  assert.deepEqual(TCL.hdrTypes, [1, 2, 3, 4]);
  assert.deepEqual(BOX_TV.hdrTypes, []);
});

test('the TCL dump predicts what real plays showed', () => {
  const { profile } = profileFromTv(TCL);
  assert.equal(profile.dolbyVision['7'].cap, 1, 'profile 7 failed on the TV');
  assert.match(profile.dolbyVision['7'].why, /doesn't support/);
  assert.equal(profile.dolbyVision['8'], null, 'profile 8 played');
  assert.equal(profile.audio.dts, null, 'DTS played');
  assert.equal(profile.audio['dts-hd'], null, 'DTS-HD MA played');
  assert.equal(profile.audio.truehd.cap, 2, 'no TrueHD decoder');
  assert.equal(profile.video.avc10.cap, 2, 'no High10 profile');
  assert.equal(profile.video.av1.cap, 4, 'present but not played yet');
  assert.equal(profile.resolution['2160p'], null);
  assert.equal(profile.resolution['4320p'].cap, 1);
  assert.equal(profile.hdr.hdr10, null);
  assert.match(profile.name, /TCL Smart TV Pro/);
});

test('a 1080p SDR box without Dolby Vision or DTS gets the matching caps', () => {
  const { profile } = profileFromTv(BOX_TV);
  assert.equal(profile.resolution['2160p'].cap, 1);
  assert.match(profile.resolution['2160p'].why, /1920x1088/);
  assert.equal(profile.dolbyVision['5'].cap, 1);
  assert.equal(profile.dolbyVision['7'].cap, 1);
  assert.equal(profile.dolbyVision['8'], null, 'plays as HDR10 through the HEVC decoder');
  assert.equal(profile.audio.dts.cap, 2);
  assert.equal(profile.video.av1.cap, 2, 'software decoder only');
  assert.equal(profile.video.avc10, null, 'High10 is listed');
  assert.equal(profile.video.vc1.cap, 1);
  assert.equal(profile.hdr.hdr10.cap, 2);
});

test('a generated profile rates real streams through the normal merge', () => {
  const { profile } = profileFromTv(BOX_TV);
  const merged = mergeProfile(defaultProfile(), profile);
  const s = {
    name: 'Torrentio\n4k HDR',
    title: 'Movie.2024.2160p.WEB-DL.DDP5.1.HDR.H.265-XYZ\n👤 80 💾 15.00 GB ⚙️ X',
    infoHash: 'a'.repeat(40), fileIdx: 0,
    behaviorHints: { filename: 'Movie.2024.2160p.WEB-DL.DDP5.1.HDR.H.265-XYZ.mkv' },
  };
  const r = rate(s, { profile: merged, runtimeMin: 120 });
  assert.equal(r.stars, 1);
  assert.match(r.issues.map((i) => i.why).join(' | '), /4K/);
});
