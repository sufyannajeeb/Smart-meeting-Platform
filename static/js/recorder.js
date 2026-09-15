/**
 * SmartMeet AI — MediaRecorder upload on stop / page leave
 *
 * Multi-participant conversation recording.
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

  function getLocalStream() {
    return window.SMARTMEET_LOCAL_STREAM;
  }

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
    if (type === "low-level") {
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

    // Audio already flowing on this connection
    pc.getReceivers()
      .filter((r) => r.track && r.track.kind === "audio")
      .forEach((r) => addAudioTrackToMix(r.track, true));

    // Audio arriving later (new participant, renegotiation)
    pc.addEventListener("track", (e) => {
      if (e.track && e.track.kind === "audio") addAudioTrackToMix(e.track, true);
    });
  }

  function attachAllKnownPeers() {
    if (!window.SMARTMEET_PEERS) return;
    Object.values(window.SMARTMEET_PEERS).forEach(attachPeerAudio);
  }

  function buildMixedStream(localStream) {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    audioCtx = new AudioCtx();
    // Resume in case the browser created it in a "suspended" state
    // (some autoplay policies do this for contexts created outside a user
    // gesture — we're inside the Record click, but be safe anyway).
    if (audioCtx.state === "suspended" && audioCtx.resume) {
      audioCtx.resume().catch(() => {});
    }
    destNode = audioCtx.createMediaStreamDestination();
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

    const out = new MediaStream();
    const videoTrack = localStream.getVideoTracks()[0];
    if (videoTrack) out.addTrack(videoTrack);

    // Local mic
    localStream.getAudioTracks().forEach((t) => addAudioTrackToMix(t, false));

    // Every remote participant currently connected
    attachAllKnownPeers();

    // New peers joining mid-recording — poll lightly
    peerPollInterval = setInterval(attachAllKnownPeers, 1500);

    destNode.stream.getAudioTracks().forEach((t) => out.addTrack(t));
    return out;
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
        alert("Wait for camera to be ready before recording.");
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