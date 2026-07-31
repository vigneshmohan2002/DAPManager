// @ts-check

function orphanElement(id) {
    const element = document.getElementById(id);
    if (!element) throw new Error(`Missing #${id}`);
    return element;
}

function orphanEscapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function selectTab(name) {
    document.querySelectorAll('.tab').forEach((tab) => {
        tab.classList.toggle('active', tab.getAttribute('data-tab') === name);
    });
    orphanElement('tab-tracks').style.display = name === 'tracks' ? '' : 'none';
    orphanElement('tab-playlists').style.display = name === 'playlists' ? '' : 'none';
}

async function reloadTracks() {
    const container = orphanElement('tracks-container');
    const countBadge = orphanElement('tracks-count');
    container.innerHTML = '<div class="empty">Loading…</div>';
    try {
        const response = await fetch('/api/orphans/tracks');
        const data = await response.json();
        if (!data.success) {
            container.innerHTML = `<div class="empty">Error: ${orphanEscapeHtml(data.message || 'unknown')}</div>`;
            return;
        }
        const rows = Array.isArray(data.tracks) ? data.tracks : [];
        countBadge.textContent = String(rows.length);
        if (!rows.length) {
            container.innerHTML = '<div class="empty">No orphan tracks.</div>';
            return;
        }
        let html = '<table class="orphan-table"><thead><tr>'
            + '<th>Artist</th><th>Title</th><th>Album</th>'
            + '<th>Deleted</th><th>On disk</th><th>Actions</th>'
            + '</tr></thead><tbody>';
        for (const row of rows) {
            const mbid = encodeURIComponent(String(row.mbid || ''));
            const hasFile = Boolean(row.local_path && String(row.local_path).trim());
            html += `<tr data-mbid="${orphanEscapeHtml(row.mbid)}">
                <td>${orphanEscapeHtml(row.artist)}</td>
                <td>${orphanEscapeHtml(row.title)}</td>
                <td>${orphanEscapeHtml(row.album)}</td>
                <td class="muted">${orphanEscapeHtml(row.deleted_at)}</td>
                <td class="muted" title="${orphanEscapeHtml(row.local_path)}">
                    ${hasFile ? 'yes' : '<span class="muted">—</span>'}
                </td>
                <td class="row-actions">
                    <button onclick="restoreTrack('${mbid}')">Restore</button>
                    ${hasFile ? `<button class="danger" onclick="deleteTrackFile('${mbid}')">Delete file</button>` : ''}
                    <button class="danger" onclick="purgeTrack('${mbid}')">Purge</button>
                </td>
            </tr>`;
        }
        container.innerHTML = html + '</tbody></table>';
    } catch (error) {
        container.innerHTML = `<div class="empty">Failed: ${orphanEscapeHtml(error)}</div>`;
    }
}

async function reloadPlaylists() {
    const container = orphanElement('playlists-container');
    const countBadge = orphanElement('playlists-count');
    container.innerHTML = '<div class="empty">Loading…</div>';
    try {
        const response = await fetch('/api/orphans/playlists');
        const data = await response.json();
        if (!data.success) {
            container.innerHTML = `<div class="empty">Error: ${orphanEscapeHtml(data.message || 'unknown')}</div>`;
            return;
        }
        const rows = Array.isArray(data.playlists) ? data.playlists : [];
        countBadge.textContent = String(rows.length);
        if (!rows.length) {
            container.innerHTML = '<div class="empty">No orphan playlists.</div>';
            return;
        }
        let html = '<table class="orphan-table"><thead><tr>'
            + '<th>Name</th><th>Tracks</th><th>Deleted</th><th>Actions</th>'
            + '</tr></thead><tbody>';
        for (const row of rows) {
            const playlistId = encodeURIComponent(String(row.playlist_id || ''));
            const trackCount = Number(row.track_count || 0);
            html += `<tr>
                <td>${orphanEscapeHtml(row.name)}</td>
                <td>${trackCount}</td>
                <td class="muted">${orphanEscapeHtml(row.deleted_at)}</td>
                <td class="row-actions">
                    <button onclick="restorePlaylist('${playlistId}')">Restore</button>
                    <button class="danger" onclick="purgePlaylist('${playlistId}', ${trackCount})">Purge</button>
                </td>
            </tr>`;
        }
        container.innerHTML = html + '</tbody></table>';
    } catch (error) {
        container.innerHTML = `<div class="empty">Failed: ${orphanEscapeHtml(error)}</div>`;
    }
}

async function orphanPostAndReload(url, method, reload, statusId, successMessage) {
    const status = orphanElement(statusId);
    status.textContent = '…';
    try {
        const response = await fetch(url, { method });
        const data = await response.json();
        if (!data.success) {
            status.textContent = `Error: ${data.message || response.status}`;
            return;
        }
        status.textContent = successMessage;
        await reload();
    } catch (error) {
        status.textContent = `Failed: ${String(error)}`;
    }
}

function restoreTrack(mbid) {
    void orphanPostAndReload(`/api/tracks/${mbid}/restore`, 'POST', reloadTracks, 'tracks-status', 'Restored.');
}

function purgeTrack(mbid) {
    if (!confirm('Permanently remove this track row? This cannot be undone.')) return;
    void orphanPostAndReload(`/api/tracks/${mbid}?purge=true`, 'DELETE', reloadTracks, 'tracks-status', 'Purged.');
}

function deleteTrackFile(mbid) {
    if (!confirm('Delete the file on disk? The orphan row stays until you Purge it.')) return;
    void orphanPostAndReload(`/api/tracks/${mbid}/file`, 'DELETE', reloadTracks, 'tracks-status', 'File deleted.');
}

function restorePlaylist(playlistId) {
    void orphanPostAndReload(`/api/playlists/${playlistId}/restore`, 'POST', reloadPlaylists, 'playlists-status', 'Restored.');
}

function purgePlaylist(playlistId, trackCount) {
    const extra = trackCount > 0
        ? ` (removes ${trackCount} membership row${trackCount === 1 ? '' : 's'})`
        : '';
    if (!confirm(`Permanently remove this playlist${extra}? This cannot be undone.`)) return;
    void orphanPostAndReload(`/api/playlists/${playlistId}?purge=true`, 'DELETE', reloadPlaylists, 'playlists-status', 'Purged.');
}

void reloadTracks();
void reloadPlaylists();
