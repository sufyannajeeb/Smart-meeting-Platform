/**
 * SmartMeet AI — MediaRecorder upload on stop / page leave
 *
 * Multi-participant conversation recording.
 *
 * VIDEO (split screen of everyone, not just the recorder):
 *   Recording the local stream directly saves ONE camera — the person who
 *   pressed "Record" — even though every peer's camera is on screen. Remote
 *   video never lives in window.SMARTMEET_LOCAL_STREAM; it arrives as a track
 *   on the RTCPeerConnection(s).
 *   So every participant (local camera + every remote peer, falling back to
 *   the raw incoming WebRTC track when a tile is missing) is painted onto a
 *   canvas in a responsive grid — 2 participants ⇒ side-by-side split screen,
 *   3 ⇒ three columns, 4 ⇒ 2x2 — and canvas.captureStream() is fed to
 *   MediaRecorder. Frames are drawn "contained" (aspect ratio preserved) so
 *   the entire person is always visible. Peers joining/leaving mid-recording
 *   are re-collected on every painted frame.
 *
 * PROBLEM (one-sided audio):
 *   MediaRecorder can only record what's actually inside the MediaStream
 *   you hand it. window.SMARTMEET_LOCAL_STREAM only ever contains *this
 *   browser's* mic + camera — the other participant's audio arrives
 *   separately, as a track on the RTCPeerConnection(s). If we recorded the
 *   local stream alone, whoever hits "Record" would only capture their own
 *   voice.
 *
 * FIX:
 *   We build a *mixed* audio stream with the Web Audio API: every audio
 *   track (local mic + every connected remote peer) is piped into a single
 *   MediaStreamAudioDestinationNode. Each user records that COMBINED
 *   conversation — so both (all) sides of the conversation are captured and
 *   the transcript contains everyone's speech.
 *
 * ADDITIONALLY:
 *   - Audio level monitoring via an AnalyserNode detects when no voice /
 *     and too-quiet audio is being captured, and shows a warning popup.
 *   - MediaRecorder uses the best supported MIME type instead of hardcoding
 *     one that may be rejected in some browsers.
 *   - New peers who join mid-recording are picked up by polling (no event
 *     is exposed globally when a new RTCPeerConnection is created).
 *   - On page leave, any in-progress recording is stopped+uploaded.
 */
(function () {
  const cfg = window.SMARTMEET;
  if (!cfg) return;

  const btnRecord = document.getElementById("btn-record");
  let mediaRecorder = null;
  let chunks = [];
  let recording = false;
  let startTime = null;

  // ---- Audio mixing state (created fresh each time recording starts) ----
  let audioCtx = null;
  let destNode = null;
  let analyser = null;        // combined mix level
  let localAnalyser = null;   // local-mic-only level
  let remoteAnalyser = null;  // remote-participant-only level
  let mixedStream = null;
  let sourceNodes = new Map(); // track.id -> MediaStreamAudioSourceNode
  let peerListenerAttached = new WeakSet();
  let peerPollInterval = null;
  let remoteSourceCount = 0;  // audio sources currently mixed from remote peers

  // ---- Audio quality monitor state ----
  let levelMeterInterval = null;
  let silentSeconds = 0;   // consecutive seconds with essentially no signal
  let quietSeconds = 0;    // consecutive seconds below clear-speech level
  let remoteEverHeard = false; // has ANY remote audio ever risen above noise
  const NOISE_FLOOR = 0.008;      // RMS below this = nothing is being captured
  const CLEAR_SPEECH = 0.02;      // RMS below this = voice present but unclear
  const WARN_QUIET_SECS = 5;      // warn when voice has been unclear/silent 5s
  const WARN_NOTHING_SECS = 3;    // warn fast if no audio source is in the mix
  const WARN_REMOTE_QUIET_SECS = 30; // remote silence this long = side missing
  let warnedAboutLevel = false;
  let warnCooldownUntil = 0;
  const WARN_COOLDOWN_MS = 20000; // don't visibly spam the user
  let isUploading = false; // guard against duplicate uploads

  // ---- Composite video state (every participant on screen) ----
  const COMPOSITE_W = 1280;      // fixed 720p keeps MP4 conversion predictable
  const COMPOSITE_H = 720;
  const COMPOSITE_FPS = 20;
  const TILE_PAD = 8;
  const TILE_RADIUS = 10;
  let compositeCtx = null;        // 2d context of the recording canvas
  let compositeStream = null;     // canvas.captureStream() result
  let compositeLocalStream = null;
  let compositeHiddenVideo = null; // fallback <video> when #local-video is idle
  let compositeIntervalId = null;  // steady paint pump (fps-capped)
  let compositeLastPaint = 0;
  let lastSourceCount = 0;         // only log when the participant count changes

  function getLocalStream() {
    return window.SMARTMEET_LOCAL_STREAM;
  }

  // ===================== COMPOSITE VIDEO (all participants) =====================

  function hiddenStyle() {
    return (
      "position:fixed;left:-10000px;top:0;width:320px;height:180px;" +
      "opacity:0;pointer-events:none;"
    );
  }

  /**
   * #local-video is the visible tile; it is un-mirrored / detached while the
   * camera is off or a screen share is shown. When it isn't carrying our camera
   * we spin up a hidden <video> bound to the raw local stream so the recording
   * still shows us.
   */
  function resolveLocalVideoEl(localStream) {
    const visible = document.getElementById("local-video");
    if (visible && visible.srcObject && visible.videoWidth > 0) return visible;
    if (!localStream || localStream.getVideoTracks().length === 0) {
      return visible;
    }
    if (!compositeHiddenVideo) {
      compositeHiddenVideo = document.createElement("video");
      compositeHiddenVideo.muted = true;
      compositeHiddenVideo.autoplay = true;
      compositeHiddenVideo.playsInline = true;
      compositeHiddenVideo.setAttribute("playsinline", "");
      compositeHiddenVideo.setAttribute("aria-hidden", "true");
      compositeHiddenVideo.style.cssText = hiddenStyle();
      document.body.appendChild(compositeHiddenVideo);
    }
    if (compositeHiddenVideo.srcObject !== localStream) {
      try {
        compositeHiddenVideo.srcObject = localStream;
      } catch (e) {
        return visible;
      }
    }
    const p = compositeHiddenVideo.play();
    if (p && typeof p.catch === "function") p.catch(() => {});
    return compositeHiddenVideo;
  }

  // Hidden <video> elements bound straight to a peer's incoming video track.
  // Used when the visible tile is missing (late tile creation, DOM change,
  // tile removed) so a connected participant can NEVER silently drop out of
  // the recording — the recording mirrors the WebRTC peers, not the page.
  const hiddenPeerVideos = new Map();

  function remoteVideoEl(uid) {
    const tile = document.getElementById("remote-tile-" + uid);
    const el = document.getElementById("remote-video-" + uid);
    if (tile && el) return el;

    const pc = (window.SMARTMEET_PEERS || {})[uid];
    if (!pc || typeof pc.getReceivers !== "function") return null;
    let track = null;
    try {
      track =
        pc.getReceivers()
          .map((r) => r.track)
          .find((t) => t && t.kind === "video" && t.readyState === "live") || null;
    } catch (e) {
      return null;
    }
    if (!track) return null;

    let v = hiddenPeerVideos.get(uid);
    if (!v) {
      v = document.createElement("video");
      v.muted = true;
      v.autoplay = true;
      v.playsInline = true;
      v.setAttribute("playsinline", "");
      v.setAttribute("aria-hidden", "true");
      v.style.cssText = hiddenStyle();
      document.body.appendChild(v);
      hiddenPeerVideos.set(uid, v);
    }
    const live = v.srcObject ? v.srcObject.getVideoTracks() : [];
    if (!live.some((t) => t.id === track.id)) {
      try {
        v.srcObject = new MediaStream([track]);
      } catch (e) {
        return null;
      }
    }
    const p = v.play();
    if (p && typeof p.catch === "function") p.catch(() => {});
    return v;
  }

  /** Local camera first, then every connected remote peer, in join order. */
  function collectCompositeSources(localStream) {
    const sources = [];
    sources.push({
      key: "__local__",
      el: resolveLocalVideoEl(localStream),
      label: (cfg.username ? cfg.username + " (You)" : "You"),
      mirror: true,
    });

    const peers = window.SMARTMEET_PEERS || {};
    Object.keys(peers).forEach((uid) => {
      const el = remoteVideoEl(uid);
      if (!el) return;
      const tile = document.getElementById("remote-tile-" + uid);
      const nameEl = tile ? tile.querySelector(".tile-name") : null;
      const label = (nameEl && nameEl.textContent ? nameEl.textContent : "Participant").trim();
      sources.push({ key: String(uid), el: el, label: label || "Participant", mirror: false });
    });

    return sources;
  }

  function gridFor(count) {
    if (count <= 1) return [1, 1];
    // Two participants = a clean side-by-side split screen (2 columns, 1 row).
    if (count === 2) return [2, 1];
    if (count === 3) return [3, 1];
    if (count === 4) return [2, 2];
    const cols = Math.min(4, Math.ceil(Math.sqrt(count)));
    return [cols, Math.ceil(count / cols)];
  }

  function roundRectPath(ctx, x, y, w, h, r) {
    const radius = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.lineTo(x + w - radius, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + radius);
    ctx.lineTo(x + w, y + h - radius);
    ctx.quadraticCurveTo(x + w, y + h, x + w - radius, y + h);
    ctx.lineTo(x + radius, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - radius);
    ctx.lineTo(x, y + radius);
    ctx.quadraticCurveTo(x, y, x + radius, y);
    ctx.closePath();
  }

  function fitText(ctx, text, maxWidth) {
    if (ctx.measureText(text).width <= maxWidth) return text;
    let cut = text;
    while (cut.length > 3 && ctx.measureText(cut + "…").width > maxWidth) {
      cut = cut.slice(0, -1);
    }
    return cut + "…";
  }

  function drawTileName(ctx, label, x, y, w, h, fontSize) {
    if (!label) return;
    const barH = Math.round(fontSize * 2.2);
    ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
    ctx.fillRect(x, y + h - barH, w, barH);
    ctx.fillStyle = "#ffffff";
    ctx.font = "600 " + fontSize + "px Inter, 'Segoe UI', sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(
      fitText(ctx, label, w - fontSize * 1.4),
      x + fontSize * 0.7,
      y + h - barH / 2
    );
  }

  function drawCompositeFrame() {
    const ctx = compositeCtx;
    if (!ctx) return;

    ctx.fillStyle = "#0b1220";
    ctx.fillRect(0, 0, COMPOSITE_W, COMPOSITE_H);

    const sources = collectCompositeSources(compositeLocalStream);
    const count = Math.max(1, sources.length);
    if (count !== lastSourceCount) {
      lastSourceCount = count;
      console.log(
        "[recorder] composite participants:",
        count,
        "—",
        sources.map((s) => s.label).join(" | ")
      );
    }
    const grid = gridFor(count);
    const cols = grid[0];
    const rows = grid[1];

    const cellW = (COMPOSITE_W - TILE_PAD * (cols + 1)) / cols;
    const cellH = (COMPOSITE_H - TILE_PAD * (rows + 1)) / rows;
    // Tiles keep their 16:9 shape and are centred inside their cell, so faces
    // are never cropped when a participant joins mid-recording.
    const tileH = Math.min(cellH, (cellW * 9) / 16);
    const tileW = Math.min(cellW, (tileH * 16) / 9);
    const fontSize = Math.max(11, Math.min(30, Math.round(tileH * 0.08)));

    for (let i = 0; i < sources.length; i++) {
      const src = sources[i];
      const row = Math.floor(i / cols);
      const col = i % cols;
      const cellX = TILE_PAD + col * (cellW + TILE_PAD);
      const cellY = TILE_PAD + row * (cellH + TILE_PAD);
      const x = cellX + (cellW - tileW) / 2;
      const y = cellY + (cellH - tileH) / 2;

      const v = src.el;
      const vw = v ? v.videoWidth : 0;
      const vh = v ? v.videoHeight : 0;

      ctx.save();
      roundRectPath(ctx, x, y, tileW, tileH, TILE_RADIUS);
      ctx.fillStyle = "#1f2937";
      ctx.fill();
      ctx.clip();

      if (vw > 0 && vh > 0) {
        // "Contain" the frame inside the tile: the whole person stays in
        // shot with the correct aspect ratio — never stretched sideways and
        // never cropped at the edges. Unused space is letterboxed.
        const scale = Math.min(tileW / vw, tileH / vh);
        const dw = Math.max(1, Math.round(vw * scale));
        const dh = Math.max(1, Math.round(vh * scale));
        const dx = x + Math.round((tileW - dw) / 2);
        const dy = y + Math.round((tileH - dh) / 2);

        ctx.fillStyle = "#000000";
        ctx.fillRect(x, y, tileW, tileH);
        if (src.mirror) {
          // Mirror the local camera, matching what the user sees on their tile.
          ctx.save();
          ctx.translate(dx + dw, dy);
          ctx.scale(-1, 1);
          ctx.drawImage(v, 0, 0, dw, dh);
          ctx.restore();
        } else {
          ctx.drawImage(v, dx, dy, dw, dh);
        }
      } else {
        // Camera off / not connected yet — show an initial so the tile still
        // reads as a participant instead of a black hole in the recording.
        ctx.fillStyle = "#111827";
        ctx.fillRect(x, y, tileW, tileH);
        const initial = ((src.label || "?").trim().charAt(0) || "?").toUpperCase();
        ctx.fillStyle = "#e5e7eb";
        ctx.font = "700 " + Math.round(tileH * 0.32) + "px Inter, 'Segoe UI', sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(initial, x + tileW / 2, y + tileH / 2 - tileH * 0.06);
      }

      drawTileName(ctx, src.label, x, y, tileW, tileH, fontSize);
      ctx.restore();
    }
  }

  function compositeTick() {
    if (!compositeCtx) return;
    try {
      drawCompositeFrame();
    } catch (e) {
      console.warn("SmartMeet recorder: composite frame failed", e);
    }
    compositeLastPaint = performance.now();
  }

  function startCompositePump() {
    if (compositeIntervalId) clearInterval(compositeIntervalId);
    compositeTick(); // paint frame 1 before captureStream() is created
    // A timer keeps painting even in background tabs (where rAF pauses).
    // Frame timestamps come from the wall clock, so a dropped frame never
    // desynchronises the video from the recorded audio.
    compositeIntervalId = setInterval(compositeTick, Math.round(1000 / COMPOSITE_FPS));
  }

  function stopCompositePump() {
    if (compositeIntervalId) {
      clearInterval(compositeIntervalId);
      compositeIntervalId = null;
    }
  }

  /** Returns the composited canvas video track, or null if unsupported. */
  function startCompositeVideo(localStream) {
    const canvas = document.createElement("canvas");
    canvas.width = COMPOSITE_W;
    canvas.height = COMPOSITE_H;
    if (typeof canvas.captureStream !== "function") return null;

    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return null;

    compositeCtx = ctx;
    compositeLocalStream = localStream;
    compositeLastPaint = 0;
    // Debug hook: lets you (or an automated harness) grab the split-screen
    // canvas that MediaRecorder is capturing.
    window.SMARTMEET_CANVAS = canvas;

    startCompositePump(); // paints frame 1 immediately, before the track exists

    const stream = canvas.captureStream(COMPOSITE_FPS);
    compositeStream = stream;
    return stream.getVideoTracks()[0] || null;
  }

  function stopCompositeVideo() {
    stopCompositePump();
    if (compositeStream) {
      compositeStream.getTracks().forEach((t) => {
        try { t.stop(); } catch (e) {}
      });
      compositeStream = null;
    }
    compositeCtx = null;
    compositeLocalStream = null;
    window.SMARTMEET_CANVAS = null;
    lastSourceCount = 0;
    hiddenPeerVideos.forEach((v) => {
      try { v.srcObject = null; } catch (e) {}
      if (v.parentNode) v.parentNode.removeChild(v);
    });
    hiddenPeerVideos.clear();
    if (compositeHiddenVideo) {
      try { compositeHiddenVideo.srcObject = null; } catch (e) {}
      if (compositeHiddenVideo.parentNode) {
        compositeHiddenVideo.parentNode.removeChild(compositeHiddenVideo);
      }
      compositeHiddenVideo = null;
    }
  }

  // =================== END COMPOSITE VIDEO ===================

  function pickMimeType() {
    const candidates = [
      "video/webm;codecs=vp9,opus",
      "video/webm;codecs=vp8,opus",
      "video/webm",
      "video/mp4",
    ];
    if (typeof MediaRecorder === "undefined") return "";
    for (const t of candidates) {
      try { if (MediaRecorder.isTypeSupported(t)) return t; } catch (e) {}
    }
    return "";
  }

  function showWarningPopup(type) {
    // Debounce — only show if we've not warned recently.
    const now = performance.now();
    if (now < warnCooldownUntil) return;
    warnCooldownUntil = now + WARN_COOLDOWN_MS;

    let title, message;
    if (type === "no-composite") {
      title = "Split-screen recording unavailable";
      message =
        "This browser could not composite everyone into one video, so the " +
        "recording will contain ONLY your camera. Open the meeting in " +
        "Chrome or Edge (Chromium) to record every participant side by side.";
    } else if (type === "low-level") {
      title = "Audio quality warning";
      message =
        "The recording is capturing very little / no voice. " +
        "Check your microphone, speak closer to it, and make sure it is not muted. " +
        "Without clear audio the transcript may be empty or inaccurate.";
    } else if (type === "no-remote") {
      title = "Remote audio missing";
      message =
        "No other participant(s) could be heard while recording. " +
        "Only your own voice will appear in the transcript. " +
        "Make sure you can hear the others through your speakers/headset.";
    } else {
      title = "Voice not recognized";
      message =
        "The voice was not recognized clearly. Move closer to the microphone, " +
        "reduce background noise, and try speaking more slowly and clearly.";
    }

    // Try to use the SmartMeet warning banner if we own some DOM for it. Fall
    // back to a native alert.
    const banner = document.getElementById("rec-warning-banner");
    if (banner) {
      banner.querySelector(".rec-warning-title").textContent = title;
      banner.querySelector(".rec-warning-msg").textContent = message;
      banner.classList.add("show");
      banner.setAttribute("aria-hidden", "false");
      const close = banner.querySelector(".rec-warning-close");
      if (close) {
        close.onclick = () => banner.classList.remove("show");
      }
      const wait = document.getElementById("rec-warning-timer");
      if (wait) {
        let s = 8;
        wait.textContent = s + "s";
        const iv = setInterval(() => {
          s--;
          wait.textContent = s + "s";
          if (s <= 0) { clearInterval(iv); banner.classList.remove("show"); }
        }, 1000);
      }
    } else {
      alert(title + "\n\n" + message);
    }
  }

  // Set fill level for the (optional) #rec-level-meter bars. RMS in [0..1]
  // is mapped so quieter speech lights ~2 bars and strong speech fills all 5.
  function updateLevelMeter(rms) {
    const meter = document.getElementById("rec-level-meter");
    if (!meter) return;
    meter.classList.toggle("low", rms < CLEAR_SPEECH);

    const activeBars = Math.max(0, Math.min(5, Math.round(Math.sqrt(rms) * 5)));
    const bars = meter.querySelectorAll(".rec-level-bar");
    bars.forEach((bar, i) => {
      bar.style.opacity = i < activeBars ? "1" : "0.25";
    });
  }

  function addAudioTrackToMix(track, isRemote) {
    if (!track || track.kind !== "audio" || sourceNodes.has(track.id)) return;
    if (!audioCtx || !destNode) return;
    try {
      const src = audioCtx.createMediaStreamSource(new MediaStream([track]));
      // Route into the final mix (what MediaRecorder captures) AND, in
      // parallel, into the combined analyser for live level monitoring.
      src.connect(destNode);
      if (analyser) src.connect(analyser);
      if (isRemote && remoteAnalyser) src.connect(remoteAnalyser);
      if (!isRemote && localAnalyser) src.connect(localAnalyser);
      sourceNodes.set(track.id, src);
      if (isRemote) remoteSourceCount += 1;
      track.addEventListener("ended", () => {
        try { src.disconnect(); } catch (e) {}
        sourceNodes.delete(track.id);
        if (isRemote && remoteSourceCount > 0) remoteSourceCount -= 1;
      });
    } catch (e) {
      console.warn("SmartMeet recorder: could not mix an audio track", e);
    }
  }

  function attachPeerAudio(pc) {
    if (!pc || peerListenerAttached.has(pc)) return;
    peerListenerAttached.add(pc);

    try {
      // Audio already flowing on this connection
      if (typeof pc.getReceivers === "function") {
        pc.getReceivers()
          .filter((r) => r.track && r.track.kind === "audio")
          .forEach((r) => addAudioTrackToMix(r.track, true));
      }

      // Audio arriving later (new participant, renegotiation)
      if (typeof pc.addEventListener === "function") {
        pc.addEventListener("track", (e) => {
          if (e.track && e.track.kind === "audio") addAudioTrackToMix(e.track, true);
        });
      }
    } catch (e) {
      // Never let one peer's audio break the recording (and with it the
      // split-screen video) — skip this peer's audio instead.
      console.warn("SmartMeet recorder: could not attach peer audio", e);
    }
  }

  function attachAllKnownPeers() {
    if (!window.SMARTMEET_PEERS) return;
    Object.values(window.SMARTMEET_PEERS).forEach((pc) => {
      try {
        attachPeerAudio(pc);
      } catch (e) {
        console.warn("SmartMeet recorder: peer audio attach failed", e);
      }
    });
  }

  function buildMixedStream(localStream) {
    const out = new MediaStream();

    // ---- VIDEO: composite EVERY participant (local + every remote peer) onto
    // one canvas, so the saved file shows the whole call in split screen
    // instead of only whoever pressed Record. Falls back to the raw camera
    // track only when the browser genuinely cannot composite.
    let videoTrack = null;
    try {
      videoTrack = startCompositeVideo(localStream);
    } catch (e) {
      console.warn("SmartMeet recorder: composite video unavailable", e);
      stopCompositeVideo();
    }
    if (!videoTrack) {
      console.warn(
        "SmartMeet recorder: composite canvas unavailable — the recording will " +
          "contain ONLY this camera. Use a Chromium-based browser for split-screen recording."
      );
      // Never fail silently: the user must know the saved file will show one
      // person instead of the whole call.
      showWarningPopup("no-composite");
      videoTrack = localStream.getVideoTracks()[0];
    }
    if (videoTrack) out.addTrack(videoTrack);

    // ---- AUDIO: mixed conversation (local mic + every remote peer).
    // Deliberately isolated from the video above: an audio problem must NEVER
    // throw the split-screen VIDEO track away and silently downgrade the
    // recording to a single camera.
    let audioMixOk = false;
    try {
      setupAudioMix();
      audioMixOk = true;
    } catch (e) {
      console.warn("SmartMeet recorder: audio mixing unavailable, using raw mic", e);
      if (peerPollInterval) {
        clearInterval(peerPollInterval);
        peerPollInterval = null;
      }
      teardownAudio();
    }

    if (audioMixOk) {
      // Local mic
      localStream.getAudioTracks().forEach((t) => addAudioTrackToMix(t, false));

      // Every remote participant currently connected
      attachAllKnownPeers();

      // New peers joining mid-recording — poll lightly
      peerPollInterval = setInterval(attachAllKnownPeers, 1500);

      destNode.stream.getAudioTracks().forEach((t) => out.addTrack(t));
    } else {
      // No mixing available — at least record our own microphone.
      localStream.getAudioTracks().forEach((t) => out.addTrack(t));
    }

    return out;
  }

  /** Create the AudioContext graph used for the mixed conversation recording. */
  function setupAudioMix() {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) throw new Error("Web Audio API unavailable");
    audioCtx = new AudioCtx();
    // Resume in case the browser created it in a "suspended" state
    // (some autoplay policies do this for contexts created outside a user
    // gesture — we're inside the Record click, but be safe anyway).
    if (audioCtx.state === "suspended" && audioCtx.resume) {
      audioCtx.resume().catch(() => {});
    }
    destNode = audioCtx.createMediaStreamDestination();

    // KEEP-ALIVE (critical): a MediaStreamDestination with nothing connected
    // to it never emits a single audio frame. Chrome's MediaRecorder then
    // stalls on that track and produces a ZERO-BYTE file — i.e. the whole
    // recording (video included) is silently lost whenever recording starts
    // before any mic/peer audio has been mixed in. A permanently connected,
    // zero-gain oscillator keeps the graph rendering while contributing
    // absolute silence; real sources are mixed in on top of it.
    try {
      const keepAlive = audioCtx.createOscillator();
      const keepAliveGain = audioCtx.createGain();
      keepAliveGain.gain.value = 0;
      keepAlive.connect(keepAliveGain);
      keepAliveGain.connect(destNode);
      keepAlive.start();
    } catch (e) {
      console.warn("SmartMeet recorder: audio keep-alive unavailable", e);
    }

    // AnalyserNodes tap the mixed audio for live level monitoring (each
    // source connects to them in parallel with the destination).
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.6;
    localAnalyser = audioCtx.createAnalyser();
    localAnalyser.fftSize = 512;
    localAnalyser.smoothingTimeConstant = 0.6;
    remoteAnalyser = audioCtx.createAnalyser();
    remoteAnalyser.fftSize = 512;
    remoteAnalyser.smoothingTimeConstant = 0.6;
    sourceNodes = new Map();
    peerListenerAttached = new WeakSet();
    remoteSourceCount = 0;
  }

  /** Dispose the audio graph only — leaves the composite video untouched. */
  function teardownAudio() {
    sourceNodes.forEach((src) => {
      try { src.disconnect(); } catch (e) {}
    });
    sourceNodes = new Map();
    peerListenerAttached = new WeakSet();
    remoteSourceCount = 0;
    silentSeconds = 0;
    quietSeconds = 0;
    remoteEverHeard = false;
    warnedAboutLevel = false;
    analyser = null;
    localAnalyser = null;
    remoteAnalyser = null;
    if (audioCtx) {
      audioCtx.close().catch(() => {});
      audioCtx = null;
    }
    destNode = null;
  }

  function teardownMix() {
    if (levelMeterInterval) {
      clearInterval(levelMeterInterval);
      levelMeterInterval = null;
    }
    if (peerPollInterval) {
      clearInterval(peerPollInterval);
      peerPollInterval = null;
    }
    stopCompositeVideo();
    teardownAudio();
    mixedStream = null;
  }

  function startLevelMonitor() {
    if (levelMeterInterval) clearInterval(levelMeterInterval);
    silentSeconds = 0;
    quietSeconds = 0;
    remoteEverHeard = false;
    warnedAboutLevel = false;
    let monitorTicks = 0;

    function rmsOf(node) {
      const buf = new Float32Array(node.fftSize);
      node.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      return Math.sqrt(sum / buf.length);
    }

    levelMeterInterval = setInterval(() => {
      if (!analyser || !recording) return;
      monitorTicks += 1;
      const rms = rmsOf(analyser);
      const remoteRms = remoteAnalyser ? rmsOf(remoteAnalyser) : 0;

      updateLevelMeter(rms);

      if (remoteRms >= CLEAR_SPEECH) remoteEverHeard = true;

      if (rms < NOISE_FLOOR) {
        silentSeconds += 1;
        quietSeconds += 1;
      } else if (rms < CLEAR_SPEECH) {
        // Mic is live but the voice is very quiet — flag it as unclear
        quietSeconds += 1;
        silentSeconds = 0;
      } else {
        silentSeconds = 0;
        quietSeconds = 0;
      }

      // 1. No audio going into the mix at all (local mic not even present)
      if (sourceNodes.size === 0 && silentSeconds >= WARN_NOTHING_SECS && !warnedAboutLevel) {
        warnedAboutLevel = true;
        showWarningPopup("low-level");
        return;
      }

      // 2. Remote peers exist but we've NEVER heard them after a long stretch.
      //    Both sides should be captured — if they're not, the conversation
      //    transcript will be one-sided (this fixes exactly that class of bug).
      if (
        remoteSourceCount > 0 &&
        monitorTicks >= WARN_REMOTE_QUIET_SECS &&
        !remoteEverHeard &&
        !warnedAboutLevel
      ) {
        warnedAboutLevel = true;
        showWarningPopup("no-remote");
        return;
      }

      // 3. Voice is missing or too quiet/clear — check mic & enunciation.
      if (quietSeconds >= WARN_QUIET_SECS && !warnedAboutLevel) {
        warnedAboutLevel = true;
        showWarningPopup("low-level");
      }
    }, 1000);
  }

  async function uploadBlob(blob, ext) {
    if (isUploading) return;
    isUploading = true;
    try {
      const duration = startTime ? Math.round((Date.now() - startTime) / 1000) : 0;
      const form = new FormData();
      form.append("video", blob, `meeting_${cfg.meetingId}.${ext || "webm"}`);
      form.append("duration", String(duration));
      form.append("target_lang", "hi");

      const res = await fetch(cfg.uploadUrl, {
        method: "POST",
        headers: { "X-CSRFToken": cfg.csrfToken },
        body: form,
      });
      const data = await res.json();
      if (!res.ok) {
        const msg = (data && data.error) ? data.error : "Upload failed (server error)";
        console.error("Upload error:", res.status, msg);
        alert("Recording upload failed: " + msg);
        return;
      }
      if (data.redirect) {
        window.location.href = data.redirect;
      }
    } catch (e) {
      console.error("Upload network error", e);
      alert("Recording upload failed. Check your connection and try again.");
    } finally {
      isUploading = false;
    }
  }

  function rawExtFromMime(mimeType) {
    if (!mimeType) return "webm";
    if (mimeType.indexOf("mp4") !== -1) return "mp4";
    if (mimeType.indexOf("ogg") !== -1) return "webm";
    return "webm";
  }

  function stopAndUpload() {
    if (!mediaRecorder || mediaRecorder.state === "inactive") return;
    mediaRecorder.onstop = async () => {
      const usedMime = mediaRecorder.mimeType || "video/webm";
      const blob = new Blob(chunks, { type: usedMime });
      const ext = rawExtFromMime(usedMime);
      chunks = [];
      teardownMix();
      if (blob.size > 0) {
        try {
          await uploadBlob(blob, ext);
        } catch (e) {
          console.error("Upload failed", e);
          alert("Recording upload failed. Try again from the meeting details page.");
        }
      } else {
        // Never fail silently — an empty file means the recording is lost.
        console.error("SmartMeet recorder: recording produced no data");
        alert(
          "The recording came out empty and could not be saved. " +
            "Please try recording again (check that your camera is on)."
        );
      }
    };
    mediaRecorder.stop();
    recording = false;
    if (btnRecord) {
      btnRecord.classList.remove("danger");
      const iconRec = document.getElementById("icon-rec");
      if (iconRec) { iconRec.className = "bi bi-record-circle"; }
      const tooltipRec = btnRecord.querySelector(".btn-tooltip");
      if (tooltipRec) tooltipRec.textContent = "Start Recording";
      const recIndicator = document.getElementById("rec-indicator");
      if (recIndicator) recIndicator.style.display = "none";
      const levelMeter = document.getElementById("rec-level-meter");
      if (levelMeter) levelMeter.style.display = "none";
    }
  }

  if (btnRecord) {
    btnRecord.addEventListener("click", () => {
      const localStream = getLocalStream();
      if (!localStream) {
        // The join-time getUserMedia failed (permission denied / no device).
        // Retry now instead of just refusing — recording is available to
        // every participant, not only the ones whose camera came up.
        if (window.SMARTMEET_INIT_MEDIA) window.SMARTMEET_INIT_MEDIA();
        alert(
          "Camera/microphone access is needed to record. We've asked for it " +
            "again — allow it, then press Record."
        );
        return;
      }

      if (!recording) {
        chunks = [];

        try {
          mixedStream = buildMixedStream(localStream);
        } catch (e) {
          console.warn(
            "SmartMeet recorder: audio mixing unavailable, falling back to local-only stream",
            e
          );
          teardownMix();
          mixedStream = localStream;
        }

        // Prefer a supported type; fall back to the browser default.
        const mimeType = pickMimeType();
        try {
          mediaRecorder = mimeType
            ? new MediaRecorder(mixedStream, { mimeType })
            : new MediaRecorder(mixedStream);
        } catch (e) {
          console.warn("MediaRecorder with chosen mime failed:", e);
          mediaRecorder = new MediaRecorder(mixedStream);
        }

        mediaRecorder.ondataavailable = (e) => {
          if (e.data.size > 0) chunks.push(e.data);
        };
        mediaRecorder.start(1000);
        startTime = Date.now();
        recording = true;
        startLevelMonitor();
        btnRecord.classList.add("danger");
        const iconRec = document.getElementById("icon-rec");
        if (iconRec) { iconRec.className = "bi bi-stop-circle"; }
        const tooltipRec = btnRecord.querySelector(".btn-tooltip");
        if (tooltipRec) tooltipRec.textContent = "Stop Recording";
        const recIndicator = document.getElementById("rec-indicator");
        if (recIndicator) recIndicator.style.display = "flex";
        const levelMeter = document.getElementById("rec-level-meter");
        if (levelMeter) levelMeter.style.display = "flex";
      } else {
        stopAndUpload();
      }
    });
  }

  window.addEventListener("beforeunload", () => {
    if (recording && mediaRecorder && chunks.length > 0 && !isUploading) {
      try {
        const usedMime = mediaRecorder.mimeType || "video/webm";
        const blob = new Blob(chunks, { type: usedMime });
        if (blob.size > 0) {
          const form = new FormData();
          const ext = rawExtFromMime(usedMime);
          form.append("video", blob, `meeting_${cfg.meetingId}.${ext}`);
          form.append("duration", startTime ? String(Math.round((Date.now() - startTime) / 1000)) : "0");
          form.append("target_lang", "hi");
          form.append("csrfmiddlewaretoken", cfg.csrfToken);
          navigator.sendBeacon(cfg.uploadUrl, form);
        }
      } catch (e) {
        console.warn("beforeunload upload failed", e);
      }
    }
  });

  // Expose recorder controls so the room page can stop+upload before ending a
  // meeting without duplicating recorder state.
  window.SMARTMEET_STOP_RECORDING = function () {
    if (recording) stopAndUpload();
  };
  window.SMARTMEET_IS_RECORDING = function () {
    return recording;
  };
})();