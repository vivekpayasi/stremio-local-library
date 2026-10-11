// Run with:  node --test   (from the repo root)
//
// Release titles are real Torrentio output; info hashes are made up.

import test from 'node:test';
import assert from 'node:assert/strict';

import { rate, decorate, adviseStreams, defaultProfile, mergeProfile } from '../src/rating.mjs';

const PROFILE = defaultProfile();
const hash = (c) => c.repeat(40);

const tio = (quality, title, filename, fileIdx = 0, c = 'a') => ({
  name: `Torrentio\n${quality}`,
  title,
  infoHash: hash(c),
  fileIdx,
  behaviorHints: { bingeGroup: `torrentio|${quality}`, filename },
});

const R00T = tio('4k DV | HDR',
  'Obsession (2026) (2160p UHD BluRay x265 10bit DV HDR TrueHD Atmos 7.1 English French Spanish r00t) [QxR]\n👤 266 💾 20.03 GB ⚙️ 1337x\n🇬🇧 / 🇪🇸 / 🇫🇷',
  'Obsession (2026) (2160p UHD BluRay x265 10bit DV HDR r00t).mkv', 4, '1');
const BTM_MP4 = tio('4k DV | HDR10+',
  'Obsession.2026.2160p.WEB-DL.UNRATED.DV.HDR10+.MULTi.FRE.LAT.Atmos.H265.MP4-BTM\n👤 612 💾 21.74 GB ⚙️ ThePirateBay\nMulti Audio / 🇬🇧 / 🇪🇸 / 🇲🇽 / 🇫🇷',
  'Obsession.2026.2160p.iT.WEB-DL.UNRATED.DV.HDR10+.MULTi[Ben The Men].mp4', 0, '2');
const BLUDV = tio('4k DV | HDR10',
  'Obsessão 2026 WEB-DL 2160p x265 DV HDR10 DUAL 5.1\n👤 173 💾 19.86 GB ⚙️ BluDV\n🇬🇧 / 🇵🇹',
  'Obsessão 2026 WEB-DL 2160p x265 DV HDR10 DUAL 5.1.mkv', 0, '3');
const MESC = tio('4320p HDR',
  'Obsession (2025) 4320p HDR Ai Upscale 7.1 Atmos + 5.1 Multidub/Sub MKV -Mesc\n👤 9 💾 18.34 GB ⚙️ ThePirateBay\nMulti Subs',
  'Obsession (2025) 4320p HDR Ai Upscale -Mesc.mkv', 0, '4');
const FRAMESTOR = tio('4k DV',
  'Chernobyl S01 UHD BluRay 2160p DTS-HD MA 5 1 DV HEVC REMUX-FraMeSToR\nChernobyl.S01E01.1.23.45.2160p.DTS-HD.MA.5.1.DV.HEVC.REMUX-FraMeSToR.mkv\n👤 110 💾 24.48 GB ⚙️ ThePirateBay',
  'Chernobyl.S01E01.1.23.45.2160p.DTS-HD.MA.5.1.DV.HEVC.REMUX-FraMeSToR.mkv', 1, '5');
const ACCID = tio('4k DV | HDR',
  'Chernobyl - S01 (2019 2160p x265 DV 10bit HDR Ita Ac3 Eng Eac3 Sub Ita Eng BDrip) [Accid]\nChernobyl - S01E01 - 1.23.45 (2160p DV HDR x265) [Accid].mkv\n👤 57 💾 6.72 GB ⚙️ 1337x\n🇬🇧 / 🇮🇹',
  'Chernobyl - S01E01 - 1.23.45 (2160p DV HDR x265) [Accid].mkv', 1, '6');
const MEM = tio('4k HDR',
  'Chernobyl.S01.2160p.UHD.BluRay.x265.10bit.HDR-MeM\nChernobyl.S01E01.1.23.45.ITA.ENG.2160p.UHD.BluRay.x265.10bit.HDR-MeM.mkv\n👤 38 💾 13.45 GB ⚙️ ilCorSaRoNeRo\n🇬🇧 / 🇮🇹',
  'Chernobyl.S01E01.1.23.45.ITA.ENG.2160p.UHD.BluRay.x265.10bit.HDR-MeM.mkv', 1, '7');
const REMUX_P7 = tio('4k DV | HDR',
  'Obsession.2025.2026.2160p.UHD.BluRay.Remux.DV.P7.HDR.MULTi.TrueH…\nObsession.2026.2160p.UHD.BluRay.Remux.DV.P7.HDR.MULTi[Ben The Men].mkv\n👤 353 💾 55.46 GB ⚙️ TorrentGalaxy\nMulti Audio / 🇬🇧',
  'Obsession.2026.2160p.UHD.BluRay.Remux.DV.P7.HDR.MULTi[Ben The Men].mkv', 1, '8');
const TIREXO = tio('4k HDR',
  'Troie.2004.DC.MULTI.VF2.2160p.BluRay.4KLight.HDR10.AC3.5.1.x265-TireXo (Troy)\n👤 35 💾 7.04 GB ⚙️ Torrent9\nMulti Audio / 🇫🇷',
  'Troy (2004) MULTi VF2 2160p 4KLight BluRay HDR AC3.5.1 x265-TireXo.mkv', 0, '9');
const web = (peers, c = 'b') => tio('4k',
  `The.Railway.Men.S01E01.2160p.NF.WEB-DL.DDP5.1.H.265-XYZ\n👤 ${peers} 💾 3.10 GB ⚙️ ThePirateBay`,
  'The.Railway.Men.S01E01.2160p.NF.WEB-DL.DDP5.1.H.265-XYZ.mkv', 3, c);

// What torrent_server.py records after probing a played file.
const FACTS = {
  r00t: {
    source: 'ffprobe', container: 'mkv', bitrateMbps: 25.8,
    video: { codec: 'hevc', width: 3840, height: 2160, bitDepth: 10, transfer: 'smpte2084', dv: { profile: 8, level: 6, el: false } },
    audio: [
      { codec: 'truehd', profile: null, channels: 8, lang: 'eng', default: true },
      { codec: 'ac3', profile: null, channels: 6, lang: 'fre', default: false },
    ],
    mp4SubsHidden: 0,
  },
  btm: {
    source: 'ffprobe', container: 'mp4', bitrateMbps: 28.5,
    video: { codec: 'hevc', width: 3840, height: 2160, bitDepth: 10, transfer: 'smpte2084', dv: { profile: 8, level: 6, el: false } },
    audio: [1, 2, 3, 4].map((i) => ({ codec: 'eac3', profile: null, channels: 6, lang: 'eng', default: i === 1 })),
    mp4SubsHidden: 2,
  },
  bludv: {
    source: 'ffprobe', container: 'mkv', bitrateMbps: 26.1,
    video: { codec: 'hevc', width: 3840, height: 2160, bitDepth: 10, transfer: 'smpte2084', dv: { profile: 8, level: 6, el: false } },
    audio: [
      { codec: 'eac3', profile: null, channels: 6, lang: 'por', default: true },
      { codec: 'eac3', profile: null, channels: 6, lang: 'eng', default: false },
    ],
    mp4SubsHidden: 0,
  },
  framestor: {
    source: 'ffprobe', container: 'mkv', bitrateMbps: 59.5,
    video: { codec: 'hevc', width: 3840, height: 2160, bitDepth: 10, transfer: 'smpte2084', dv: { profile: 7, level: 6, el: true } },
    audio: [{ codec: 'dts', profile: 'DTS-HD MA', channels: 6, lang: 'eng', default: true }],
    mp4SubsHidden: 0,
  },
};

const issueText = (r) => r.issues.map((i) => i.why).join(' | ');

// ------------------------------------------------------------------ name-only heuristics

test('TrueHD in the release name rates 1 star', () => {
  const r = rate(R00T, { profile: PROFILE, runtimeMin: 109 });
  assert.equal(r.stars, 1);
  assert.match(issueText(r), /TrueHD/);
});

test('DTS-HD MA with a Blu-ray REMUX rates 1 star', () => {
  const r = rate(FRAMESTOR, { profile: PROFILE, runtimeMin: 65 });
  assert.equal(r.stars, 1);
  assert.match(issueText(r), /DTS-HD/);
});

test('an explicit DV P7 remux rates 1 star', () => {
  const r = rate(REMUX_P7, { profile: PROFILE, runtimeMin: 109 });
  assert.equal(r.stars, 1);
  assert.match(issueText(r), /profile 7/);
});

test('8K AI upscales rate 1 star', () => {
  const r = rate(MESC, { profile: PROFILE, runtimeMin: 109 });
  assert.equal(r.stars, 1);
  assert.match(issueText(r), /8K/);
  assert.match(issueText(r), /upscale/i);
});

test('a WEB-DL MKV with E-AC3, DV P8, sane bitrate and many peers rates 5 stars', () => {
  const r = rate(ACCID, { profile: PROFILE, runtimeMin: 65 });
  assert.equal(r.stars, 5, issueText(r));
  assert.ok(r.bitrateMbps > 13 && r.bitrateMbps < 17, `bitrate ${r.bitrateMbps}`);
  assert.equal(r.estimated, true);
});

test('an MP4 rates at most 4 stars until verified', () => {
  const r = rate(BTM_MP4, { profile: PROFILE, runtimeMin: 109 });
  assert.equal(r.stars, 4, issueText(r));
  assert.match(issueText(r), /MP4/);
});

test('a release that does not name its audio codec rates 4 stars', () => {
  const r = rate(MEM, { profile: PROFILE, runtimeMin: 65 });
  assert.equal(r.stars, 4, issueText(r));
  assert.match(issueText(r), /audio/i);
});

test('AC3 + HDR10 x265 encode rates 5 stars', () => {
  assert.equal(rate(TIREXO, { profile: PROFILE, runtimeMin: 163 }).stars, 5);
});

test('peer count: 0 is dead (1), a handful is risky (2), a dozen is slow (4)', () => {
  assert.equal(rate(web(0), { profile: PROFILE, runtimeMin: 60 }).stars, 1);
  assert.equal(rate(web(3), { profile: PROFILE, runtimeMin: 60 }).stars, 2);
  assert.equal(rate(web(12), { profile: PROFILE, runtimeMin: 60 }).stars, 4);
  assert.equal(rate(web(40), { profile: PROFILE, runtimeMin: 60 }).stars, 5);
});

test('estimated bitrate above 48 Mbps rates 2 stars, above 35 Mbps 3 stars', () => {
  const big = (gb) => tio('4k', `Movie.2024.2160p.WEB-DL.DDP5.1.H.265-XYZ\n👤 90 💾 ${gb} GB ⚙️ X`, 'Movie.2024.2160p.WEB-DL.DDP5.1.H.265-XYZ.mkv');
  assert.equal(rate(big('55.00'), { profile: PROFILE, runtimeMin: 120 }).stars, 2);   // ~66 Mbps
  assert.equal(rate(big('35.00'), { profile: PROFILE, runtimeMin: 120 }).stars, 3);   // ~42 Mbps
  assert.equal(rate(big('20.00'), { profile: PROFILE, runtimeMin: 120 }).stars, 5);   // ~24 Mbps
});

test('without a runtime there is no bitrate penalty', () => {
  const r = rate(ACCID, { profile: PROFILE });
  assert.equal(r.bitrateMbps, null);
  assert.equal(r.stars, 5);
});

test('10-bit H.264 (Hi10P) rates 2 stars', () => {
  const s = tio('1080p', '[SubsPlease] Some Anime - 01 (1080p) [Hi10P x264 AAC]\n👤 50 💾 1.10 GB ⚙️ Nyaa', 'Some Anime - 01 (1080p).mkv');
  assert.equal(rate(s, { profile: PROFILE, runtimeMin: 24 }).stars, 2);
});

test('AV1 is untested on this TV: 3 stars', () => {
  const s = tio('4k', 'Movie.2024.2160p.WEB-DL.DDP5.1.AV1-XYZ\n👤 50 💾 8.00 GB ⚙️ X', 'Movie.2024.2160p.WEB-DL.DDP5.1.AV1-XYZ.mkv');
  const r = rate(s, { profile: PROFILE, runtimeMin: 120 });
  assert.equal(r.stars, 3);
  assert.match(issueText(r), /AV1/);
});

test('Atmos on a WEB-DL means E-AC3, so it is fine', () => {
  const s = tio('4k', 'Movie.2024.2160p.WEB-DL.Atmos.DV.HDR.H.265-XYZ\n👤 90 💾 15.00 GB ⚙️ X', 'Movie.2024.2160p.WEB-DL.Atmos.DV.HDR.H.265-XYZ.mkv');
  assert.equal(rate(s, { profile: PROFILE, runtimeMin: 120 }).stars, 5);
});

test('a lossless track next to a lossy one is risky: the player may start on it', () => {
  const s = tio('4k', 'Movie.2024.2160p.BluRay.x265.HDR.TrueHD.7.1.Atmos.AC3.5.1-XYZ\n👤 90 💾 15.00 GB ⚙️ X', 'Movie.2024.2160p.BluRay.x265.HDR.TrueHD.7.1.Atmos.AC3.5.1-XYZ.mkv');
  const r = rate(s, { profile: PROFILE, runtimeMin: 120 });
  assert.equal(r.stars, 2);
  assert.match(issueText(r), /TrueHD/);
});

test('AC3 right after a dash still counts as AC3', () => {
  const s = tio('4k', 'Obsession (2025) (Spanish English Subs) Micro4K 2160P Sdr x265-Ac3 Aac\n👤 27 💾 5.11 GB ⚙️ ThePirateBay', 'Obsession (2025) Micro4K 2160P Sdr x265-Ac3 Aac By Chinak.mkv');
  const r = rate(s, { profile: PROFILE, runtimeMin: 109 });
  assert.equal(r.stars, 5, issueText(r));
  assert.ok(r.summary.includes('AC3'), r.summary.join(' · '));
});

test('only a Blu-ray remux implies Dolby Vision profile 7', () => {
  const rife = tio('4k DV | HDR', 'Obsession.2025.2160p.60fps.AI.RIFE.DV.HDR10.DDP.Atmos.Multi-Lang…\n👤 46 💾 16.37 GB ⚙️ TorrentGalaxy',
    'Obsession.2025.2160p.60fps.AI.RIFE.DV.HDR10.DDP.Atmos.Multi-Lang.REMUX.HEVC-Wood.mkv');
  const encode = tio('4k DV | HDR', 'Obsession.2025.4K.HDR.DV.2160p.BDRemux Ita Eng x265-NAHOM\n👤 30 💾 15.00 GB ⚙️ X',
    'Obsession.2025.4K.HDR.DV.2160p.BDRemux Ita Eng x265-NAHOM.mkv');
  const uhd = tio('4k DV | HDR', 'Обсессия / Obsession [2025 UHD BDRemux 2160p HDR10 Dolby Vision] Dub\n👤 50 💾 51.87 GB ⚙️ Rutracker', '');
  assert.doesNotMatch(issueText(rate(rife, { profile: PROFILE })), /profile 7/);
  assert.match(issueText(rate(rife, { profile: PROFILE })), /interpolated/);
  assert.doesNotMatch(issueText(rate(encode, { profile: PROFILE })), /profile 7/);
  assert.match(issueText(rate(uhd, { profile: PROFILE })), /profile 7/);
});

test('DTS-HD MA written as one word is still DTS-HD MA', () => {
  for (const tag of ['DTS-HDMA', 'DTSHDMA', 'DTS-HD.MA']) {
    const s = tio('1080p', `Movie.2010.1080p.BluRay.x264.${tag}.5.1-XYZ\n👤 50 💾 9.00 GB ⚙️ X`, `Movie.2010.1080p.BluRay.x264.${tag}.5.1-XYZ.mkv`);
    assert.equal(rate(s, { profile: PROFILE, runtimeMin: 120 }).stars, 1, tag);
  }
});

test('"Mel" or "Fel" in a title is not a Dolby Vision layer', () => {
  const s = tio('4k DV | HDR', 'Mel.Brooks.Spaceballs.1987.2160p.UHD.BluRay.DV.HDR.DDP5.1.x265-XYZ\n👤 50 💾 15.00 GB ⚙️ X', 'Mel.Brooks.Spaceballs.1987.2160p.UHD.BluRay.DV.HDR.DDP5.1.x265-XYZ.mkv');
  assert.doesNotMatch(issueText(rate(s, { profile: PROFILE, runtimeMin: 96 })), /profile 7/);
  const fel = tio('4k DV', 'Movie.2020.2160p.UHD.BluRay.DV.FEL.HDR.DDP5.1.x265-XYZ\n👤 50 💾 15.00 GB ⚙️ X', 'Movie.2020.2160p.UHD.BluRay.DV.FEL.HDR.DDP5.1.x265-XYZ.mkv');
  assert.match(issueText(rate(fel, { profile: PROFILE, runtimeMin: 96 })), /profile 7/);
});

test('"10-Bit" H.264 is 10-bit too', () => {
  const s = tio('1080p', 'Movie.2020.1080p.WEB-DL.AAC.x264.10-Bit-XYZ\n👤 50 💾 3.00 GB ⚙️ X', 'Movie.2020.1080p.WEB-DL.AAC.x264.10-Bit-XYZ.mkv');
  assert.equal(rate(s, { profile: PROFILE, runtimeMin: 100 }).stars, 2);
});

test('debrid streams keep their [RD+] tag and are not judged by peers', () => {
  const s = {
    name: '[RD+] Torrentio\n4k DV | HDR',
    title: 'Movie.2026.2160p.WEB-DL.DDP5.1.DV.HDR.H.265-XYZ\n👤 0 💾 15.00 GB ⚙️ X',
    url: 'https://example.invalid/resolve/realdebrid/x',
    behaviorHints: { filename: 'Movie.2026.2160p.WEB-DL.DDP5.1.DV.HDR.H.265-XYZ.mkv' },
  };
  const r = rate(s, { profile: PROFILE, runtimeMin: 120 });
  assert.equal(r.stars, 5, issueText(r));
  assert.equal(decorate(s, r).name, '★★★★★ [RD+]\n4k DV | HDR');
});

test('one malformed upstream stream is passed through, not fatal to the list', () => {
  const odd = { name: 42, title: { x: 1 }, infoHash: hash('f') };
  const out = adviseStreams([null, odd, ACCID], { profile: PROFILE, runtimeMin: 65 });
  assert.equal(out.length, 2);
  assert.equal(out[0], odd);
  assert.match(out[1].name, /^★★★★★/);
});

// ------------------------------------------------------------------ verified facts win

test('verified: Ben The Men MP4 with hidden subtitle tracks rates 4 stars', () => {
  const r = rate(BTM_MP4, { profile: PROFILE, runtimeMin: 109, facts: FACTS.btm });
  assert.equal(r.verified, true);
  assert.equal(r.stars, 4, issueText(r));
  assert.match(issueText(r), /subtitles hidden/i);
});

test('verified: an MP4 with nothing hidden rates 5 stars', () => {
  const r = rate(BTM_MP4, { profile: PROFILE, runtimeMin: 109, facts: { ...FACTS.btm, mp4SubsHidden: 0 } });
  assert.equal(r.stars, 5, issueText(r));
});

test('verified: r00t plays TrueHD first, so 1 star', () => {
  const r = rate(R00T, { profile: PROFILE, runtimeMin: 109, facts: FACTS.r00t });
  assert.equal(r.stars, 1);
  assert.match(issueText(r), /TrueHD/);
});

test('verified: a TrueHD track that is not the default only earns a note', () => {
  const [truehd, ac3] = FACTS.r00t.audio;
  const facts = { ...FACTS.r00t, audio: [{ ...ac3, default: true }, { ...truehd, default: false }] };
  const r = rate(R00T, { profile: PROFILE, runtimeMin: 109, facts });
  assert.equal(r.stars, 5, issueText(r));
  assert.ok(r.notes.some((n) => /TrueHD/.test(n)), r.notes.join(' | '));
});

test('verified: BluDV (DV P8, E-AC3, 26 Mbps) rates 5 stars even though its name hides the audio codec', () => {
  const r = rate(BLUDV, { profile: PROFILE, runtimeMin: 109, facts: FACTS.bludv });
  assert.equal(r.stars, 5, issueText(r));
  assert.equal(r.bitrateMbps, 26.1);
  assert.equal(r.estimated, false);
});

test('verified: FraMeSToR (DV P7 FEL, DTS-HD MA, 59.5 Mbps) rates 1 star', () => {
  const r = rate(FRAMESTOR, { profile: PROFILE, runtimeMin: 65, facts: FACTS.framestor });
  assert.equal(r.stars, 1);
  assert.match(issueText(r), /profile 7/);
  assert.match(issueText(r), /DTS-HD/);
});

// ------------------------------------------------------------------ device profile

test('a profile override changes the verdict (a TV that decodes TrueHD)', () => {
  const profile = mergeProfile(PROFILE, { audio: { truehd: null } });
  assert.equal(rate(R00T, { profile, runtimeMin: 109 }).stars, 5);
  assert.equal(PROFILE.audio.truehd.cap, 1, 'the default profile is not mutated');
});

// ------------------------------------------------------------------ display

test('decorate puts stars in the name and reasons above Torrentio\'s own text', () => {
  const out = decorate(R00T, rate(R00T, { profile: PROFILE, runtimeMin: 109 }));
  assert.equal(out.name, '★☆☆☆☆\n4k DV | HDR');
  const [first, ...rest] = out.title.split('\n');
  assert.match(first, /^✗ TrueHD/);
  assert.equal(rest.join('\n'), R00T.title);
  assert.equal(out.infoHash, R00T.infoHash);
  assert.equal(out.fileIdx, R00T.fileIdx);
  assert.deepEqual(out.behaviorHints, R00T.behaviorHints);
});

test('decorate summarises a good stream and marks verified facts', () => {
  const r = rate(BLUDV, { profile: PROFILE, runtimeMin: 109, facts: FACTS.bludv });
  const first = decorate(BLUDV, r).title.split('\n')[0];
  assert.equal(first, '✓ verified · MKV · E-AC3 · DV P8 · 26 Mbps · 173 peers');
});

test('decorate keeps a description field when the upstream uses one', () => {
  const s = { name: 'MediaFusion\n4K', description: 'Movie.2024.2160p.WEB-DL.DDP5.1.H.265-XYZ\n👤 40 💾 10 GB', infoHash: hash('c') };
  const out = decorate(s, rate(s, { profile: PROFILE, runtimeMin: 120 }));
  assert.equal(out.name, '★★★★★\n4K');
  assert.equal(out.title, undefined);
  assert.match(out.description, /^✓ MKV|^✓ E-AC3|^✓ /);
});

test('adviseStreams keeps the upstream order unless asked to sort by rating', () => {
  const list = [R00T, ACCID, web(3)];
  const ctx = { profile: PROFILE, runtimeMin: 100, factsFor: () => null };
  assert.deepEqual(adviseStreams(list, ctx).map((s) => s.name[0]), ['★', '★', '★']);
  assert.deepEqual(adviseStreams(list, ctx).map((s) => s.infoHash), list.map((s) => s.infoHash));
  const sorted = adviseStreams(list, { ...ctx, sort: 'rating' });
  assert.deepEqual(sorted.map((s) => s.infoHash), [ACCID, web(3), R00T].map((s) => s.infoHash));
});
