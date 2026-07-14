// @ts-check
"use strict";

/**
 * @typedef {{mbid: string, title?: string, artist?: string, album?: string,
 *   album_id?: string, duration_seconds?: number, track_number?: number}} SatelliteTrack
 * @typedef {{id: string, title?: string, artist?: string, track_count?: number}} SatelliteAlbum
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

/* ─── Utilities ────────────────────────────────────── */
const $ = id => document.getElementById(id);
const fmt = s => (!s||isNaN(s)) ? "—" : Math.floor(+s/60)+":"+String(Math.floor(+s%60)).padStart(2,"0");
const esc = s => String(s??"").replace(/[&<>"']/g,c=>({'&':"&amp;",'<':"&lt;",'>':"&gt;",'"':"&quot;","'":'&#39;'}[c]));

function toast(msg, dur=2400) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), dur);
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
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
    if (id==="queue") refreshStatus();
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
    renderLibrary();
    $("lib-msg").style.display = "none";
  } catch(e) { $("lib-msg").textContent = "Failed to load library: "+e.message; }
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
$("req-search").addEventListener("input", e => {
  const q = e.target.value.trim().toLowerCase();
  const el = $("req-results");
  if (!q) { el.innerHTML="<div class='state-msg'>Search your library or tap<br><strong>+ Request Download</strong> to add new music.</div>"; return; }
  const matches = allTracks.filter(t =>
    (t.title||"").toLowerCase().includes(q)||(t.artist||"").toLowerCase().includes(q)||(t.album||"").toLowerCase().includes(q)
  ).slice(0,50);
  if (!matches.length) { el.innerHTML="<div class='state-msg'>Not in library yet. Use Request Download to add it.</div>"; return; }
  el.innerHTML = matches.map((t,i)=>
    `<div class="row" data-i="${i}">
      <div class="row-info">
        <div class="row-title">${esc(t.title||"Unknown")}</div>
        <div class="row-sub">${esc(t.artist||"—")} · ${esc(t.album||"—")}</div>
      </div>
      <div style="font-size:12px;color:var(--muted)">${fmt(t.duration_seconds)}</div>
    </div>`
  ).join("");
  el.querySelectorAll(".row").forEach((r,i)=>r.addEventListener("click",()=>playIdx(allTracks.indexOf(matches[i]))));
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
    const s = await api("GET","/api/status");
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

$("btn-run-dl").addEventListener("click", async () => {
  $("btn-run-dl").textContent="Starting…"; $("btn-run-dl").disabled=true;
  try {
    await api("POST","/api/download");
    toast("Download queue started");
    logActivity("Started download queue");
    refreshStatus();
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

/* ─── Init ─────────────────────────────────────────── */
loadLibrary();
refreshStatus();
