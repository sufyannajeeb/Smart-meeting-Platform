(function () {
  console.log("[webrtc.js] loaded — build v10-video-fix");
  const cfg = window.SMARTMEET;
  if (!cfg) return;
  console.log("[webrtc.js] iceServers in use:", cfg.iceServers);

  const iceServers = cfg.iceServers || [
    { urls: "stun:stun.l.google.com:19302" },
    { urls: "stun:stun1.l.google.com:19302" },
    { urls: "stun:stun2.l.google.com:19302" },
    { urls: "stun:global.stun.twilio.com:3478" },
    { urls: "stun:stun.cloudflare.com:3478" },
  ];

  const peers = new Map();         // userId -> RTCPeerConnection
  const remoteNames = new Map();   // userId -> username
  const pendingCandidates = new Map(); // userId -> RTCIceCandidate[]
  let localStream = null;
  let ws = null;
  let muted = false;

  window.SMARTMEET_PEERS = {};
  function syncPeersGlobal() {
    const obj = {};
    peers.forEach((pc, id) => { obj[id] = pc; });
    window.SMARTMEET_PEERS = obj;
  }

  const localVideo = document.getElementById("local-video");
  const videoGrid = document.getElementById("video-grid");
  const statusLine = document.getElementById("status-line");
  const participantList = document.getElementById("participant-list");
  const btnMute = document.getElementById("btn-mute");

  function setStatus(msg) {
    if (statusLine) statusLine.textContent = msg;
  }

  function updateParticipantCount() {
    const badge = document.getElementById("participant-count");
    if (badge) {
      const count = participantList ? participantList.children.length : 1;
      badge.textContent = count;
    }
  }

  function addParticipant(userId, name) {
    if (!participantList) { console.warn("addParticipant: no participantList"); return; }
    if (!name) { console.warn("addParticipant: empty name"); return; }
    if (userId != null && document.getElementById("participant-item-" + userId)) {
      console.log("addParticipant: already in list, skipping", name);
      return;
    }
    const li = document.createElement("li");
    li.className = "participant-item";
    if (userId != null) li.id = "participant-item-" + userId;
    li.innerHTML =
      '<div class="participant-item-left">' +
        '<div class="participant-avatar">' + name.charAt(0).toUpperCase() + '</div>' +
        '<div>' +
          '<div class="fw-semibold text-light">' + name + '</div>' +
          '<div class="small text-muted">Participant</div>' +
        '</div>' +
      '</div>' +
      '<div class="participant-status-icons">' +
        '<span class="hand-flag" data-hand-flag-for="' + userId + '" title="Hand raised">&#9995;</span>' +
        '<i class="bi bi-mic-fill"></i>' +
      '</div>';
    participantList.appendChild(li);
    console.log("addParticipant: added", name);
    updateParticipantCount();
  }

  function removeParticipant(userId) {
    if (!participantList || userId == null) return;
    const li = document.getElementById("participant-item-" + userId);
    if (li) li.remove();
    updateParticipantCount();
  }

  function addRemoteVideoTile(userId, userName) {
    if (document.getElementById("remote-tile-" + userId)) return;
    const wrap = document.createElement("div");
    wrap.className = "video-tile-wrap";
    wrap.id = "remote-tile-" + userId;
    const video = document.createElement("video");
    video.id = "remote-video-" + userId;
    video.className = "video-tile";
    video.autoplay = true;
    video.playsInline = true;
    video.setAttribute("playsinline", "");
    video.muted = true;
    const avatar = document.createElement("div");
    avatar.className = "video-avatar-fallback";
    avatar.id = "remote-avatar-" + userId;
    avatar.style.display = "none";
    avatar.innerHTML = '<div class="avatar-circle">' + (userName || "?").charAt(0).toUpperCase() + '</div>';
    const handBadge = document.createElement("div");
    handBadge.className = "tile-hand-badge";
    handBadge.setAttribute("data-hand-badge-for", userId);
    handBadge.title = "Hand raised";
    handBadge.innerHTML = "&#9995;";
    const presentingBadge = document.createElement("div");
    presentingBadge.className = "tile-presenting-badge";
    presentingBadge.setAttribute("data-presenting-badge-for", userId);
    presentingBadge.title = "Presenting";
    presentingBadge.innerHTML = '<i class="bi bi-easel2-fill"></i>';
    const overlay = document.createElement("div");
    overlay.className = "tile-overlay";
    overlay.innerHTML =
      '<span class="tile-mic-icon tile-mic-remote" data-uid="' + userId + '">' +
        '<i class="bi bi-mic-fill"></i>' +
      '</span>' +
      '<span class="tile-name" data-base-name="' + (userName || "?") + '">' + (userName || "?") + '</span>';
    wrap.appendChild(video);
    wrap.appendChild(avatar);
    wrap.appendChild(handBadge);
    wrap.appendChild(presentingBadge);
    wrap.appendChild(overlay);
    videoGrid.appendChild(wrap);
  }

  function removeRemoteVideoTile(userId) {
    const wrap = document.getElementById("remote-tile-" + userId);
    if (wrap) wrap.remove();
  }

  function applyRemoteName(userId, name) {
    if (!name || userId == null) return;
    remoteNames.set(userId, name);
    const initial = name.charAt(0).toUpperCase();

    const tileName = document.querySelector('#remote-tile-' + userId + ' .tile-name');
    if (tileName) tileName.textContent = name;

    const avatarCircle = document.querySelector('#remote-avatar-' + userId + ' .avatar-circle');
    if (avatarCircle) avatarCircle.textContent = initial;

    const participantNameEl = document.querySelector('#participant-item-' + userId + ' .fw-semibold');
    if (participantNameEl) participantNameEl.textContent = name;

    const participantAvatar = document.querySelector('#participant-item-' + userId + ' .participant-avatar');
    if (participantAvatar) participantAvatar.textContent = initial;
  }

  function addMissingLocalTracks(pc) {
    if (!localStream) return 0;
    const kinds = new Set(
      pc.getSenders()
        .map((s) => (s.track ? s.track.kind : null))
        .filter(Boolean)
    );
    let added = 0;
    localStream.getTracks().forEach((track) => {
      if (!kinds.has(track.kind)) {
        try {
          pc.addTrack(track, localStream);
          added += 1;
        } catch (err) {
          console.warn("[pc] addTrack failed for", track.kind, err);
        }
      }
    });
    return added;
  }

  // Build a single stream containing ALL receiver tracks from this PC.
  // This handles the case where ontrack fires with empty e.streams (common
  // after renegotiation or glare recovery) so the video element always gets
  // a stream with both audio and video tracks.
  function buildRemoteStream(pc) {
    const stream = new MediaStream();
    pc.getReceivers().forEach(function (r) {
      if (r.track && r.track.readyState === "live") {
        stream.addTrack(r.track);
      }
    });
    return stream;
  }

  function attachReceiversIfNeeded(pc, remoteId) {
    const videoEl = document.getElementById("remote-video-" + remoteId);
    if (!videoEl) return;
    if (videoEl.srcObject && videoEl.srcObject.getVideoTracks().length > 0) return;
    const stream = buildRemoteStream(pc);
    if (stream && stream.getVideoTracks().length) {
      attachStreamToRemote(remoteId, stream);
    }
  }

  const _pendingAttach = new Set();

  function attachStreamToRemote(remoteId, stream) {
    const videoEl = document.getElementById("remote-video-" + remoteId);
    if (!videoEl) {
      console.warn("[pc] attachStreamToRemote: no video element for", remoteId, "— retrying in 300ms");
      setTimeout(function () { attachStreamToRemote(remoteId, stream); }, 300);
      return;
    }

    // Don't clobber a working video element with an audio-only stream.
    if (videoEl.srcObject) {
      var oldHasLive = videoEl.srcObject.getVideoTracks().some(function (t) { return t.readyState === "live"; });
      var newHasLive = stream.getVideoTracks().some(function (t) { return t.readyState === "live"; });
      if (oldHasLive && !newHasLive) return;
    }

    if (videoEl.srcObject === stream) return;
    if (_pendingAttach.has(remoteId)) {
      setTimeout(function () { attachStreamToRemote(remoteId, stream); }, 200);
      return;
    }
    _pendingAttach.add(remoteId);
    videoEl.srcObject = stream;
    videoEl.muted = true;

    playVideoWithRetry(videoEl, remoteId, 0);
  }

  // Retry play() with increasing delays to work around browser autoplay policy.
  // Most browsers allow autoplay on muted video; if the first play() fails
  // (e.g. user hasn't interacted with the page yet), retry a few times.
  function playVideoWithRetry(videoEl, remoteId, attempt) {
    var MAX_ATTEMPTS = 6;
    var DELAYS = [0, 300, 600, 1000, 2000, 4000];

    videoEl.play().then(function () {
      _pendingAttach.delete(remoteId);
      videoEl._playRetryCycles = 0;
      // Unmute after a short delay so audio doesn't blast
      setTimeout(function () {
        videoEl.muted = false;
        console.log("[pc] remote video playing for", remoteId);
      }, 500);
    }).catch(function (err) {
      console.warn("[pc] play() attempt " + (attempt + 1) + "/" + MAX_ATTEMPTS + " failed for", remoteId, ":", err.message || err);
      if (attempt + 1 < MAX_ATTEMPTS) {
        setTimeout(function () {
          playVideoWithRetry(videoEl, remoteId, attempt + 1);
        }, DELAYS[attempt + 1] || 1000);
      } else {
        console.warn("[pc] remote video play() gave up after " + MAX_ATTEMPTS + " attempts for", remoteId, "— will retry muted");
        _pendingAttach.delete(remoteId);
        // NEVER call videoEl.load() here: load() resets the element and
        // clears srcObject, permanently killing the tile (black video, no
        // audio, and nothing ever re-attaches). Stay muted (autoplay is
        // always allowed for muted video) and start a slower retry cycle
        // that recovers as soon as the browser permits playback.
        videoEl.muted = true;
        videoEl._playRetryCycles = (videoEl._playRetryCycles || 0) + 1;
        if (!videoEl._playRetryScheduled && videoEl._playRetryCycles <= 4) {
          videoEl._playRetryScheduled = true;
          setTimeout(function () {
            videoEl._playRetryScheduled = false;
            if (videoEl.srcObject) playVideoWithRetry(videoEl, remoteId, 0);
          }, 5000);
        }
      }
    });
  }

  // Offers can only be created from the "stable" signaling state. When media
  // shows up mid-negotiation (camera granted late, tracks added while we're
  // answering) we remember the request and flush it the moment we're stable —
  // otherwise that media is silently never negotiated and the far end never
  // receives it.
  async function flushOffer(pc, remoteId) {
    if (!pc._pendingOffer) return;
    if (pc._makingOffer) return;
    if (pc.connectionState === "closed") { pc._pendingOffer = false; return; }
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (pc.signalingState !== "stable") return; // retried on signalingstatechange

    pc._pendingOffer = false;
    pc._makingOffer = true;
    try {
      addMissingLocalTracks(pc);
      var opts = pc._restartAttempts > 0 ? { iceRestart: true } : undefined;
      var offer = await pc.createOffer(opts);
      if (pc.signalingState !== "stable") return; // raced by another offer
      await pc.setLocalDescription(offer);
      ws.send(
        JSON.stringify({
          type: "offer",
          target_id: remoteId,
          sdp: pc.localDescription,
        })
      );
    } catch (err) {
      console.error("[pc] sendOffer failed for", remoteId, err);
    } finally {
      pc._makingOffer = false;
    }
  }

  function sendOffer(pc, remoteId) {
    pc._pendingOffer = true;
    return flushOffer(pc, remoteId);
  }

  function createPeer(remoteId, initiator) {
    if (peers.has(remoteId) || remoteId === cfg.userId) return peers.get(remoteId);

    var name = remoteNames.get(remoteId) || "User " + String(remoteId).slice(0, 6);
    addRemoteVideoTile(remoteId, name);

    var pc = new RTCPeerConnection({ iceServers: iceServers });
    peers.set(remoteId, pc);
    pendingCandidates.set(remoteId, []);
    syncPeersGlobal();

    var MAX_RESTART_ATTEMPTS = 5;
    pc._isInitiator = !!initiator;
    pc._negotiationDone = false;
    pc._restartAttempts = 0;
    pc._pendingOffer = false;   // an offer is requested (flushed when stable)
    pc._makingOffer = false;    // createOffer/setLocalDescription in flight
    pc._ignoreOffer = false;    // discard a remote offer we lost glare against
    pc._candidateCounts = { host: 0, srflx: 0, relay: 0, prflx: 0, other: 0 };

    // Only the INITIATOR adds tracks here — triggers onnegotiationneeded →
    // createOffer so the offer carries our media. The ANSWERING side adds
    // tracks later in handleSignal via addMissingLocalTracks() AFTER
    // setRemoteDescription(offer) so the answer also carries sendrecv media.
    if (initiator && localStream) {
      localStream.getTracks().forEach(function (track) { pc.addTrack(track, localStream); });
    }

    pc.onicecandidate = function (e) {
      if (e.candidate) {
        var t = e.candidate.type || "other";
        pc._candidateCounts[t] = (pc._candidateCounts[t] || 0) + 1;
        console.log(
          "[ICE candidate]", remoteId,
          "type:", t,
          "protocol:", e.candidate.protocol,
          "address:", e.candidate.address,
          "port:", e.candidate.port
        );
        ws.send(
          JSON.stringify({
            type: "ice-candidate",
            target_id: remoteId,
            candidate: e.candidate,
          })
        );
      } else {
        console.log("[ICE candidate] gathering complete for", remoteId, "— totals:", pc._candidateCounts);
        if (pc._candidateCounts.relay === 0) {
          console.warn(
            "[ICE candidate] WARNING: no relay candidates for",
            remoteId, "— direct/STUN paths only."
          );
        }
      }
    };

    pc.ontrack = function (e) {
      console.log("[pc] ontrack fired for", remoteId, "kind:", e.track.kind, "streams:", e.streams.length);

      // Prefer the browser-associated stream, but if empty (common after
      // renegotiation), build a merged stream from ALL live receiver tracks
      // so the video element gets both audio and video in one stream.
      var stream;
      if (e.streams && e.streams[0] && e.streams[0].getTracks().length > 0) {
        stream = e.streams[0];
      } else {
        stream = buildRemoteStream(pc);
        if (!stream) stream = new MediaStream([e.track]);
      }
      attachStreamToRemote(remoteId, stream);
    };

    pc.onconnectionstatechange = function () {
      console.log("[pc] connectionState ->", pc.connectionState, "for", remoteId);

      if (pc.connectionState === "connected") {
        attachReceiversIfNeeded(pc, remoteId);
        setStatus("Connected to " + name);
        // Flush any ICE candidates that arrived before remote description
        flushPendingCandidates(remoteId, pc);
      }

      if (pc.connectionState === "disconnected") {
        setStatus("Reconnecting to " + name + "...");
        return;
      }

      if (pc.connectionState === "failed") {
        if (pc._restartAttempts < MAX_RESTART_ATTEMPTS) {
          pc._restartAttempts += 1;
          console.warn("[pc] connection failed for", remoteId, "— ICE restart attempt", pc._restartAttempts);
          try {
            pc.restartIce();
            return;
          } catch (err) {
            console.error("[pc] restartIce() threw for", remoteId, err);
          }
        }
        console.warn("[pc] giving up on peer", remoteId, "— closing connection");
        pc.close();
        peers.delete(remoteId);
        remoteNames.delete(remoteId);
        pendingCandidates.delete(remoteId);
        removeRemoteVideoTile(remoteId);
        syncPeersGlobal();
        setStatus("Lost connection to " + name);
      }
    };

    pc.oniceconnectionstatechange = function () {
      console.log("[pc] iceConnectionState ->", pc.iceConnectionState, "for", remoteId);
      if (pc.iceConnectionState === "connected" || pc.iceConnectionState === "completed") {
        attachReceiversIfNeeded(pc, remoteId);
      }
    };

    pc.onicegatheringstatechange = function () {
      console.log("[pc] icegatheringstatechange ->", pc.iceGatheringState, "for", remoteId);
    };

    pc.onsignalingstatechange = function () {
      console.log("[pc] signalingState ->", pc.signalingState, "for", remoteId);
      // When we return to stable after receiving an answer, flush buffered
      // ICE candidates that arrived while we were in a mid-negotiation state.
      if (pc.signalingState === "stable") {
        flushPendingCandidates(remoteId, pc);
        // A negotiation was requested while we were busy — honour it now.
        if (pc._pendingOffer) {
          flushOffer(pc, remoteId);
        }
      }
    };

    // ICE stalled — poll and force renegotiation if needed.
    pc._watchdog = setInterval(function () {
      if (pc.connectionState === "connected" || pc.connectionState === "closed") {
        clearInterval(pc._watchdog);
        return;
      }
      if (pc._restartAttempts >= MAX_RESTART_ATTEMPTS) {
        clearInterval(pc._watchdog);
        return;
      }
      if (pc.signalingState !== "stable") return;
      pc._restartAttempts += 1;
      if (!pc._isInitiator) pc._isInitiator = true;
      console.warn(
        "[pc] ICE stuck in", pc.iceConnectionState,
        "— renegotiating with", remoteId, "(attempt", pc._restartAttempts + ")"
      );
      sendOffer(pc, remoteId).catch(function (err) {
        console.error("[pc] stuck-restart offer failed for", remoteId, err);
      });
    }, 6000);

    pc.onnegotiationneeded = async function () {
      // BOTH sides may start a negotiation (perfect negotiation). The answerer
      // side used to bail out here, which meant any media added AFTER we
      // answered — a mic/camera granted late, or a media kind the remote
      // offer didn't contain — was never renegotiated, so the other
      // participants never received it. That is exactly the "joined but not
      // audible/visible" bug.
      if (pc.signalingState !== "stable") {
        // Will be flushed as soon as we return to stable (see below).
        pc._pendingOffer = true;
        return;
      }
      pc._negotiationDone = true;
      try {
        await sendOffer(pc, remoteId);
      } catch (err) {
        pc._negotiationDone = false;
        console.error("[pc] renegotiation/offer error for", remoteId, err);
      }
    };

    // Safety net: if we're the initiator but had no local media when the peer
    // joined (camera/mic not granted yet), still craft an offer — a 'recvonly'
    // offer is enough for the remote's video/audio to reach us, and the
    // backfill in initMedia() renegotiates once our media is ready.
    if (initiator && !localStream) {
      setTimeout(function () {
        if (pc.signalingState === "stable") {
          pc._negotiationDone = false;
          console.log("[pc] initiating recvonly offer to", remoteId, "(local media not ready yet)");
          sendOffer(pc, remoteId).catch(function (err) {
            console.error("[pc] recvonly offer failed for", remoteId, err);
          });
        }
      }, 150);
    }

    return pc;
  }

  // Flush buffered ICE candidates once the PC is ready (remote description set).
  function flushPendingCandidates(remoteId, pc) {
    var buffered = pendingCandidates.get(remoteId);
    if (!buffered || buffered.length === 0) return;
    console.log("[pc] flushing", buffered.length, "buffered ICE candidates for", remoteId);
    var list = buffered.splice(0);
    list.forEach(function (c) {
      pc.addIceCandidate(new RTCIceCandidate(c)).catch(function (e) {
        console.warn("[pc] buffered ICE candidate error for", remoteId, e);
      });
    });
  }

  async function handleSignal(data) {
    console.log("WS recv:", data.type, data);

    if (data.type === "peer-joined") {
      addParticipant(data.user_id, data.user);
      if (data.user_id && data.user_id !== cfg.userId) {
        applyRemoteName(data.user_id, data.user);
        addRemoteVideoTile(data.user_id, data.user);

        // The existing occupant initiates toward the newly-joined peer (the
        // new peer never receives a peer-joined for the existing occupants,
        // only an offer). Simultaneous joins produce glare — handled by the
        // rollback path in the offer handler below.
        console.log("[peer-joined]", data.user, "— creating peer and initiating");
        createPeer(data.user_id, true);
      }
      return;
    }

    if (data.type === "peer-left") {
      removeParticipant(data.user_id);
      var pcLeft = peers.get(data.user_id);
      if (pcLeft) {
        pcLeft.close();
        peers.delete(data.user_id);
        pendingCandidates.delete(data.user_id);
        syncPeersGlobal();
      }
      remoteNames.delete(data.user_id);
      removeRemoteVideoTile(data.user_id);
      return;
    }

    if (data.type === "chat") {
      document.dispatchEvent(
        new CustomEvent("smartmeet:chat-message", {
          detail: {
            userId: data.from_id,
            username: data.from,
            text: data.text,
            image: data.image,
            ts: data.ts,
          },
        })
      );
      return;
    }

    if (data.type === "hand-raise") {
      var handFlag = document.querySelector('[data-hand-flag-for="' + data.from_id + '"]');
      if (handFlag) handFlag.classList.toggle("show", !!data.raised);
      var handBadge = document.querySelector('[data-hand-badge-for="' + data.from_id + '"]');
      if (handBadge) handBadge.classList.toggle("show", !!data.raised);

      document.dispatchEvent(
        new CustomEvent("smartmeet:hand-raise", {
          detail: {
            userId: data.from_id,
            username: data.from,
            raised: data.raised,
          },
        })
      );
      return;
    }

    if (data.type === "screen-share") {
      var presBadge = document.querySelector('[data-presenting-badge-for="' + data.from_id + '"]');
      if (presBadge) presBadge.classList.toggle("show", !!data.sharing);
      var tileName = document.querySelector('#remote-tile-' + data.from_id + ' .tile-name');
      if (tileName) {
        var base = tileName.getAttribute('data-base-name') || tileName.textContent;
        tileName.textContent = base + (data.sharing ? ' — Screen' : '');
      }

      document.dispatchEvent(
        new CustomEvent("smartmeet:screen-share", {
          detail: {
            userId: data.from_id,
            username: data.from,
            sharing: data.sharing,
          },
        })
      );
      return;
    }

    if (data.type === "sign-caption") {
      document.dispatchEvent(
        new CustomEvent("smartmeet:sign-caption", {
          detail: {
            userId: data.from_id,
            username: data.from,
            emojis: data.emojis || [],
          },
        })
      );
      return;
    }

    if (data.type === "reaction") {
      document.dispatchEvent(
        new CustomEvent("smartmeet:reaction", {
          detail: {
            userId: data.from_id,
            username: data.from,
            emoji: data.emoji,
          },
        })
      );
      return;
    }

    var remoteId = data.from_id;
    if (!remoteId || remoteId === cfg.userId) return;

    if (data.from) {
      applyRemoteName(remoteId, data.from);
    }

    var pc = peers.get(remoteId);
    if (!pc) {
      // The remote peer is sending us an offer but we haven't created a PC
      // for them yet (their peer-joined hasn't arrived or was lost).
      // Create a non-initiator peer — we'll answer their offer.
      console.log("[pc] creating answerer peer for", remoteId, "(offer arrived first)");
      pc = createPeer(remoteId, false);
    }

    if (data.type === "offer" && data.sdp) {
      try {
        // GLARE (both sides offered at the same time). Resolve it
        // deterministically so both browsers make the SAME choice:
        //   - the peer with the HIGHER id keeps its own offer ("impolite")
        //     and discards the remote one,
        //   - the peer with the LOWER id backs down ("polite"), rolls its
        //     own offer back and answers.
        // Both sides independently compute the same winner, so we can never
        // end up with both rolling back (or both refusing).
        var polite = Number(cfg.userId) < Number(remoteId);
        var offerCollision =
          pc._makingOffer || pc._pendingOffer || pc.signalingState === "have-local-offer";
        if (offerCollision && !polite) {
          console.log("[pc] glare — ignoring remote offer from", remoteId, "(we are impolite)");
          pc._ignoreOffer = true;
          return;
        }
        pc._ignoreOffer = false;
        if (pc.signalingState === "have-local-offer") {
          console.log("[pc] glare — rolling back local offer from", remoteId, "(we are polite)");
          await pc.setLocalDescription({ type: "rollback" });
        }
        await pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
        pc._ignoreOffer = false;
        // CRITICAL: tracks must be added AFTER setRemoteDescription(offer) so
        // the transceivers match the offer's m-lines. This ensures the answer
        // carries our video/audio as sendrecv.
        addMissingLocalTracks(pc);
        var answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        ws.send(
          JSON.stringify({
            type: "answer",
            target_id: remoteId,
            sdp: pc.localDescription,
          }          )
        );
      } catch (err) {
        console.error("[pc] offer handling error for", remoteId, err);
      }
    } else if (data.type === "answer" && data.sdp) {
      // If we're already in stable state, this answer is stale (e.g. from a
      // previous glare exchange). Safe to ignore — don't throw errors.
      if (pc.signalingState === "stable") {
        console.log("[pc] ignoring stale answer from", remoteId, "(already stable)");
        return;
      }
      try {
        await pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
        // Clear the "we lost glare" state — this remote description is the
        // one we accepted. (_pendingOffer is deliberately left alone: media
        // added while our own offer was in flight still needs a follow-up
        // negotiation, and a harmless extra offer is much cheaper than a
        // camera/mic that never gets sent.)
        pc._ignoreOffer = false;
      } catch (err) {
        console.error("[pc] answer handling error for", remoteId, err);
      }
    } else if (data.type === "ice-candidate" && data.candidate) {
      // Candidates that belong to an offer we just discarded (glare) must not
      // be applied — they describe a negotiation that no longer exists.
      if (pc._ignoreOffer) {
        console.log("[pc] dropping ICE candidate from", remoteId, "(ignored offer)");
        return;
      }
      // Buffer candidates that arrive before the remote description is set.
      // They'll be flushed once the PC reaches stable state.
      if (pc.remoteDescription && pc.remoteDescription.type) {
        try {
          await pc.addIceCandidate(new RTCIceCandidate(data.candidate));
        } catch (e) {
          console.warn("[pc] ICE candidate error for", remoteId, e);
        }
      } else {
        var buf = pendingCandidates.get(remoteId);
        if (buf) {
          buf.push(data.candidate);
          console.log("[pc] buffered ICE candidate for", remoteId, "(total:", buf.length, ")");
        }
      }
    }
  }

  function connectSocket() {
    var url = cfg.wsScheme + "://" + window.location.host + "/ws/room/" + cfg.roomCode + "/";
    ws = new WebSocket(url);

    window.SMARTMEET_WS = ws;

    ws.onopen = function () {
      console.log("[ws] connected to", url);
      setStatus("Connected — waiting for others to join...");
    };
    ws.onclose = function (e) {
      console.warn("[ws] closed", e.code, e.reason);
      setStatus("Disconnected from signaling server — refresh the page");
    };
    ws.onerror = function (e) {
      console.error("[ws] error", e);
      setStatus("WebSocket error — make sure Daphne is running (daphne -b 0.0.0.0 -p 8000 config.asgi:application)");
    };
    ws.onmessage = function (ev) {
      try {
        var data = JSON.parse(ev.data);
        handleSignal(data).catch(function (e) { console.error("handleSignal error:", e); });
      } catch (e) {
        console.error(e);
      }
    };
  }

  // Add the local tracks we have to EVERY peer that is missing them and ask
  // for a renegotiation. Runs after media is (re)acquired — including for
  // peers we ANSWERED rather than initiated, which used to be skipped and
  // left those participants without our audio/video.
  function backfillPeers() {
    peers.forEach(function (pc, id) {
      if (pc.connectionState === "closed") return;
      var added = 0;
      try {
        added = addMissingLocalTracks(pc);
      } catch (err) {
        console.warn("[pc] backfill addTrack failed for", id, err);
        return;
      }
      if (added > 0) {
        console.log("[pc] backfilled", added, "track(s) to peer", id, "— renegotiating");
        sendOffer(pc, id).catch(function (err) {
          console.error("[pc] backfill offer failed for", id, err);
        });
      }
    });
  }

  let mediaInitPromise = null;
  async function initMedia() {
    if (mediaInitPromise) return mediaInitPromise;
    mediaInitPromise = _initMedia().finally(function () {
      mediaInitPromise = null;
    });
    return mediaInitPromise;
  }

  async function _initMedia() {
    // The page can create a stream of its own (camera button pressed while
    // our first getUserMedia failed). Adopt it so we never end up with two
    // different "local stream" objects — the peers would only ever get one
    // of them.
    if (!localStream && window.SMARTMEET_LOCAL_STREAM) {
      localStream = window.SMARTMEET_LOCAL_STREAM;
    }

    const hasLive = function (kind) {
      return (
        localStream &&
        localStream.getTracks().some(function (t) {
          return t.kind === kind && t.readyState === "live";
        })
      );
    };

    const needVideo = !hasLive("video");
    const needAudio = !hasLive("audio");

    if (needVideo || needAudio) {
      // Try the full request first, then relax: a missing/blocked CAMERA must
      // never cost the user their MICROPHONE (an all-or-nothing getUserMedia
      // failure is how someone ends up joined-but-silent), and vice versa.
      const attempts = [];
      if (needVideo && needAudio) attempts.push({ video: true, audio: true });
      if (needAudio) attempts.push({ audio: true, video: false });
      if (needVideo) attempts.push({ video: true, audio: false });

      let stream = null;
      let lastErr = null;
      for (const constraints of attempts) {
        try {
          stream = await navigator.mediaDevices.getUserMedia(constraints);
          break;
        } catch (err) {
          lastErr = err;
          console.warn("[media] getUserMedia failed for", constraints, err);
        }
      }

      if (!stream) {
        console.warn("Camera/mic unavailable, joining without media:", lastErr);
        setStatus("Joined without camera/microphone — click Mute or Camera to retry");
        backfillPeers();
        return;
      }

      if (!localStream) {
        localStream = stream;
      } else {
        stream.getTracks().forEach(function (track) {
          if (!localStream.getTracks().some(function (t) { return t.id === track.id; })) {
            localStream.addTrack(track);
          }
        });
      }
      if (localVideo) localVideo.srcObject = localStream;
      window.SMARTMEET_LOCAL_STREAM = localStream;
      setStatus("Camera and microphone ready — connecting...");
    }

    // Backfill EVERY peer (not just the ones we initiated): tracks added
    // after a peer connection was negotiated are only sent once we
    // renegotiate, otherwise the far end never hears/sees us.
    backfillPeers();
  }

  // So the page can re-request permission if the first attempt failed
  // (denied, device busy, no camera…) instead of staying mute forever.
  window.SMARTMEET_INIT_MEDIA = initMedia;

  window.SMARTMEET_SET_VIDEO = async function (enabled) {
    if (!localStream) return;
    if (enabled) {
      var existingTracks = localStream.getVideoTracks();
      var hasLive = existingTracks.some(function (t) { return t.readyState === "live"; });
      if (hasLive) return;
      var camStream = await navigator.mediaDevices.getUserMedia({ video: true });
      var newTrack = camStream.getVideoTracks()[0];
      localStream.addTrack(newTrack);
      if (localVideo) localVideo.srcObject = localStream;
      var peerEntries = Array.from(peers.entries());
      for (var i = 0; i < peerEntries.length; i++) {
        var pcEntry = peerEntries[i];
        if (pcEntry[1].connectionState === "closed") continue;
        var vs = pcEntry[1].getSenders().find(function (s) { return s.track && s.track.kind === "video"; });
        if (vs) { try { await vs.replaceTrack(newTrack); } catch (_) {} }
        else { pcEntry[1].addTrack(newTrack, localStream); }
        pcEntry[1]._negotiationDone = false;
      }
    } else {
      if (localVideo) localVideo.srcObject = null;
      localStream.getVideoTracks().forEach(function (t) { t.stop(); localStream.removeTrack(t); });
      var closedPcs = Array.from(peers.values());
      for (var j = 0; j < closedPcs.length; j++) {
        if (closedPcs[j].connectionState === "closed") continue;
        var vs2 = closedPcs[j].getSenders().find(function (s) { return s.track && s.track.kind === "video"; });
        if (vs2) { try { await vs2.replaceTrack(null); } catch (_) {} }
      }
    }
  };

  (async function start() {
    await initMedia();
    connectSocket();
  })();
})();
