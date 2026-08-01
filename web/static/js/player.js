// @ts-check
"use strict";

/**
 * @typedef {{mbid: string, title?: string, artist?: string, album?: string,
 *   album_id?: string, duration_seconds?: number}} PlayerTrack
 */

/* ── State ─────────────────────────────────────────────── */
/** @type {HTMLAudioElement} */
const audio = /** @type {HTMLAudioElement} */ (document.getElementById("audio"));
/** @type {PlayerTrack[]} */
let allTracks   = [];    // full library
/** @type {PlayerTrack[]} */
let viewTracks  = [];    // after search filter
let curIdx      = -1;    // index into viewTracks
let scrubbing   = false;

/* ── Utilities ─────────────────────────────────────────── */
function fmt(s) {
    if (!s || isNaN(s)) return "—";
    const m = Math.floor(+s / 60);
    return m + ":" + String(Math.floor(+s % 60)).padStart(2, "0");
}

function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ── Fetch library ─────────────────────────────────────── */
async function loadLibrary() {
    try {
        const r = await fetch("/api/library/tracks");
        if (!r.ok) throw new Error("HTTP " + r.status);
        const d = await r.json();
        allTracks  = d.tracks || [];
        viewTracks = allTracks;
        renderList();
    } catch (e) {
        document.getElementById("list-msg").textContent =
            "Could not load library: " + e.message;
    }
}

/* ── Render track rows ─────────────────────────────────── */
function renderList() {
    const container = document.getElementById("track-list");
    const msg       = document.getElementById("list-msg");

    if (!viewTracks.length) {
        container.innerHTML = "";
        msg.style.display = "";
        msg.textContent = allTracks.length ? "No results." : "Library is empty.";
        return;
    }
    msg.style.display = "none";

    const frag = document.createDocumentFragment();
    viewTracks.forEach((t, i) => {
        const playing = (i === curIdx);
        const row = document.createElement("div");
        row.className = "track-row" + (playing ? " is-playing" : "");
        row.dataset.i = i;
        row.innerHTML =
            `<div class="trk-idx${playing ? " active" : ""}">${playing ? "▶" : i + 1}</div>` +
            `<div class="trk-info">` +
              `<div class="trk-title">${esc(t.title || "Unknown")}</div>` +
              `<div class="trk-artist">${esc(t.artist || "—")}</div>` +
            `</div>` +
            `<div class="trk-dur">${fmt(t.duration_seconds)}</div>`;
        row.addEventListener("click", () => playIdx(i));
        frag.appendChild(row);
    });
    container.innerHTML = "";
    container.appendChild(frag);
}

/* ── Play a track by index ─────────────────────────────── */
function playIdx(idx) {
    if (idx < 0 || idx >= viewTracks.length) return;
    curIdx = idx;
    const t = viewTracks[idx];

    /* Update now-playing panel */
    document.getElementById("np-title").textContent  = t.title  || "Unknown";
    document.getElementById("np-artist").textContent =
        [t.artist, t.album].filter(Boolean).join(" · ") || "—";

    /* Cover art */
    const img = document.getElementById("cover-img");
    const ph  = document.getElementById("cover-ph");
    if (t.album_id) {
        img.src     = window.dapApiUrl("/api/library/albums/" + encodeURIComponent(t.album_id) + "/cover");
        img.style.display = "block";
        ph.style.display  = "none";
        img.onerror = () => { img.style.display = "none"; ph.style.display = ""; };
    } else {
        img.style.display = "none";
        ph.style.display  = "";
    }

    /* Load & play */
    audio.src = window.dapApiUrl("/api/stream/" + encodeURIComponent(t.mbid));
    audio.load();
    audio.play().catch(() => {});

    renderList();
    syncMediaSession(t);

    /* Scroll playing row into view */
    requestAnimationFrame(() => {
        const row = document.querySelector(".track-row.is-playing");
        if (row) row.scrollIntoView({ block: "nearest" });
    });
}

/* ── Controls ──────────────────────────────────────────── */
document.getElementById("btn-play").addEventListener("click", () => {
    if (curIdx < 0)       { playIdx(0); return; }
    if (audio.paused)     audio.play().catch(() => {});
    else                  audio.pause();
});

document.getElementById("btn-prev").addEventListener("click", () => {
    if (audio.currentTime > 3) { audio.currentTime = 0; return; }
    if (curIdx > 0)            playIdx(curIdx - 1);
});

document.getElementById("btn-next").addEventListener("click", () => {
    if (curIdx < viewTracks.length - 1) playIdx(curIdx + 1);
});

/* ── Audio events ──────────────────────────────────────── */
audio.addEventListener("play",  () => { toggle(true);  });
audio.addEventListener("pause", () => { toggle(false); });
audio.addEventListener("ended", () => {
    if (curIdx < viewTracks.length - 1) playIdx(curIdx + 1);
    else toggle(false);
});

function toggle(playing) {
    document.getElementById("ico-play").style.display  = playing ? "none" : "";
    document.getElementById("ico-pause").style.display = playing ? "" : "none";
}

/* ── Progress / scrub ──────────────────────────────────── */
audio.addEventListener("timeupdate", () => {
    if (scrubbing || !audio.duration || isNaN(audio.duration)) return;
    const pct = audio.currentTime / audio.duration;
    const s   = document.getElementById("scrubber");
    s.value   = Math.round(pct * 1000);
    s.style.setProperty("--pct", pct);
    document.getElementById("time-cur").textContent = fmt(audio.currentTime);
});

audio.addEventListener("loadedmetadata", () => {
    document.getElementById("time-dur").textContent = fmt(audio.duration);
});

const scrubber = document.getElementById("scrubber");
scrubber.addEventListener("pointerdown", () => { scrubbing = true; });
scrubber.addEventListener("pointerup",   () => { scrubbing = false; });
scrubber.addEventListener("input", e => {
    const pct = e.target.value / 1000;
    e.target.style.setProperty("--pct", pct);
    if (audio.duration && !isNaN(audio.duration)) {
        audio.currentTime = pct * audio.duration;
        document.getElementById("time-cur").textContent =
            fmt(pct * audio.duration);
    }
});

/* ── Search ────────────────────────────────────────────── */
document.getElementById("search").addEventListener("input", e => {
    const q = e.target.value.trim().toLowerCase();
    const playingTrack = allTracks[curIdx]; // keep identity across filter
    if (!q) {
        viewTracks = allTracks;
    } else {
        viewTracks = allTracks.filter(t =>
            (t.title  || "").toLowerCase().includes(q) ||
            (t.artist || "").toLowerCase().includes(q) ||
            (t.album  || "").toLowerCase().includes(q)
        );
    }
    curIdx = playingTrack ? viewTracks.indexOf(playingTrack) : -1;
    renderList();
});

/* ── Media Session (lock screen / AirPods / CarPlay) ───── */
function syncMediaSession(t) {
    if (!("mediaSession" in navigator)) return;
    navigator.mediaSession.metadata = new MediaMetadata({
        title:   t.title  || "Unknown",
        artist:  t.artist || "Unknown Artist",
        album:   t.album  || "",
        artwork: t.album_id ? [{
            src:   window.dapApiUrl("/api/library/albums/" + encodeURIComponent(t.album_id) + "/cover"),
            sizes: "512x512",
            type:  "image/jpeg"
        }] : []
    });
    navigator.mediaSession.setActionHandler("play",          () => audio.play().catch(() => {}));
    navigator.mediaSession.setActionHandler("pause",         () => audio.pause());
    navigator.mediaSession.setActionHandler("previoustrack", () => {
        if (curIdx > 0) playIdx(curIdx - 1);
    });
    navigator.mediaSession.setActionHandler("nexttrack",     () => {
        if (curIdx < viewTracks.length - 1) playIdx(curIdx + 1);
    });
    navigator.mediaSession.setActionHandler("seekto", d => {
        if (d.seekTime != null) audio.currentTime = d.seekTime;
    });
}

/* ── Init ──────────────────────────────────────────────── */
loadLibrary();
