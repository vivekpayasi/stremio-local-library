// Stream Advisor rules: works out what a stream is — from its release name, or from facts the torrent
// server learned by probing the real file — and rates how well it will play on one TV, 1 to 5 stars.
//
// The TV's abilities live in a device profile (device-profile.default.json, with the user's overrides
// merged on top): every rule there is a cap, and a stream's rating is the lowest cap it hits. Pure
// functions only, apart from loading that file.

import fs from 'node:fs';

const PROFILE_FILE = new URL('./device-profile.default.json', import.meta.url);

export const defaultProfile = () => JSON.parse(fs.readFileSync(PROFILE_FILE, 'utf8'));

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

/** Objects merge key by key; anything else (numbers, lists, null) replaces. */
export function mergeProfile(base, over) {
  const out = structuredClone(base);
  for (const [k, v] of Object.entries(over ?? {})) {
    out[k] = isObj(v) && isObj(out[k]) ? mergeProfile(out[k], v) : structuredClone(v);
  }
  return out;
}

// ---------------------------------------------------------------- release names

// A token starts where a word does; '.' and '_' have already been turned into spaces.
const T = (src, flags = '') => new RegExp(`(?<![a-z0-9])${src}`, flags);

const RE = {
  r4320: T('(?:4320p|8k)(?![a-z0-9])'),
  r2160: T('(?:2160p|4k|uhd)(?![a-z0-9])'),
  r1080: T('1080[pi](?![a-z0-9])'),
  r720: T('720p(?![a-z0-9])'),
  dv: T('(?:dv|dovi|dolby vision)(?![a-z])'),
  dvProfile: T('(?:dv|dovi|dolby vision)(?:[ -]?p|[ -]?profile ?|)([578])(?![0-9])'),
  pProfile: T('p([578])(?![0-9a-z])'),
  dualLayer: T('(?:dv|dovi|p7)[ -]?(?:fel|mel)(?![a-z])'),   // "DV.FEL", never the name "Mel"
  hdr10plus: T('hdr10(?:\\+|plus)'),
  hdr: T('hdr(?:10)?(?![a-z0-9])'),
  hlg: T('hlg(?![a-z])'),
  hevc: T('(?:x ?265|h ?265|hevc)(?![0-9])'),
  avc: T('(?:x ?264|h ?264|avc)(?![0-9a-z])'),
  av1: T('av1(?![0-9a-z])'),
  vp9: T('vp9(?![0-9])'),
  vc1: T('vc-?1(?![0-9])'),
  mpeg2: T('mpeg-?2(?![0-9])'),
  bit10: T('(?:10[ -]?bit|hi10p?)(?![a-z])'),
  hi10p: T('hi10p?(?![a-z])'),
  truehd: T('true ?-?hd(?![a-z])'),
  dtsHd: T('(?:dts[ :-]?(?:hd|x)|dtshd)(?:[ -]?(?:ma|hra))?(?![a-z])', 'g'),
  dts: T('dts(?![a-z])'),
  eac3: T('(?:e-?ac-?3|ddp|dd\\+|dolby digital plus)(?![a-z])'),
  ac3: /(?<![a-z0-9])(?<!e-)(?:ac-?3(?![0-9])|dd(?= ?[0-9])|dolby digital(?! plus))/,
  aac: T('aac(?![a-z])'),
  opus: T('opus(?![a-z])'),
  flac: T('flac(?![a-z])'),
  pcm: T('l?pcm(?![a-z])'),
  mp3: T('mp3(?![a-z0-9])'),
  atmos: T('atmos(?![a-z])'),
  web: T('(?:web[ -]?dl|webdl|web[ -]?rip|web|nf|amzn|dsnp|atvp|hmax|pcok)(?![a-z])'),
  bluray: T('(?:blu ?-?ray|bdrip|brrip|bd ?remux|bdmv|remux)(?![a-z])'),
  remux: T('(?:bd)?remux(?![a-z])'),
  discSource: T('(?:blu ?-?ray|bd ?remux|bdmv|uhd)(?![a-z])'),
  x265: T('x ?265(?![0-9])'),
  upscale: T('(?:ai ?-?)?upscale[ds]?(?![a-z])'),
  interpolated: T('(?:rife|interpolat[a-z]*)(?![a-z])'),
  hfr: T('(48|50|60|120) ?fps(?![a-z])'),
};

const CONTAINERS = ['mkv', 'mp4', 'm4v', 'avi', 'm2ts', 'ts', 'mov', 'webm'];
const GIB = 1024 ** 3;
const UNIT = { tb: 1024 * GIB, gb: GIB, mb: GIB / 1024 };

/** What the release name and Torrentio's stats line say about a stream. */
function fromName(stream) {
  const text = stream.title ?? stream.description ?? '';
  const filename = stream.behaviorHints?.filename ?? '';
  const quality = (stream.name ?? '').split('\n').slice(1).join(' ');
  // The stats line ends in the tracker's name ("⚙️ 1337x"), which is not part of the release.
  const words = [text.replace(/⚙️?[^\n]*/g, ''), filename, quality].join(' \n ');
  const s = ` ${words.toLowerCase().replace(/[._]/g, ' ')} `;
  const has = (k) => RE[k].test(s);

  const peers = /👤\s*(\d+)/.exec(text);
  const size = /💾\s*([\d.]+)\s*(tb|gb|mb)/i.exec(text);
  const ext = /\.([a-z0-9]{2,4})$/i.exec(filename)?.[1]?.toLowerCase();
  const container = CONTAINERS.includes(ext) ? ext
    : CONTAINERS.find((c) => T(`${c}(?![a-z0-9])`).test(s)) ?? null;

  const resolution = has('r4320') ? '4320p' : has('r2160') ? '2160p'
    : has('r1080') ? '1080p' : has('r720') ? '720p' : null;

  const codec = has('hevc') ? 'hevc' : has('av1') ? 'av1' : has('avc') || has('hi10p') ? 'avc'
    : has('vp9') ? 'vp9' : has('vc1') ? 'vc1' : has('mpeg2') ? 'mpeg2' : null;

  let dv = null;
  const explicit = has('dv') ? RE.dvProfile.exec(s)?.[1] ?? RE.pProfile.exec(s)?.[1] : null;
  // A straight copy of a Blu-ray. "REMUX" next to an encoder tag (x265) or on a web source is not one.
  const discRemux = has('remux') && has('discSource') && !has('x265');
  if (has('dv')) {
    const hdrBase = has('hdr') || has('hdr10plus');
    dv = explicit ? Number(explicit)
      : has('dualLayer') || discRemux ? 7      // UHD Blu-ray Dolby Vision is dual layer
        : hdrBase ? 8                          // DV with an HDR10 base layer: profile 8
          : 'unknown';
  }

  const audio = [];
  if (has('truehd')) audio.push('truehd');
  if (has('dtsHd')) audio.push('dts-hd');
  if (RE.dts.test(s.replace(RE.dtsHd, ' '))) audio.push('dts');
  for (const k of ['eac3', 'ac3', 'aac', 'opus', 'flac', 'pcm', 'mp3']) if (has(k)) audio.push(k);
  // "Atmos" alone: streaming services carry it in E-AC3, Blu-rays in TrueHD.
  if (!audio.length && has('atmos')) {
    if (has('web') && !has('bluray')) audio.push('eac3');
    else if (has('bluray')) audio.push('truehd');
  }

  return {
    container, resolution,
    video: { codec, bit10: has('bit10') || has('hi10p'), dv, dvStated: Boolean(explicit) },
    hdr: has('hdr10plus') ? 'HDR10+' : has('hdr') ? 'HDR10' : has('hlg') ? 'HLG' : null,
    audio,
    remux: discRemux, upscale: has('upscale'), interpolated: has('interpolated'),
    fps: Number(RE.hfr.exec(s)?.[1] ?? 0) || null,
    peers: peers ? Number(peers[1]) : null,
    sizeBytes: size ? Number(size[1]) * UNIT[size[2].toLowerCase()] : null,
  };
}

// ---------------------------------------------------------------- probed facts

const VIDEO_CODEC = { h264: 'avc', hevc: 'hevc', av1: 'av1', vp9: 'vp9', vc1: 'vc1', mpeg2video: 'mpeg2' };

function audioClass(track) {
  const c = String(track?.codec ?? '').toLowerCase();
  if (c === 'dts') return /hd|x/i.test(track.profile ?? '') ? 'dts-hd' : 'dts';
  if (c.startsWith('pcm')) return 'pcm';
  return c || 'unknown';
}

const AUDIO_LABEL = {
  truehd: 'TrueHD', 'dts-hd': 'DTS-HD MA', dts: 'DTS', eac3: 'E-AC3', ac3: 'AC3', aac: 'AAC',
  opus: 'Opus', flac: 'FLAC', pcm: 'PCM', mp3: 'MP3',
};
const audioLabel = (c) => AUDIO_LABEL[c] ?? c.toUpperCase();

function resolutionOf(v) {
  const w = v?.width ?? 0, h = v?.height ?? 0;
  if (w >= 6000 || h >= 3500) return '4320p';
  if (w >= 3000 || h >= 1600) return '2160p';
  if (w >= 1700 || h >= 900) return '1080p';
  if (w >= 1100 || h >= 600) return '720p';
  return w || h ? 'sd' : null;
}

// ---------------------------------------------------------------- rating

/** A profile entry as {cap, why}, or null when it does not limit anything. */
function ruleOf(v, fallbackWhy) {
  if (v == null) return null;
  const r = typeof v === 'number' ? { cap: v } : v;
  if (typeof r.cap !== 'number' || r.cap >= 5) return null;
  return { cap: r.cap, why: r.why ?? fallbackWhy };
}

const fill = (why, n) => String(why).replaceAll('{n}', n ?? '');

/**
 * Rate one stream. `facts` (from stream-facts.json) replace the name's guesses about the file itself;
 * `runtimeMin` turns the stated size into an estimated bitrate when no measured one exists.
 */
export function rate(stream, { profile, facts = null, runtimeMin = null } = {}) {
  const name = fromName(stream);
  const verified = Boolean(facts);
  const issues = [];
  const notes = [];
  const add = (rule, n) => { if (rule) issues.push({ cap: rule.cap, why: fill(rule.why, n) }); };

  // What the file is: measured when we have facts, guessed from the name otherwise.
  const container = facts?.container ?? name.container;
  const resolution = verified ? resolutionOf(facts.video) ?? name.resolution : name.resolution;
  const codec = verified ? VIDEO_CODEC[facts.video?.codec] ?? facts.video?.codec ?? null : name.video.codec;
  const bit10 = verified ? (facts.video?.bitDepth ?? 8) >= 10 : name.video.bit10;
  const dv = verified ? facts.video?.dv?.profile ?? null : name.video.dv;
  const transfer = facts?.video?.transfer;
  const hdr = !verified ? name.hdr
    : transfer === 'smpte2084' ? (name.hdr === 'HDR10+' ? 'HDR10+' : 'HDR10')
      : transfer === 'arib-std-b67' ? 'HLG' : 'SDR';

  // Container.
  if (verified && container === 'mp4') {
    if (facts.mp4SubsHidden > 0) add(ruleOf(profile.container?.['mp4-subs-hidden'], 'subtitles hidden'));
  } else if (container) {
    add(ruleOf(profile.container?.[container], `${container.toUpperCase()} container`));
  }

  // Video.
  const videoKey = codec === 'avc' && bit10 ? 'avc10' : codec;
  if (videoKey) add(ruleOf(profile.video?.[videoKey], `${videoKey} video`));
  if (dv != null) add(ruleOf(profile.dolbyVision?.[String(dv)], `Dolby Vision profile ${dv}`));
  if (resolution) add(ruleOf(profile.resolution?.[resolution], resolution));

  // Audio: a verified file is judged by the track the player starts on.
  const rulesFor = (c) => ruleOf(profile.audio?.[c], `${audioLabel(c)} audio`);
  if (verified && facts.audio?.length) {
    const first = facts.audio.find((a) => a.default) ?? facts.audio[0];
    add(rulesFor(audioClass(first)));
    const others = new Set(facts.audio.filter((a) => a !== first).map(audioClass));
    for (const c of others) {
      if ((rulesFor(c)?.cap ?? 5) <= 2) notes.push(`also has ${audioLabel(c)} — don't switch to it`);
    }
  } else if (!verified) {
    if (!name.audio.length) add(ruleOf(profile.audio?.unknown, 'audio codec unknown'));
    const good = name.audio.filter((c) => !rulesFor(c));
    const mixed = ruleOf(profile.audio?.mixed, '{n} track may play first');
    for (const c of name.audio) {
      const r = rulesFor(c);
      if (!r) continue;
      // With a playable track alongside, it depends on which one the player picks.
      if (good.length && mixed && r.cap < mixed.cap) add(mixed, audioLabel(c));
      else add(r);
    }
  }

  // Release tags. A remux is only a hint at what is inside: facts say it directly.
  if (!verified && name.remux) add(ruleOf(profile.tags?.remux, 'REMUX'));
  if (name.upscale) add(ruleOf(profile.tags?.upscale, 'AI upscale'));
  if (name.interpolated) add(ruleOf(profile.tags?.interpolated, 'frame-interpolated'));
  else if (name.fps >= 48) add(ruleOf(profile.tags?.hfr, '{n} fps'), name.fps);

  // Bitrate: measured, or the stated size over the title's runtime.
  let bitrateMbps = facts?.bitrateMbps ?? null;
  const estimated = bitrateMbps == null && name.sizeBytes != null && runtimeMin >= 10;
  if (estimated) bitrateMbps = Math.round((name.sizeBytes * 8) / (runtimeMin * 60) / 1e5) / 10;
  if (bitrateMbps != null) {
    const over = [...(profile.bitrate ?? [])].sort((a, b) => b.above - a.above).find((r) => bitrateMbps > r.above);
    add(ruleOf(over), Math.round(bitrateMbps));
  }

  // Swarm: Torrentio's seeder count, which is often stale but still the best early signal. A debrid
  // stream (a URL, no info hash) is served from the debrid service's copy, so the swarm doesn't matter.
  if (name.peers != null && stream.infoHash) {
    const few = [...(profile.peers ?? [])].sort((a, b) => a.atMost - b.atMost).find((r) => name.peers <= r.atMost);
    add(ruleOf(few), name.peers);
  }

  issues.sort((a, b) => a.cap - b.cap);
  const stars = Math.max(1, Math.min(5, ...issues.map((i) => i.cap)));

  // The short "what it is" line shown for a good stream.
  const summary = [];
  if (container) summary.push(container.toUpperCase());
  const lead = verified ? facts.audio?.find((a) => a.default) ?? facts.audio?.[0] : null;
  const audioShown = lead ? audioClass(lead) : name.audio.find((c) => !rulesFor(c)) ?? name.audio[0];
  if (audioShown) summary.push(audioLabel(audioShown));
  // A profile is shown only when measured or stated, not when assumed from the other tags.
  if (dv != null) summary.push(typeof dv === 'number' && (verified || name.video.dvStated) ? `DV P${dv}` : 'DV');
  else if (hdr) summary.push(hdr);
  if (bitrateMbps != null) summary.push(`${estimated ? '~' : ''}${Math.round(bitrateMbps)} Mbps`);
  if (name.peers != null) summary.push(`${name.peers} peers`);

  return { stars, issues, notes, summary, verified, bitrateMbps, estimated };
}

// ---------------------------------------------------------------- display

const starsOf = (n) => '★'.repeat(n) + '☆'.repeat(5 - n);

/**
 * The same stream with its rating shown: stars in the name (the short left column on the TV), and a
 * reasons line above the upstream's own text. Everything that makes it play is left untouched.
 */
export function decorate(stream, r) {
  const lines = (stream.name ?? '').split('\n');
  const rest = lines.length > 1 ? lines.slice(1).join('\n') : lines[0];
  // Torrentio marks debrid streams on the first line ("[RD+] Torrentio"): keep that tag.
  const tag = /^\s*(\[[^\]]+\])/.exec(lines[0])?.[1];
  const stars = tag ? `${starsOf(r.stars)} ${tag}` : starsOf(r.stars);
  const reasons = r.issues.length
    ? r.issues.slice(0, 2).map((i) => `${i.cap <= 2 ? '✗' : '⚠'} ${i.why}`).join(' · ') + (r.verified ? ' · verified' : '')
    : `✓ ${[...(r.verified ? ['verified'] : []), ...r.summary, ...r.notes].join(' · ')}`;
  const field = stream.title == null && stream.description != null ? 'description' : 'title';
  const text = stream[field];
  return {
    ...stream,
    name: rest ? `${stars}\n${rest}` : stars,
    [field]: text ? `${reasons}\n${text}` : reasons,
  };
}

/** Rate and decorate a whole list; `sort: 'rating'` puts the best first (stable within a rating). */
export function adviseStreams(streams, { profile, runtimeMin = null, factsFor = () => null, sort = 'original' } = {}) {
  const rated = streams.filter((s) => s && typeof s === 'object').map((s) => {
    // One stream this code can't read is passed through as it came, not allowed to sink the list.
    try {
      const r = rate(s, { profile, runtimeMin, facts: factsFor(s) });
      return { s: decorate(s, r), stars: r.stars };
    } catch {
      return { s, stars: 0 };
    }
  });
  if (sort === 'rating') rated.sort((a, b) => b.stars - a.stars);
  return rated.map(({ s }) => s);
}
