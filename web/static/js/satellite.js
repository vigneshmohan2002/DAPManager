// @ts-check
"use strict";

/**
 * @typedef {{mbid: string, title?: string, artist?: string, album?: string,
 *   album_id?: string, duration_seconds?: number, track_number?: number}} SatelliteTrack
 * @typedef {{id: string, title?: string, artist?: string, track_count?: number}} SatelliteAlbum
 * @typedef {{release_mbid: string, title?: string, artist?: string,
 *   track_count?: number, date?: string, country?: string, status?: string,
 *   disambiguation?: string, score?: number, cover_url?: string, format?: string,
 *   label?: string, catalog_number?: string, barcode?: string,
 *   musicbrainz_url?: string}} AlbumCandidate
 * @typedef {{id: string|number, release_mbid?: string, title?: string,
 *   artist?: string, track_count?: number, stage?: string, detail?: string,
 *   completed_tracks?: number}} AlbumDownloadRequest
 */

/* ─── Globals ──────────────────────────────────────── */
/** @type {HTMLAudioElement} */
const audio = /** @type {HTMLAudioElement} */ (document.getElementById("audio"));
/** @type {SatelliteTrack[]} */
let allTracks = [], viewTracks = [];
let curIdx = -1, scrubbing = false;
/** @type {SatelliteAlbum[]} */
let allAlbums = [];
/** @type {SatelliteTrack[]} */
let curAlbumTracks = [];
/** @type {string | null} */
let curAlbumId = null;
let activityLog = [];
let likedMbids = new Set();
/** @type {AlbumCandidate[]} */
let albumCandidates = [];
/** @type {AlbumCandidate | null} */
let selectedAlbumCandidate = null;
let albumSearchState = "idle";
let albumSearchMessage = "";
let albumSearchTimer = 0;
let albumSearchSequence = 0;
/** @type {AbortController | null} */
let albumSearchController = null;
let albumRequestSubmitting = false;
const ALBUM_REQUEST_STORAGE_PREFIX = "dapmanager.satellite.albumRequestIds.v1";
const ALBUM_REQUEST_DISMISSED_STORAGE_PREFIX = "dapmanager.satellite.dismissedAlbumRequestIds.v1";
/** @type {string | null} */
let albumRequestStorageKey = null;
/** @type {string | null} */
let albumRequestDismissedStorageKey = null;
/** @type {string[]} */
let trackedAlbumRequestIds = [];
let dismissedAlbumRequestIds = new Set();
/** @type {Promise<void>} */
let albumRequestStorageReady = Promise.resolve();
/** @type {Map<string, AlbumDownloadRequest>} */
const trackedAlbumRequests = new Map();
/** @type {Map<string, string>} */
const albumRequestPollErrors = new Map();
let albumRequestPollInFlight = false;
let albumRequestListInFlight = false;

/* ─── Utilities ────────────────────────────────────── */
const $ = id => document.getElementById(id);
const fmt = s => (!s||isNaN(s)) ? "—" : Math.floor(+s/60)+":"+String(Math.floor(+s%60)).padStart(2,"0");
const esc = s => String(s??"").replace(/[&<>"']/g,c=>({'&':"&amp;",'<':"&lt;",'>':"&gt;",'"':"&quot;","'":'&#39;'}[c]));
const isReleaseMbid = value => /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(String(value||""));

function safeCoverUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(String(value), window.location.origin);
    return (url.protocol === "http:" || url.protocol === "https:") ? url.href : "";
  } catch (_) { return ""; }
}

function loadStoredIdList(key) {
  if (!key) return [];
  try {
    const value = JSON.parse(localStorage.getItem(key) || "[]");
    if (!Array.isArray(value)) return [];
    return [...new Set(value
      .filter(id => typeof id === "string" || typeof id === "number")
      .map(id => String(id).trim())
      .filter(Boolean))].slice(0, 30);
  } catch (_) { return []; }
}

function loadTrackedAlbumRequestIds() {
  return loadStoredIdList(albumRequestStorageKey);
}

function saveTrackedAlbumRequestIds() {
  if (!albumRequestStorageKey) return;
  try { localStorage.setItem(albumRequestStorageKey, JSON.stringify(trackedAlbumRequestIds)); }
  catch (_) { /* Progress still works for this page session. */ }
}

function saveDismissedAlbumRequestIds() {
  if (!albumRequestDismissedStorageKey) return;
  try { localStorage.setItem(albumRequestDismissedStorageKey, JSON.stringify([...dismissedAlbumRequestIds].slice(0, 100))); }
  catch (_) { /* Dismissal is best effort. */ }
}

function albumRequestMasterScope(config) {
  if (!config || typeof config !== "object") return "";
  const role = String(config.device_role || "").trim().toLowerCase();
  const rawMasterUrl = String(config.master_url || "").trim();
  if (role === "satellite") {
    if (!rawMasterUrl) return "";
    try {
      const parsed = new URL(rawMasterUrl);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
      if (parsed.username || parsed.password || parsed.search || parsed.hash) return "";
      const pathname = parsed.pathname.replace(/\/+$/, "");
      return `master:${parsed.origin}${pathname}`;
    } catch (_) {
      return "";
    }
  }
  if (role === "master" || role === "standalone") {
    return `${role}:${window.location.origin}`;
  }
  return "";
}

async function initializeAlbumRequestStorage() {
  // Never read the old unscoped keys. Request IDs are only meaningful to the
  // authority that allocated them, and numeric IDs can overlap across masters.
  try {
    const result = await api("GET", "/api/config");
    const scope = result && result.success === true
      ? albumRequestMasterScope(result.config)
      : "";
    if (!scope) return;
    const suffix = encodeURIComponent(scope);
    albumRequestStorageKey = `${ALBUM_REQUEST_STORAGE_PREFIX}:${suffix}`;
    albumRequestDismissedStorageKey = `${ALBUM_REQUEST_DISMISSED_STORAGE_PREFIX}:${suffix}`;
    trackedAlbumRequestIds = loadTrackedAlbumRequestIds();
    dismissedAlbumRequestIds = new Set(loadStoredIdList(albumRequestDismissedStorageKey));
  } catch (_) {
    // Without a verified authority identity, retain page-session progress only.
  }
}

function rememberAlbumRequest(request) {
  if (!request || request.id == null || String(request.id).trim() === "") return false;
  const id = String(request.id).trim();
  if (dismissedAlbumRequestIds.delete(id)) saveDismissedAlbumRequestIds();
  trackedAlbumRequestIds = [id, ...trackedAlbumRequestIds.filter(value => value !== id)].slice(0, 30);
  trackedAlbumRequests.set(id, request);
  albumRequestPollErrors.delete(id);
  saveTrackedAlbumRequestIds();
  return true;
}

function toast(msg, dur=2400) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), dur);
}

async function api(method, path, body) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 30000);
  /** @type {RequestInit} */
  const opts = { method, headers: {}, signal:controller.signal, cache:"no-store" };
  if (body) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  let r;
  try { r = await fetch(path, opts); }
  catch (error) {
    if (error && error.name === "AbortError") throw new Error("Request timed out");
    throw error;
  }
  finally { window.clearTimeout(timeout); }
  let data = null;
  try { data = await r.json(); }
  catch (_) { /* The status below still gives a useful fallback. */ }
  if (!r.ok) {
    /** @type {Error & {status?: number}} */
    const error = new Error((data && (data.message || data.error)) || ("HTTP " + r.status));
    error.status = r.status;
    throw error;
  }
  if (!data || typeof data !== "object") throw new Error("Invalid JSON response");
  return data;
}

function logActivity(msg) {
  const now = new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
  activityLog.unshift({ msg, time: now });
  renderActivity();
}

/* ─── Tab navigation ───────────────────────────────── */
document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    const id = tab.dataset.tab;
    document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t===tab));
    document.querySelectorAll(".panel").forEach(p => p.classList.toggle("active", p.id==="panel-"+id));
    if (id==="queue") {
      refreshStatus();
      reconcileTrackedAlbumRequests();
    }
  });
});

/* ─── Load library ─────────────────────────────────── */
async function loadLibrary() {
  try {
    const [td, ad] = await Promise.all([
      api("GET", "/api/library/tracks?limit=5000"),
      api("GET", "/api/library/albums?limit=2000"),
    ]);
    allTracks  = td.tracks || [];
    viewTracks = allTracks;
    allAlbums  = ad.albums || [];
    renderPlayerQueue();
    const message = $("lib-msg");
    if (message) message.style.display = "none";
    renderLibrary();
    if ($("req-search").value.trim()) renderRequestSearch($("req-search").value.trim());
  } catch(e) {
    const list = $("lib-list");
    if (list) {
      list.innerHTML = `<div class="state-msg">Failed to load library: ${esc(e.message)}</div>`;
    }
  }
}

/* ─── PLAYER ───────────────────────────────────────── */
function playIdx(idx, list) {
  if (list) { viewTracks = list; }
  if (idx < 0 || idx >= viewTracks.length) return;
  curIdx = idx;
  const t = viewTracks[idx];
  $("np-title").textContent  = t.title  || "Unknown";
  $("np-artist").textContent = [t.artist, t.album].filter(Boolean).join(" · ") || "—";

  const img = $("cover-img"), ph = $("cover-ph");
  if (t.album_id) {
    img.src = window.dapApiUrl("/api/library/albums/"+encodeURIComponent(t.album_id)+"/cover");
    img.style.display=""; ph.style.display="none";
    img.onerror = () => { img.style.display="none"; ph.style.display=""; };
  } else { img.style.display="none"; ph.style.display=""; }

  audio.src = window.dapApiUrl("/api/stream/"+encodeURIComponent(t.mbid));
  audio.load(); audio.play().catch(()=>{});

  const lb = $("btn-like");
  lb.style.display = "";
  lb.classList.toggle("liked", likedMbids.has(t.mbid));
  lb.querySelector("path").setAttribute("fill", likedMbids.has(t.mbid) ? "currentColor" : "none");

  syncMediaSession(t);
  renderPlayerQueue();
  // switch to player tab
  document.querySelectorAll(".tab")[0].click();
}

function renderPlayerQueue() {
  const el = $("player-queue");
  if (!viewTracks.length) { el.innerHTML = "<div class='state-msg'>Library empty.</div>"; return; }
  const start = Math.max(0, curIdx);
  const slice = viewTracks.slice(start, start+30);
  el.innerHTML = slice.map((t,i) => {
    const idx = start+i, active = (idx===curIdx);
    return `<div class="row" data-idx="${idx}">
      <div class="trk-idx" style="width:22px;flex-shrink:0;font-size:12px;color:${active?"var(--accent)":"var(--muted)"}">
        ${active?"▶":idx+1}
      </div>
      <div class="row-info">
        <div class="row-title" style="color:${active?"var(--accent)":"var(--text)"}">${esc(t.title||"Unknown")}</div>
        <div class="row-sub">${esc(t.artist||"—")}</div>
      </div>
      <div style="font-size:12px;color:var(--muted)">${fmt(t.duration_seconds)}</div>
    </div>`;
  }).join("");
  el.querySelectorAll(".row").forEach(r => r.addEventListener("click", () => playIdx(+r.dataset.idx)));
}

$("btn-play").addEventListener("click", () => {
  if (curIdx<0) { playIdx(0); return; }
  audio.paused ? audio.play().catch(()=>{}) : audio.pause();
});
$("btn-prev").addEventListener("click", () => {
  if (audio.currentTime>3) { audio.currentTime=0; return; }
  if (curIdx>0) playIdx(curIdx-1);
});
$("btn-next").addEventListener("click", () => { if (curIdx<viewTracks.length-1) playIdx(curIdx+1); });
audio.addEventListener("play",  () => { $("ico-play").style.display="none"; $("ico-pause").style.display=""; });
audio.addEventListener("pause", () => { $("ico-play").style.display="";     $("ico-pause").style.display="none"; });
audio.addEventListener("ended", () => { if (curIdx<viewTracks.length-1) playIdx(curIdx+1); else { $("ico-play").style.display=""; $("ico-pause").style.display="none"; } });
audio.addEventListener("timeupdate", () => {
  if (scrubbing||!audio.duration||isNaN(audio.duration)) return;
  const pct = audio.currentTime/audio.duration;
  const s=$("scrubber"); s.value=Math.round(pct*1000); s.style.setProperty("--pct",pct);
  $("time-cur").textContent=fmt(audio.currentTime);
});
audio.addEventListener("loadedmetadata", () => { $("time-dur").textContent=fmt(audio.duration); });
$("scrubber").addEventListener("pointerdown", ()=>{ scrubbing=true; });
$("scrubber").addEventListener("pointerup",   ()=>{ scrubbing=false; });
$("scrubber").addEventListener("input", e => {
  const pct=e.target.value/1000; e.target.style.setProperty("--pct",pct);
  if (audio.duration&&!isNaN(audio.duration)) { audio.currentTime=pct*audio.duration; $("time-cur").textContent=fmt(pct*audio.duration); }
});

$("btn-like").addEventListener("click", async () => {
  const t = viewTracks[curIdx]; if (!t) return;
  const liked = !likedMbids.has(t.mbid);
  try {
    await api(liked?"POST":"DELETE", "/api/library/tracks/"+encodeURIComponent(t.mbid)+"/like");
    if (liked) likedMbids.add(t.mbid); else likedMbids.delete(t.mbid);
    $("btn-like").classList.toggle("liked", liked);
    $("btn-like").querySelector("path").setAttribute("fill", liked?"currentColor":"none");
    toast(liked ? "Liked" : "Removed from liked");
  } catch(e) { toast("Could not update like"); }
});

function syncMediaSession(t) {
  if (!("mediaSession" in navigator)) return;
  navigator.mediaSession.metadata = new MediaMetadata({
    title:t.title||"Unknown", artist:t.artist||"", album:t.album||"",
    artwork: t.album_id?[{src:window.dapApiUrl("/api/library/albums/"+encodeURIComponent(t.album_id)+"/cover"),sizes:"512x512",type:"image/jpeg"}]:[]
  });
  navigator.mediaSession.setActionHandler("play",          ()=>audio.play().catch(()=>{}));
  navigator.mediaSession.setActionHandler("pause",         ()=>audio.pause());
  navigator.mediaSession.setActionHandler("previoustrack", ()=>{ if(curIdx>0)playIdx(curIdx-1); });
  navigator.mediaSession.setActionHandler("nexttrack",     ()=>{ if(curIdx<viewTracks.length-1)playIdx(curIdx+1); });
  navigator.mediaSession.setActionHandler("seekto",        d=>{ if(d.seekTime!=null)audio.currentTime=d.seekTime; });
}

/* ─── LIBRARY ──────────────────────────────────────── */
function renderLibrary(filter="") {
  const el = $("lib-list");
  const q = filter.toLowerCase();
  const albums = q ? allAlbums.filter(a =>
    (a.title||"").toLowerCase().includes(q) || (a.artist||"").toLowerCase().includes(q)
  ) : allAlbums;
  if (!albums.length) { el.innerHTML = q ? "<div class='state-msg'>No results.</div>" : ""; return; }
  el.innerHTML = albums.map(a =>
    `<div class="row" data-album-id="${esc(a.id)}">
      <div class="row-art" id="art-${esc(a.id)}">♪<img data-album="${esc(a.id)}" alt=""></div>
      <div class="row-info">
        <div class="row-title">${esc(a.title||"Unknown Album")}</div>
        <div class="row-sub">${esc(a.artist||"—")} · ${a.track_count||""} tracks</div>
      </div>
      <div class="chevron">›</div>
    </div>`
  ).join("");

  // lazy-load album art
  el.querySelectorAll("img[data-album]").forEach(img => {
    const obs = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting) {
        img.src = window.dapApiUrl("/api/library/albums/"+encodeURIComponent(img.dataset.album)+"/cover");
        img.style.display="";
        img.onerror = ()=>img.remove();
        obs.disconnect();
      }
    }, { rootMargin:"100px" });
    obs.observe(img);
  });

  el.querySelectorAll(".row").forEach(row =>
    row.addEventListener("click", () => openAlbum(row.dataset.albumId))
  );
}

$("lib-search").addEventListener("input", e => renderLibrary(e.target.value.trim()));

async function openAlbum(albumId) {
  curAlbumId = albumId;
  const album = allAlbums.find(a=>a.id===albumId) || {};
  $("overlay-title").textContent = album.title || "Album";
  $("album-meta-title").textContent = album.title || "Unknown Album";
  $("album-meta-sub").textContent   = album.artist || "—";

  const artImg = $("album-art-img");
  artImg.src = window.dapApiUrl("/api/library/albums/"+encodeURIComponent(albumId)+"/cover");
  artImg.style.display="";
  artImg.onerror = ()=>artImg.remove();

  $("overlay-tracks").innerHTML = "<div class='state-msg'>Loading…</div>";
  $("album-overlay").classList.add("open");

  try {
    const d = await api("GET", "/api/library/albums/"+encodeURIComponent(albumId)+"/tracks");
    curAlbumTracks = d.tracks || [];
    $("overlay-tracks").innerHTML = curAlbumTracks.map((t,i) =>
      `<div class="row" data-i="${i}">
        <div style="width:28px;flex-shrink:0;font-size:13px;color:var(--muted);text-align:right">${t.track_number||i+1}</div>
        <div class="row-info">
          <div class="row-title">${esc(t.title||"Unknown")}</div>
          <div class="row-sub">${fmt(t.duration_seconds)}</div>
        </div>
      </div>`
    ).join("") || "<div class='state-msg'>No tracks.</div>";

    $("overlay-tracks").querySelectorAll(".row").forEach(r =>
      r.addEventListener("click", () => { closeAlbum(); playIdx(+r.dataset.i, [...curAlbumTracks]); })
    );
  } catch(e) { $("overlay-tracks").innerHTML = "<div class='state-msg'>Failed to load tracks.</div>"; }
}

function closeAlbum() { $("album-overlay").classList.remove("open"); }
$("btn-back").addEventListener("click", closeAlbum);
$("btn-play-album").addEventListener("click", () => { closeAlbum(); playIdx(0, [...curAlbumTracks]); });

/* ─── SEARCH / REQUEST ─────────────────────────────── */
function localRequestMatches(query) {
  const q = query.toLowerCase();
  if (!q) return [];
  return allTracks.filter(t =>
    (t.title||"").toLowerCase().includes(q) ||
    (t.artist||"").toLowerCase().includes(q) ||
    (t.album||"").toLowerCase().includes(q)
  ).slice(0, 30);
}

function albumCandidateMeta(candidate) {
  const parts = [];
  if (candidate.date) parts.push(String(candidate.date));
  if (candidate.country) parts.push(String(candidate.country));
  if (candidate.format) parts.push(String(candidate.format));
  if (Number(candidate.track_count) > 0) parts.push(`${Number(candidate.track_count)} tracks`);
  if (candidate.status) parts.push(String(candidate.status));
  return parts.join(" · ");
}

function updateAlbumRequestButton() {
  const button = /** @type {HTMLButtonElement} */ ($("btn-request-album"));
  const selected = selectedAlbumCandidate;
  button.hidden = !selected;
  button.disabled = !selected || albumRequestSubmitting;
  if (albumRequestSubmitting) button.textContent = "Queuing Album…";
  else if (selected) button.textContent = `Request “${selected.title || "Selected Album"}”`;
  else button.textContent = "Request Selected Album";
}

function renderRequestSearch(query="") {
  const el = $("req-results");
  if (!query) {
    el.innerHTML = "<div class='state-msg'>Search your library and choose an album verified by MusicBrainz, or request an individual track.</div>";
    updateAlbumRequestButton();
    return;
  }

  const matches = localRequestMatches(query);
  const libraryHtml = matches.length ? matches.map((t, i) =>
    `<div class="row" data-local-index="${i}">
      <div class="row-info">
        <div class="row-title">${esc(t.title||"Unknown")}</div>
        <div class="row-sub">${esc(t.artist||"—")} · ${esc(t.album||"—")}</div>
      </div>
      <div style="font-size:12px;color:var(--muted)">${fmt(t.duration_seconds)}</div>
    </div>`
  ).join("") : "<div class='result-note'>No matching tracks in this library.</div>";

  let albumHtml = "";
  if (query.length < 2) {
    albumHtml = "<div class='result-note'>Type at least two characters to check MusicBrainz.</div>";
  } else if (albumSearchState === "loading") {
    albumHtml = "<div class='result-note'>Checking MusicBrainz…</div>";
  } else if (albumSearchState === "error") {
    albumHtml = `<div class='result-note'>${esc(albumSearchMessage || "MusicBrainz search is unavailable. Try again.")}</div>`;
  } else if (albumSearchState === "empty") {
    albumHtml = "<div class='result-note'>No matching MusicBrainz albums. Refine the artist or album name.</div>";
  } else if (albumSearchState === "ready") {
    albumHtml = albumCandidates.map((candidate, i) => {
      const selected = selectedAlbumCandidate && selectedAlbumCandidate.release_mbid === candidate.release_mbid;
      const cover = safeCoverUrl(candidate.cover_url);
      const edition = [candidate.label, candidate.catalog_number, candidate.barcode ? `Barcode ${candidate.barcode}` : ""]
        .filter(Boolean).join(" · ");
      const extra = [candidate.disambiguation, edition, `MBID ${candidate.release_mbid}`, Number.isFinite(Number(candidate.score)) ? `MusicBrainz ${Number(candidate.score)}%` : ""]
        .filter(Boolean).join(" · ");
      return `<button type="button" class="album-candidate${selected ? " selected" : ""}" data-candidate-index="${i}" aria-pressed="${selected ? "true" : "false"}">
        <span class="candidate-art">${cover ? `<img src="${esc(cover)}" alt="">` : "♪"}</span>
        <span class="row-info">
          <span class="row-title" style="display:block">${esc(candidate.title||"Unknown Album")}</span>
          <span class="row-sub" style="display:block">${esc(candidate.artist||"—")}</span>
          <span class="candidate-meta" style="display:block">${esc(albumCandidateMeta(candidate))}${extra ? `<br>${esc(extra)}` : ""}</span>
        </span>
        <span class="candidate-check" aria-hidden="true">✓</span>
      </button>`;
    }).join("");
  } else {
    albumHtml = "<div class='result-note'>Checking MusicBrainz…</div>";
  }

  el.innerHTML = `<div class="result-section">
      <div class="result-section-title">In your library</div>${libraryHtml}
    </div>
    <div class="result-section">
      <div class="result-section-title">Albums on MusicBrainz</div>${albumHtml}
    </div>`;

  el.querySelectorAll("[data-local-index]").forEach(row => {
    row.addEventListener("click", () => {
      const track = matches[Number(row.dataset.localIndex)];
      if (track) playIdx(allTracks.indexOf(track), allTracks);
    });
  });
  el.querySelectorAll("[data-candidate-index]").forEach(row => {
    row.addEventListener("click", () => {
      const candidate = albumCandidates[Number(row.dataset.candidateIndex)];
      if (!candidate || !isReleaseMbid(candidate.release_mbid)) return;
      selectedAlbumCandidate = candidate;
      renderRequestSearch($("req-search").value.trim());
    });
  });
  el.querySelectorAll(".candidate-art img").forEach(image => {
    image.addEventListener("error", () => {
      const host = image.parentElement;
      if (host) { image.remove(); host.textContent = "♪"; }
    }, {once:true});
  });
  updateAlbumRequestButton();
}

function scheduleAlbumSearch(rawQuery) {
  const query = rawQuery.trim();
  window.clearTimeout(albumSearchTimer);
  albumSearchSequence += 1;
  const sequence = albumSearchSequence;
  if (albumSearchController) albumSearchController.abort();
  albumSearchController = null;
  albumCandidates = [];
  selectedAlbumCandidate = null;
  albumSearchMessage = "";

  if (!query) {
    albumSearchState = "idle";
    renderRequestSearch("");
    return;
  }
  if (query.length < 2) {
    albumSearchState = "idle";
    renderRequestSearch(query);
    return;
  }

  albumSearchState = "loading";
  renderRequestSearch(query);
  albumSearchTimer = window.setTimeout(async () => {
    const controller = new AbortController();
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, 20000);
    albumSearchController = controller;
    try {
      const response = await fetch(`/api/download/albums/search?q=${encodeURIComponent(query)}`, {signal: controller.signal, cache:"no-store"});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (data.success !== true || !Array.isArray(data.candidates)) {
        throw new Error("Invalid MusicBrainz response");
      }
      if (sequence !== albumSearchSequence || $("req-search").value.trim() !== query) return;
      albumCandidates = data.candidates.filter(candidate =>
        candidate && typeof candidate === "object" && isReleaseMbid(candidate.release_mbid)
      ).slice(0, 25);
      albumSearchState = albumCandidates.length ? "ready" : "empty";
      albumSearchMessage = "";
    } catch (error) {
      if (error && error.name === "AbortError" && !timedOut) return;
      if (sequence !== albumSearchSequence || $("req-search").value.trim() !== query) return;
      albumCandidates = [];
      selectedAlbumCandidate = null;
      albumSearchState = "error";
      albumSearchMessage = "Could not check MusicBrainz. Your library results are still available.";
    } finally {
      window.clearTimeout(timeout);
      if (sequence === albumSearchSequence) albumSearchController = null;
    }
    if (sequence === albumSearchSequence && $("req-search").value.trim() === query) {
      renderRequestSearch(query);
    }
  }, 450);
}

$("req-search").addEventListener("input", event => scheduleAlbumSearch(event.target.value));

$("btn-request-album").addEventListener("click", async () => {
  await albumRequestStorageReady;
  const candidate = selectedAlbumCandidate;
  if (!candidate || !isReleaseMbid(candidate.release_mbid) || albumRequestSubmitting) return;
  albumRequestSubmitting = true;
  updateAlbumRequestButton();
  try {
    const result = await api("POST", "/api/download/albums/request", {release_mbid:candidate.release_mbid});
    if (result.success !== true || !result.request || result.request.id == null) {
      throw new Error(result.error || "Master did not return a request ID");
    }
    const request = Object.assign({}, candidate, result.request);
    if (!rememberAlbumRequest(request)) throw new Error("Master returned an invalid request ID");
    renderAlbumDownloads();
    logActivity(`${result.queued ? "Requested" : "Tracking"} album: ${candidate.artist || "Unknown artist"} — ${candidate.title || "Unknown album"}`);

    const requestStage = normalizedAlbumRequestStage(request);
    let started = false;
    let startAttempted = false;
    if (result.queued === true && requestStage === "queued") {
      startAttempted = true;
      try {
        const runResult = await api("POST", "/api/download");
        started = runResult.success === true;
      }
      catch (_) { started = false; }
    }

    selectedAlbumCandidate = null;
    renderRequestSearch($("req-search").value.trim());
    const queueTab = document.querySelector('.tab[data-tab="queue"]');
    if (queueTab) queueTab.click();
    if (requestStage === "success") toast("Album is already in the master library", 3600);
    else if (!result.queued) toast(`Album request is already ${requestStage}`, 3600);
    else if (started) toast("Album queued; download started", 3600);
    else if (startAttempted) toast("Album queued; start the queue when the master is available", 3600);
    else toast("Album request is being tracked", 3600);
    refreshTrackedAlbumRequests();
  } catch (error) {
    toast(`Album request failed: ${error.message}`, 3600);
  } finally {
    albumRequestSubmitting = false;
    updateAlbumRequestButton();
  }
});

$("btn-open-req").addEventListener("click", () => {
  const q = $("req-search").value.trim();
  if (q) $("req-query").value = q;
  $("req-sheet").classList.add("open");
  setTimeout(()=>$("req-query").focus(), 100);
});
$("req-cancel").addEventListener("click", ()=>$("req-sheet").classList.remove("open"));
$("req-backdrop").addEventListener("click", ()=>$("req-sheet").classList.remove("open"));

$("req-send").addEventListener("click", async () => {
  const q = $("req-query").value.trim(); if (!q) return;
  $("req-send").disabled=true; $("req-send").textContent="Sending…";
  try {
    const r = await api("POST","/api/download/request",{search_query:q});
    $("req-sheet").classList.remove("open");
    $("req-query").value="";
    toast(r.queued ? "Request queued on master" : "Already queued");
    logActivity("Requested: "+q);
  } catch(e) { toast("Request failed: "+e.message); }
  finally { $("req-send").disabled=false; $("req-send").textContent="Send Request"; }
});

/* ─── QUEUE / STATUS ───────────────────────────────── */
async function refreshStatus() {
  try {
    const s = await api("GET","/api/status?scope=downloads");
    const area=$("status-area");
    area.innerHTML=`<div class="status-banner">
      <div class="status-dot${s.running?" running":""}"></div>
      <div class="status-text">
        <div>${esc(s.message||"Idle")}</div>
        ${s.detail?`<div class="status-sub">${esc(s.detail)}</div>`:""}
      </div>
    </div>`;
  } catch(e) { $("status-area").innerHTML=`<div class="status-banner"><div class="status-dot"></div><div class="status-text">Offline</div></div>`; }
}

function renderActivity() {
  const el=$("activity-list");
  if (!activityLog.length) { el.innerHTML="<div class='state-msg' id='activity-msg'>No activity yet.</div>"; return; }
  el.innerHTML=activityLog.map(a=>
    `<div class="row" style="cursor:default">
      <div class="row-info"><div class="row-title">${esc(a.msg)}</div></div>
      <div style="font-size:12px;color:var(--muted)">${a.time}</div>
    </div>`
  ).join("");
}

function normalizedAlbumRequestStage(request) {
  const stage = String(request && request.stage || "queued").trim().toLowerCase();
  if (stage === "complete" || stage === "completed") return "success";
  if (stage === "error") return "failed";
  return ["queued", "downloading", "importing", "success", "failed"].includes(stage) ? stage : "queued";
}

function albumRequestIsFinished(request) {
  const stage = normalizedAlbumRequestStage(request);
  return stage === "success" || stage === "failed";
}

function renderAlbumDownloads() {
  const el = $("album-download-list");
  const clearButton = $("btn-clear-album-downloads");
  if (!trackedAlbumRequestIds.length) {
    el.innerHTML = "<div class='result-note' id='album-download-msg'>No tracked album downloads.</div>";
    clearButton.hidden = true;
    return;
  }

  let hasFinished = false;
  el.innerHTML = trackedAlbumRequestIds.map(id => {
    const request = trackedAlbumRequests.get(id);
    const pollError = albumRequestPollErrors.get(id);
    if (!request) {
      return `<div class="download-card">
        <div class="download-card-head">
          <span class="download-stage-dot"></span>
          <span class="download-card-title">Album request</span>
          <span class="download-stage">Checking</span>
        </div>
        <div class="download-card-sub">${esc(pollError || "Loading progress…")}</div>
      </div>`;
    }

    const stage = normalizedAlbumRequestStage(request);
    if (albumRequestIsFinished(request)) hasFinished = true;
    const total = Math.max(0, Number(request.track_count) || 0);
    const completed = Math.max(0, Math.min(total || Infinity, Number(request.completed_tracks) || 0));
    const percent = stage === "success" ? 100 : (total ? Math.min(100, Math.round(completed / total * 100)) : 0);
    const title = request.title || "Album request";
    const summary = [request.artist, total ? `${completed}/${total} tracks` : ""].filter(Boolean).join(" · ");
    const detail = pollError || request.detail || (stage === "queued" ? "Waiting for the master download queue." : "");
    return `<div class="download-card">
      <div class="download-card-head">
        <span class="download-stage-dot ${stage}"></span>
        <span class="download-card-title">${esc(title)}</span>
        <span class="download-stage ${stage}">${esc(stage)}</span>
      </div>
      ${summary ? `<div class="download-card-sub">${esc(summary)}</div>` : ""}
      ${detail ? `<div class="download-card-sub">${esc(detail)}</div>` : ""}
      ${total ? `<div class="download-progress" role="progressbar" aria-label="${esc(title)} download progress" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${completed}"><div class="download-progress-fill" style="width:${percent}%"></div></div>` : ""}
    </div>`;
  }).join("");
  clearButton.hidden = !hasFinished;
}

async function refreshTrackedAlbumRequests() {
  await albumRequestStorageReady;
  if (albumRequestPollInFlight || !trackedAlbumRequestIds.length) {
    renderAlbumDownloads();
    return;
  }
  albumRequestPollInFlight = true;
  const ids = [...trackedAlbumRequestIds];
  try {
    await Promise.all(ids.map(async id => {
      try {
        const result = await api("GET", `/api/download/albums/requests/${encodeURIComponent(id)}`);
        if (result.success !== true || !result.request) throw new Error("Invalid progress response");
        const previous = trackedAlbumRequests.get(id) || {id};
        trackedAlbumRequests.set(id, Object.assign({}, previous, result.request, {id:result.request.id == null ? id : result.request.id}));
        albumRequestPollErrors.delete(id);
      } catch (error) {
        if (error && error.status === 404) {
          const previous = trackedAlbumRequests.get(id) || {id};
          trackedAlbumRequests.set(id, Object.assign({}, previous, {
            stage:"failed",
            detail:"This request no longer exists on the configured master.",
          }));
          albumRequestPollErrors.delete(id);
        } else {
          albumRequestPollErrors.set(id, "Progress is temporarily unavailable; this request is still being tracked.");
        }
      }
    }));
  } finally {
    albumRequestPollInFlight = false;
    renderAlbumDownloads();
  }
}

async function reconcileTrackedAlbumRequests() {
  await albumRequestStorageReady;
  if (albumRequestListInFlight) return;
  albumRequestListInFlight = true;
  try {
    const result = await api("GET", "/api/download/albums/requests");
    if (result.success !== true || !Array.isArray(result.requests)) {
      throw new Error("Invalid active request list");
    }
    result.requests.forEach(request => {
      const stage = normalizedAlbumRequestStage(request);
      const active = stage === "queued" || stage === "downloading" || stage === "importing";
      if (request && (active || !dismissedAlbumRequestIds.has(String(request.id)))) {
        rememberAlbumRequest(request);
      }
    });
  } catch (_) {
    // Stored IDs still provide continuity when the master is temporarily away.
  }
  finally { albumRequestListInFlight = false; }
  renderAlbumDownloads();
  refreshTrackedAlbumRequests();
}

$("btn-clear-album-downloads").addEventListener("click", async () => {
  await albumRequestStorageReady;
  const finishedIds = new Set(trackedAlbumRequestIds.filter(id => {
    const request = trackedAlbumRequests.get(id);
    return request ? albumRequestIsFinished(request) : false;
  }));
  trackedAlbumRequestIds = trackedAlbumRequestIds.filter(id => !finishedIds.has(id));
  finishedIds.forEach(id => {
    dismissedAlbumRequestIds.add(id);
    trackedAlbumRequests.delete(id);
    albumRequestPollErrors.delete(id);
  });
  saveTrackedAlbumRequestIds();
  saveDismissedAlbumRequestIds();
  renderAlbumDownloads();
});

$("btn-run-dl").addEventListener("click", async () => {
  $("btn-run-dl").textContent="Starting…"; $("btn-run-dl").disabled=true;
  try {
    await api("POST","/api/download");
    toast("Download queue started");
    logActivity("Started download queue");
    refreshStatus();
    refreshTrackedAlbumRequests();
  } catch(e) { toast("Failed: "+e.message); }
  finally { $("btn-run-dl").textContent="Run Download Queue"; $("btn-run-dl").disabled=false; }
});

$("btn-sync").addEventListener("click", async () => {
  $("btn-sync").textContent="Syncing…"; $("btn-sync").disabled=true;
  try {
    await api("POST","/api/catalog/pull");
    toast("Catalog sync started");
    logActivity("Synced catalog from master");
    refreshStatus();
  } catch(e) { toast("Sync failed: "+e.message); }
  finally { $("btn-sync").textContent="Sync Catalog from Master"; $("btn-sync").disabled=false; }
});

/* auto-poll status every 10s when queue tab is active */
setInterval(()=>{ if($("panel-queue").classList.contains("active")) refreshStatus(); }, 10000);
/* tracked album request IDs survive reloads; poll active progress while visible */
setInterval(() => {
  if (document.visibilityState === "visible" && trackedAlbumRequestIds.some(id => {
    const request = trackedAlbumRequests.get(id);
    return !request || !albumRequestIsFinished(request);
  })) refreshTrackedAlbumRequests();
}, 5000);
setInterval(() => {
  if (document.visibilityState === "visible") reconcileTrackedAlbumRequests();
}, 30000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") reconcileTrackedAlbumRequests();
});

/* ─── Init ─────────────────────────────────────────── */
loadLibrary();
refreshStatus();
albumRequestStorageReady = initializeAlbumRequestStorage();
albumRequestStorageReady.then(() => {
  renderAlbumDownloads();
  reconcileTrackedAlbumRequests();
});
