// @ts-check

function fleetEscapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

async function loadSummary() {
    const container = document.getElementById('fleet-summary-container');
    if (!container) return;
    try {
        const response = await fetch('/api/fleet/summary');
        const data = await response.json();
        if (!data.success) {
            container.innerHTML = `<div class="empty">Error: ${fleetEscapeHtml(data.message || 'unknown')}</div>`;
            return;
        }
        const devices = Array.isArray(data.devices) ? data.devices : [];
        if (!devices.length) {
            container.innerHTML = '<div class="empty">No devices have reported inventory yet. Run "Report Inventory" on a device to populate this view.</div>';
            return;
        }
        let html = '<table class="fleet-table"><thead><tr>'
            + '<th>Device</th><th>Tracks</th><th>Last reported</th></tr></thead><tbody>';
        for (const device of devices) {
            html += `<tr>
                <td><strong>${fleetEscapeHtml(device.device_id)}</strong></td>
                <td>${Number(device.track_count || 0)}</td>
                <td class="muted">${fleetEscapeHtml(device.last_reported_at)}</td>
            </tr>`;
        }
        container.innerHTML = html + '</tbody></table>';
    } catch (error) {
        container.innerHTML = `<div class="empty">Failed to load: ${fleetEscapeHtml(error)}</div>`;
    }
}

async function searchFleet() {
    const input = document.getElementById('fleet-search-input');
    const output = document.getElementById('fleet-search-results');
    if (!(input instanceof HTMLInputElement) || !output) return;
    const query = input.value.trim();
    if (!query) {
        output.innerHTML = '<div class="muted">Enter a query first.</div>';
        return;
    }
    output.innerHTML = '<div class="muted">Searching…</div>';
    try {
        const response = await fetch(`/api/fleet/track?q=${encodeURIComponent(query)}`);
        const data = await response.json();
        if (!data.success) {
            output.innerHTML = `<div class="empty">Error: ${fleetEscapeHtml(data.message || 'unknown')}</div>`;
            return;
        }
        const results = Array.isArray(data.results) ? data.results : [];
        if (!results.length) {
            output.innerHTML = '<div class="empty">No matching tracks.</div>';
            return;
        }
        let html = '<table class="fleet-table"><thead><tr>'
            + '<th>Artist</th><th>Title</th><th>Album</th><th>Held by</th>'
            + '</tr></thead><tbody>';
        for (const row of results) {
            const holders = (Array.isArray(row.holders) ? row.holders : [])
                .map((holder) => `<span class="device-pill" title="${fleetEscapeHtml(holder.local_path)}">${fleetEscapeHtml(holder.device_id)}</span>`)
                .join('') || '<span class="muted">no device</span>';
            html += `<tr>
                <td>${fleetEscapeHtml(row.artist)}</td>
                <td>${fleetEscapeHtml(row.title)}</td>
                <td>${fleetEscapeHtml(row.album)}</td>
                <td>${holders}</td>
            </tr>`;
        }
        output.innerHTML = html + '</tbody></table>';
    } catch (error) {
        output.innerHTML = `<div class="empty">Failed: ${fleetEscapeHtml(error)}</div>`;
    }
}

const fleetSearchInput = document.getElementById('fleet-search-input');
fleetSearchInput?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') void searchFleet();
});

void loadSummary();
