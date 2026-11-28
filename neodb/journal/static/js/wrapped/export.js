// The export worker of the annual summary: the page draws each card into a bitmap (only the page can draw an SVG) and
// sends it here once; this worker keeps it as a PNG and makes the ZIP, the GIF and the MP4, so the page stays responsive.
// Messages in: {type: "card", key, bitmap}, {type: "probe", id}, {type: "export", id, kind: "zip" | "gif" | "mp4", items, lib}.
// Messages out, each with the id of its request: {type: "probe", mp4}, {type: "progress", done, total}, {type: "export", blob, ...},
// {type: "error", message}.
// a card is 4:5
const W = 1080, H = 1350;
// each card's PNG, by a key the page builds from the card id and its drawing; a promise, as messages arrive while one is still encoding
const pngs = new Map();

async function toPng(bitmap) {
  const c = new OffscreenCanvas(W, H);
  c.getContext("2d").drawImage(bitmap, 0, 0, W, H);
  bitmap.close();
  return c.convertToBlob({ type: "image/png" });
}

async function pngOf(key) {
  const p = pngs.get(key);
  if (!p) throw new Error(`card ${key} was not sent`);
  return p;
}

// ---------- zip: no compression, as PNG is compressed already; names are UTF-8 ----------
const CRC = new Uint32Array(256).map((z, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(u8) {
  let c = 0xffffffff;
  for (let i = 0; i < u8.length; i++) c = CRC[(c ^ u8[i]) & 255] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
async function zip(m, progress) {
  const now = new Date(), time = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1), date = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
  const enc = new TextEncoder(), parts = [], central = [];
  let offset = 0;
  for (let i = 0; i < m.items.length; i++) {
    // the bytes only for the checksum; the archive holds the PNG blob itself, so no second copy stays in memory
    const png = await pngOf(m.items[i].key), size = png.size, name = enc.encode(m.items[i].name), crc = crc32(new Uint8Array(await png.arrayBuffer()));
    const local = new DataView(new ArrayBuffer(30));
    [[0, 0x04034b50, 4], [4, 20, 2], [6, 0x0800, 2], [8, 0, 2], [10, time, 2], [12, date, 2], [14, crc, 4], [18, size, 4], [22, size, 4], [26, name.length, 2], [28, 0, 2]].forEach(([at, v, n]) => n === 4 ? local.setUint32(at, v, true) : local.setUint16(at, v, true));
    const head = new DataView(new ArrayBuffer(46));
    [[0, 0x02014b50, 4], [4, 20, 2], [6, 20, 2], [8, 0x0800, 2], [10, 0, 2], [12, time, 2], [14, date, 2], [16, crc, 4], [20, size, 4], [24, size, 4], [28, name.length, 2], [30, 0, 2], [32, 0, 2], [34, 0, 2], [36, 0, 2], [38, 0, 4], [42, offset, 4]].forEach(([at, v, n]) => n === 4 ? head.setUint32(at, v, true) : head.setUint16(at, v, true));
    parts.push(local, name, png);
    central.push(head, name);
    offset += 30 + name.length + size;
    progress(i + 1);
  }
  const size = central.reduce((s, p) => s + p.byteLength, 0), end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, m.items.length, true); end.setUint16(10, m.items.length, true); end.setUint32(12, size, true); end.setUint32(16, offset, true);
  return { blob: new Blob([...parts, ...central, end], { type: "application/zip" }) };
}

// ---------- gif: full size, 256 colours for each frame, 2.5 s a card ----------
async function gif(m, progress) {
  const { GIFEncoder, quantize, applyPalette } = await import(m.lib.gif);
  const g = GIFEncoder(), c = new OffscreenCanvas(W, H), cx = c.getContext("2d", { willReadFrequently: true });
  for (let i = 0; i < m.items.length; i++) {
    const bmp = await createImageBitmap(await pngOf(m.items[i].key));
    cx.drawImage(bmp, 0, 0); bmp.close();
    const data = cx.getImageData(0, 0, W, H).data, palette = quantize(data, 256), index = applyPalette(data, palette);
    g.writeFrame(index, W, H, { palette, delay: 2500 });
    progress(i + 1);
  }
  g.finish();
  return { blob: new Blob([g.bytes()], { type: "image/gif" }), frames: m.items.length };
}

// ---------- mp4: H.264 with the browser's encoder; a card holds 2.1 s, then fades into the next in 0.4 s ----------
// A frame is written only when the picture changes: one long frame for a card at rest, 30 a second in a fade.
const HOLD = 2.1e6, FADE = 12, STEP = 1e6 / 30, PERIOD = HOLD + FADE * STEP;
async function mp4Config() {
  if (typeof VideoEncoder === "undefined") return null;
  // High, Main, then Baseline profile, all at level 4.0, which takes 1080 x 1350 at 30 frames a second
  for (const codec of ["avc1.640028", "avc1.4d0028", "avc1.42e028"]) {
    const config = { codec, width: W, height: H, bitrate: 6e6, framerate: 30, avc: { format: "avc" } };
    try { if ((await VideoEncoder.isConfigSupported(config)).supported) return config; } catch (e) { /* try the next profile */ }
  }
  return null;
}
// an encoder that gives no output for this long has stopped (the iOS simulator does this with H.264 High), so the page shows an
// error and does not wait forever; the page turns the message "stalled" into its own words
const STALL = 15000;
const close = c => { try { if (c.state !== "closed") c.close(); } catch (e) { /* closed already */ } };
// wait while the encoder's queue is full; a short queue keeps memory low, and the encoder is far faster than the drawing anyway
async function room(enc, st) {
  while (enc.encodeQueueSize > 8) {
    if (st.failure) throw st.failure;
    if (performance.now() - st.last > STALL) throw new Error("stalled");
    await new Promise(r => setTimeout(r, 1));
  }
  if (st.failure) throw st.failure;
}
async function flushed(enc, st) {
  let timer;
  try { await Promise.race([enc.flush(), new Promise((r, fail) => { timer = setTimeout(() => fail(new Error("stalled")), STALL); })]); } finally { clearTimeout(timer); }
  if (st.failure) throw st.failure;
}
// the chosen music as AAC beside the video: 48 kHz stereo at 128 kbps, if this browser can encode it
const AAC = { codec: "mp4a.40.2", sampleRate: 48000, numberOfChannels: 2, bitrate: 128000 };
async function aacOk() {
  try { return typeof AudioEncoder !== "undefined" && (await AudioEncoder.isConfigSupported(AAC)).supported; } catch (e) { return false; }
}
// the track repeats to fill the video; a short fade at each repeat avoids a click, and the last two seconds fade out
async function encodeMusic(pcm, total, muxer) {
  const st = { failure: null, last: performance.now() };
  const enc = new AudioEncoder({ output: (chunk, meta) => { st.last = performance.now(); muxer.addAudioChunk(chunk, meta); }, error: e => { st.failure = e; } });
  try {
    enc.configure(AAC);
    const rate = AAC.sampleRate, len = pcm.left.length, all = Math.round(total / 1e6 * rate), seam = Math.round(rate * 0.02), tail = rate * 2, step = rate;
    for (let at = 0; at < all; at += step) {
      const n = Math.min(step, all - at), data = new Float32Array(n * 2);
      for (let i = 0; i < n; i++) {
        const k = at + i, s = k % len, g = Math.min(1, s / seam, (len - s) / seam, (all - k) / tail, k / seam);
        data[i] = pcm.left[s] * g; data[n + i] = pcm.right[s] * g;
      }
      await room(enc, st);
      const frame = new AudioData({ format: "f32-planar", sampleRate: rate, numberOfFrames: n, numberOfChannels: 2, timestamp: Math.round(at / rate * 1e6), data });
      enc.encode(frame); frame.close();
    }
    await flushed(enc, st);
  } finally { close(enc); }
}
async function mp4(m, progress) {
  const config = await mp4Config();
  if (!config) throw new Error("this browser cannot encode H.264");
  const { Muxer, ArrayBufferTarget } = await import(m.lib.mp4);
  const music = !!(m.audio && m.audio.left && m.audio.left.length) && await aacOk();
  const muxer = new Muxer({ target: new ArrayBufferTarget(), video: { codec: "avc", width: W, height: H }, ...(music ? { audio: { codec: "aac", sampleRate: AAC.sampleRate, numberOfChannels: 2 } } : {}), fastStart: "in-memory" });
  const st = { failure: null, last: performance.now() };
  const encoder = new VideoEncoder({ output: (chunk, meta) => { st.last = performance.now(); muxer.addVideoChunk(chunk, meta); }, error: e => { st.failure = e; } });
  const c = new OffscreenCanvas(W, H), cx = c.getContext("2d");
  const push = async (timestamp, duration, keyFrame) => {
    await room(encoder, st);
    const frame = new VideoFrame(c, { timestamp: Math.round(timestamp), duration: Math.round(duration) });
    encoder.encode(frame, { keyFrame });
    frame.close();
  };
  const n = m.items.length;
  let cur = null, next = null;
  try {
    encoder.configure(config);
    next = await createImageBitmap(await pngOf(m.items[0].key));
    for (let i = 0; i < n; i++) {
      const t0 = i * PERIOD, last = i === n - 1;
      cur = next; next = last ? null : await createImageBitmap(await pngOf(m.items[i + 1].key));
      cx.globalAlpha = 1; cx.drawImage(cur, 0, 0);
      // the muxer gives the last frame of the file one step, so the last card ends with a copy of itself and holds its full time
      await push(t0, last ? PERIOD - STEP : HOLD, true);
      if (last) await push(t0 + PERIOD - STEP, STEP, false);
      else {
        for (let j = 0; j < FADE; j++) {
          cx.globalAlpha = 1; cx.drawImage(cur, 0, 0);
          cx.globalAlpha = (j + 1) / (FADE + 1); cx.drawImage(next, 0, 0);
          await push(t0 + HOLD + j * STEP, STEP, false);
        }
      }
      cur.close(); cur = null;
      progress(i + 1);
    }
    await flushed(encoder, st);
  } finally { close(encoder); if (cur) cur.close(); if (next) next.close(); }
  if (music) await encodeMusic(m.audio, n * PERIOD, muxer);
  muxer.finalize();
  return { blob: new Blob([muxer.target.buffer], { type: "video/mp4" }), seconds: Math.round(n * PERIOD / 1e5) / 10, music };
}

const EXPORT = { zip, gif, mp4 };
self.onmessage = async e => {
  const m = e.data;
  // a card that fails to encode is dropped, so a later export with it sent again encodes it again
  if (m.type === "card") { const p = toPng(m.bitmap); p.catch(() => { if (pngs.get(m.key) === p) pngs.delete(m.key); }); pngs.set(m.key, p); return; }
  try {
    if (m.type === "probe") { postMessage({ type: "probe", id: m.id, mp4: !!(await mp4Config()) }); return; }
    if (m.type === "export") {
      // only the cards of this export stay; a card drawn again after an edit has a new key
      const keep = new Set(m.items.map(x => x.key));
      [...pngs.keys()].forEach(k => { if (!keep.has(k)) pngs.delete(k); });
      const out = await EXPORT[m.kind](m, done => postMessage({ type: "progress", id: m.id, done, total: m.items.length }));
      postMessage({ type: "export", id: m.id, ...out });
    }
  } catch (err) {
    postMessage({ type: m.type === "probe" ? "probe" : "error", id: m.id, mp4: false, message: String((err && err.message) || err) });
  }
};
