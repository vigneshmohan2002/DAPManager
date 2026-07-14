// @ts-check
        /**
         * @typedef {Object} SetupState
         * @property {number} currentStep
         * @property {string} role
         * @property {string} urlSource
         * @property {string=} savedPublicUrl
         * @property {string=} savedToken
         */
        const TOTAL_STEPS = 6;
        /** @type {SetupState} */
        const state = {
            currentStep: 1,
            role: 'master',
            urlSource: 'none',
        };

        const $ = (sel) => document.querySelector(sel);
        const $$ = (sel) => Array.from(document.querySelectorAll(sel));

        // --------- Stepper rendering ---------
        function renderStepper() {
            const labels = ['Role', 'Paths', 'URL', 'Integrations', 'Auth', 'Share'];
            const html = labels.map((label, i) => {
                const idx = i + 1;
                let cls = 'pip';
                if (idx === state.currentStep) cls += ' active';
                else if (idx < state.currentStep) cls += ' done';
                return `<div class="${cls}">${idx}. ${label}</div>`;
            }).join('');
            $('#stepper').innerHTML = html;
        }

        function applyRoleVisibility() {
            $$('[data-show-when]').forEach((el) => {
                const cond = el.getAttribute('data-show-when');
                el.style.display = matchesRoleCond(cond) ? '' : 'none';
            });
        }

        function matchesRoleCond(cond) {
            // cond is "role=master" or "role!=satellite" — minimal grammar.
            const m = cond.match(/^role(=|!=)(.+)$/);
            if (!m) return true;
            const op = m[1];
            const target = m[2].trim();
            return op === '=' ? state.role === target : state.role !== target;
        }

        function showStep(n) {
            state.currentStep = n;
            $$('.step-panel').forEach((el) => {
                el.classList.toggle('active', Number(el.dataset.step) === n);
            });
            // Once saved, Back from step 6 would let the user edit and
            // re-submit. Disable it so the wizard ends cleanly.
            $('#back-btn').disabled = n === 1 || n === 6;
            $('#next-btn').textContent =
                n === 5 ? 'Save & Continue' : n === 6 ? 'Finish' : 'Next';
            renderStepper();
            applyRoleVisibility();
            if (n === 3 && state.role !== 'satellite') {
                tryDetectUrl(false);
            }
            if (n === 6) {
                renderShareStep();
            }
        }

        // --------- Step 1: Role cards ---------
        function bindRoleCards() {
            const cards = $$('.role-card');
            const refresh = () => cards.forEach((c) =>
                c.classList.toggle('selected', c.dataset.role === state.role)
            );
            cards.forEach((c) => c.addEventListener('click', () => {
                state.role = c.dataset.role;
                refresh();
                applyRoleVisibility();
            }));
            refresh();
        }

        // --------- Step 2: path validation ---------
        async function validatePath(id, kind, optional) {
            const input = document.getElementById(id);
            const status = document.querySelector(`[data-status-for="${id}"]`);
            const value = input.value.trim();
            if (!value) {
                input.classList.toggle('invalid', !optional);
                status.className = 'field-status' + (optional ? '' : ' bad');
                status.textContent = optional ? '' : 'Required.';
                return optional;
            }
            try {
                const res = await fetch('/api/setup/validate-path', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: value, kind }),
                });
                const body = await res.json();
                if (body.ok) {
                    input.classList.remove('invalid');
                    status.className = 'field-status ok';
                    status.textContent = '✓ found';
                } else {
                    input.classList.add('invalid');
                    status.className = 'field-status bad';
                    status.textContent = body.message || 'invalid';
                }
                return body.ok;
            } catch (err) {
                status.className = 'field-status bad';
                status.textContent = 'check failed: ' + err;
                return false;
            }
        }

        function bindPathFields() {
            const items = [
                ['music_library_path', 'directory', false],
                ['downloads_path', 'directory', false],
                ['dap_mount_point', 'directory', true],
            ];
            for (const [id, kind, optional] of items) {
                const el = document.getElementById(id);
                el.addEventListener('blur', () => validatePath(id, kind, optional));
            }
        }

        // --------- Step 3: URL detect ---------
        async function tryDetectUrl(force) {
            const input = $('#public_master_url');
            if (!force && input.value.trim()) return;
            const hint = $('#url-source-hint');
            hint.textContent = 'Detecting…';
            try {
                const res = await fetch('/api/setup/detect-public-url');
                const body = await res.json();
                if (body.url) {
                    input.value = body.url;
                    state.urlSource = body.source;
                    hint.textContent = body.source === 'env'
                        ? 'Pulled from MASTER_PUBLIC_URL env (bootstrap script).'
                        : 'Pulled from in-container Tailscale.';
                } else {
                    hint.textContent = 'No suggestion — type the URL satellites should use.';
                }
            } catch (err) {
                hint.textContent = 'Detection failed: ' + err;
            }
        }

        // --------- Step 5: token gen ---------
        function generateToken() {
            const buf = new Uint8Array(24);
            crypto.getRandomValues(buf);
            const hex = Array.from(buf, (b) => b.toString(16).padStart(2, '0')).join('');
            $('#api_token').value = hex;
        }

        // --------- Per-step gating ---------
        async function canAdvanceFrom(step) {
            if (step === 1) return true;
            if (step === 2) {
                const a = await validatePath('music_library_path', 'directory', false);
                const b = await validatePath('downloads_path', 'directory', false);
                const c = await validatePath('dap_mount_point', 'directory', true);
                return a && b && c;
            }
            if (step === 3) {
                if (state.role === 'satellite') {
                    const v = $('#master_url').value.trim();
                    if (!v) {
                        flashError('Master URL is required for satellites.');
                        return false;
                    }
                    return true;
                }
                // Master/standalone: public_master_url is recommended but
                // not blocking — wizard accepts blank, server-side
                // /download/mac will refuse to serve if it's missing.
                return true;
            }
            if (step === 4) return true;
            return true;
        }

        function flashError(msg) {
            const el = $('#submit-error');
            el.textContent = msg;
            el.classList.remove('hidden');
            setTimeout(() => { el.classList.add('hidden'); }, 4000);
        }

        // --------- Submit ---------
        function collectPayload() {
            const v = (id) => (document.getElementById(id) || { value: '' }).value;
            const checked = (id) => !!(document.getElementById(id) || {}).checked;
            const base = {
                role: state.role,
                music_library_path: v('music_library_path').trim(),
                downloads_path: v('downloads_path').trim(),
                dap_mount_point: v('dap_mount_point').trim(),
                acoustid_api_key: v('acoustid_api_key'),
                contact_email: v('contact_email').trim(),
                api_token: v('api_token').trim(),
                jellyfin_url: v('jellyfin_url').trim(),
                jellyfin_api_key: v('jellyfin_api_key'),
                jellyfin_user_id: v('jellyfin_user_id').trim(),
            };
            if (state.role === 'satellite') {
                base.master_url = v('master_url').trim();
            } else {
                base.public_master_url = v('public_master_url').trim();
                base.slsk_username = v('slsk_username').trim();
                base.slsk_password = v('slsk_password');
            }
            if (state.role === 'master') {
                base.lidarr_enabled = checked('lidarr_enabled');
                base.lidarr_url = v('lidarr_url').trim();
                base.lidarr_api_key = v('lidarr_api_key');
            }
            return base;
        }

        async function submitConfig() {
            $('#next-btn').disabled = true;
            try {
                const payload = collectPayload();
                state.savedPublicUrl = payload.public_master_url || '';
                state.savedToken = payload.api_token || '';
                const res = await fetch('/api/save_config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const body = await res.json();
                if (body.success) {
                    showStep(6);
                    $('#next-btn').disabled = false;
                } else {
                    flashError('Save failed: ' + (body.message || 'unknown error'));
                    $('#next-btn').disabled = false;
                }
            } catch (err) {
                flashError('Network error: ' + err);
                $('#next-btn').disabled = false;
            }
        }

        // --------- Step 6: share rendering ---------
        async function renderShareStep() {
            if (state.role === 'satellite') return;

            const url = (state.savedPublicUrl || '').replace(/\/$/, '');
            const linkInput = $('#share-link');
            const warning = $('#share-warning');
            const qrHost = $('#qr-host');

            if (!url) {
                linkInput.value = '(public_master_url not set — open Settings to fill it in)';
                warning.textContent =
                    'Without a public URL, satellites can\'t reach this master. ' +
                    'You can add it later from Settings.';
                qrHost.style.display = 'none';
                $('#copy-link').disabled = true;
                return;
            }

            let shareUrl = url + '/download/mac';
            try {
                const headers = state.savedToken
                    ? { 'Authorization': 'Bearer ' + state.savedToken }
                    : {};
                const response = await fetch('/api/satellite-bundle-link', { headers });
                const payload = await response.json();
                if (!response.ok || !payload.success) throw new Error(payload.message || 'link unavailable');
                shareUrl = payload.url;
            } catch (e) {
                linkInput.value = '(could not create download link)';
                warning.textContent = String(e);
                qrHost.style.display = 'none';
                $('#copy-link').disabled = true;
                return;
            }
            linkInput.value = shareUrl;
            warning.textContent = state.savedToken
                ? 'This bundle-only link expires in one hour; refresh this page to mint another.'
                : 'Open mode (no token). Anyone on this Tailnet who can reach the link can download.';

            $('#copy-link').disabled = false;
            renderQr(shareUrl);
        }

        function renderQr(url) {
            const card = $('#qr-card');
            card.innerHTML = '';
            if (typeof window.QRCode === 'undefined') {
                $('#qr-host').style.display = 'none';
                return;
            }
            window.QRCode.toString(
                url,
                { type: 'svg', margin: 0, errorCorrectionLevel: 'M' },
                (err, svg) => {
                    if (err) {
                        $('#qr-host').style.display = 'none';
                        return;
                    }
                    card.innerHTML = svg;
                }
            );
        }

        async function copyShareLink() {
            const value = $('#share-link').value;
            const status = $('#copy-status');
            try {
                await navigator.clipboard.writeText(value);
                status.textContent = 'Copied.';
            } catch {
                status.textContent = 'Copy failed — select the text and copy manually.';
            }
            setTimeout(() => { status.innerHTML = '&nbsp;'; }, 2000);
        }

        // --------- Wire up ---------
        function init() {
            bindRoleCards();
            bindPathFields();
            $('#back-btn').addEventListener('click', () => {
                if (state.currentStep > 1) showStep(state.currentStep - 1);
            });
            $('#next-btn').addEventListener('click', async () => {
                if (state.currentStep === 5) {
                    submitConfig();
                    return;
                }
                if (state.currentStep === 6) {
                    window.location.href = state.savedToken
                        ? '/?token=' + encodeURIComponent(state.savedToken)
                        : '/';
                    return;
                }
                if (await canAdvanceFrom(state.currentStep)) {
                    showStep(state.currentStep + 1);
                }
            });
            $('#copy-link').addEventListener('click', copyShareLink);
            $('#redetect-url').addEventListener('click', () => tryDetectUrl(true));
            $('#generate-token').addEventListener('click', generateToken);
            showStep(1);
        }

        init();
