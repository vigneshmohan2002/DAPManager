// @ts-check

const CONTRIBUTION_STATUS_META = {
    attempting: { label: 'Attempting', cls: 'text-[var(--color-text-muted)]' },
    have_better: { label: 'Have better', cls: 'text-[var(--color-accent)]' },
    satisfied: { label: 'Downloaded', cls: 'text-[var(--color-accent)]' },
    needs_upload: { label: 'Needs upload', cls: 'text-yellow-500' },
    ingested: { label: 'Ingested', cls: 'text-green-500' },
};

function contributionEscapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function contributionQualityLabel(quality) {
    if (!quality || typeof quality !== 'object') return '—';
    const tier = quality.lossless ? 'Lossless' : 'Lossy';
    const bits = quality.bits_per_sample ? `${quality.bits_per_sample}-bit/` : '';
    const sampleRate = quality.sample_rate ? `${(quality.sample_rate / 1000).toFixed(1)}kHz` : '';
    const bitrate = quality.bitrate ? ` · ${Math.round(quality.bitrate / 1000)}kbps` : '';
    return `${tier} ${bits}${sampleRate}${bitrate}`.trim();
}

async function loadContributions() {
    const container = document.getElementById('contrib-container');
    if (!container) return;
    container.innerHTML = '<div class="empty">Loading…</div>';
    try {
        const response = await fetch('/api/contributions');
        const data = await response.json();
        if (!data.success) {
            container.innerHTML = `<div class="empty">Error: ${contributionEscapeHtml(data.message || 'unknown')}</div>`;
            return;
        }
        const rows = Array.isArray(data.contributions) ? data.contributions : [];
        if (!rows.length) {
            container.innerHTML = '<div class="empty">No contributions yet. Run "Contribute" on a satellite to populate this view.</div>';
            return;
        }
        let html = '<table class="fleet-table"><thead><tr>'
            + '<th>Device</th><th>Track</th><th>Status</th>'
            + '<th>Promised</th><th>Master has</th><th>Updated</th>'
            + '</tr></thead><tbody>';
        for (const row of rows) {
            const meta = CONTRIBUTION_STATUS_META[row.status]
                || { label: String(row.status || ''), cls: '' };
            const track = `${contributionEscapeHtml(row.artist)} — ${contributionEscapeHtml(row.title)}`;
            html += `<tr>
                <td class="muted">${contributionEscapeHtml(row.device_id || '—')}</td>
                <td>${track}${row.album ? `<div class="muted text-xs">${contributionEscapeHtml(row.album)}</div>` : ''}</td>
                <td class="${meta.cls}">${contributionEscapeHtml(meta.label)}</td>
                <td class="muted">${contributionEscapeHtml(contributionQualityLabel(row.target_quality))}</td>
                <td class="muted">${contributionEscapeHtml(contributionQualityLabel(row.acquired_quality))}</td>
                <td class="muted">${contributionEscapeHtml(row.updated_at)}</td>
            </tr>`;
        }
        container.innerHTML = html + '</tbody></table>';
    } catch (error) {
        container.innerHTML = `<div class="empty">Failed to load: ${contributionEscapeHtml(error)}</div>`;
    }
}

void loadContributions();
