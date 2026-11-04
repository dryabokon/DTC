// Benchmark and Runs tabs of the DTC web UI (click-to-track model benchmark: utils_bench.py, bench_worker.py).
(function () {
    'use strict';
    const $ = id => document.getElementById(id);
    const E = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined && text !== null) e.textContent = text; if (cls) e.className = cls; return e; };
    const postJ = (url, body) => fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})}).then(r => r.json());
    const getJ = url => fetch(url).then(r => r.json());
    const COLORS = {NANO: '#0a84ff', VIT: '#30d158', SIAMBAN: '#ff9f0a', SIAMFC: '#bf5af2', MIL: '#8e8e93'};
    const fmtS = s => { s = Math.max(0, Math.round(s || 0)); return s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.floor(s % 3600 / 60)} min` : s >= 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s} s`; };
    const num = v => (v === null || v === undefined || v === '') ? '–' : v;

    //------------------------------------------------------------------------------------------------------------------
    // tabs
    //------------------------------------------------------------------------------------------------------------------
    const feed = $('cameraFeed'), feedSrc = feed.getAttribute('src');
    let tab = 'live';
    function showTab(t, push) {
        tab = t;
        document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === t));
        $('liveView').hidden = t !== 'live'; $('benchView').hidden = t !== 'bench'; $('runsView').hidden = t !== 'runs';
        if (t === 'live') { if (!feed.getAttribute('src')) feed.setAttribute('src', feedSrc); }       // the MJPEG stream only while it is seen
        else feed.removeAttribute('src');
        if (t === 'bench') initBench();
        if (t === 'runs') loadRuns();
        if (push !== false) { const u = new URL(location); u.searchParams.set('tab', t); if (t !== 'runs') u.searchParams.delete('run'); history.replaceState(null, '', u); }
    }
    document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => { showTab(b.dataset.tab); b.blur(); });
    // a hidden browser tab / minimised window does not watch either: drop the stream, take it back when visible again
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) feed.removeAttribute('src');
        else if (tab === 'live' && !feed.getAttribute('src')) feed.setAttribute('src', feedSrc);
    });

    //------------------------------------------------------------------------------------------------------------------
    // Benchmark tab: preset -> parameters -> readiness + estimate (automatic) -> Run -> progress
    //------------------------------------------------------------------------------------------------------------------
    let opts = null, checkTimer = null, lastCheck = null, pollTimer = null, watching = null;
    function initBench() {
        if (opts) { pollProgress(); return; }
        getJ('/bench/options').then(o => {
            opts = o;
            const ps = $('b_preset'); ps.innerHTML = '';
            Object.entries(o.presets).forEach(([k, v]) => { const op = E('option', v.label); op.value = k; ps.appendChild(op); });
            const op = E('option', 'Custom'); op.value = ''; ps.appendChild(op);
            const ms = $('b_models'); ms.innerHTML = '';
            o.models.forEach(m => {
                const l = E('label', null, 'check'); const i = E('input'); i.type = 'checkbox'; i.value = m.name; i.disabled = !m.ok;
                const sw = E('span', '■'); sw.style.color = COLORS[m.name] || 'inherit';
                l.append(i, sw, document.createTextNode(` ${m.label}`), E('span', ` ~${m.pi_ms} ms` + (m.ok ? '' : ' · no weights'), 'hint'));
                ms.appendChild(l);
            });
            const ss = $('b_seqs'); ss.innerHTML = '';
            o.sequences.forEach(s => {
                const l = E('label', null, 'check'); const i = E('input'); i.type = 'checkbox'; i.value = s.id;
                l.append(i, document.createTextNode(' ' + s.label)); ss.appendChild(l);
            });
            const fill = (sel, values) => { sel.innerHTML = ''; values.forEach(v => { const x = E('option', v); x.value = v; sel.appendChild(x); }); };
            const tr = $('b_tracks'); tr.innerHTML = ''; o.objects.forEach(v => { const x = E('option', v ? String(v) : 'all'); x.value = v; tr.appendChild(x); });
            applyPreset('quick');
            $('b_preset').onchange = e => { if (e.target.value) applyPreset(e.target.value); };
            $('benchForm').addEventListener('change', e => { if (e.target.id !== 'b_preset') { $('b_preset').value = ''; } scheduleCheck(); });
            $('benchForm').addEventListener('input', e => { if (e.target.type === 'number') { $('b_preset').value = ''; scheduleCheck(); } });
            $('b_all').onclick = () => { $('b_seqs').querySelectorAll('input').forEach(i => i.checked = true); $('b_preset').value = ''; scheduleCheck(); };
            $('b_none').onclick = () => { $('b_seqs').querySelectorAll('input').forEach(i => i.checked = false); $('b_preset').value = ''; scheduleCheck(); };
            $('b_run').onclick = startBench;
            $('b_cancel').onclick = () => { $('b_cancel').disabled = true; postJ('/bench/cancel', {id: watching}).then(pollProgress); };
            pollProgress();
        });
    }
    function applyPreset(k) {
        const p = opts.presets[k]; $('b_preset').value = k;
        $('b_models').querySelectorAll('input').forEach(i => i.checked = p.models.includes(i.value) && !i.disabled);
        $('b_seqs').querySelectorAll('input').forEach(i => i.checked = p.sequences === '*' || p.sequences.includes(i.value));
        $('b_tracks').value = p.tracks; $('b_minsize').value = p.min_size; $('b_loss').checked = p.loss_rules;
        scheduleCheck();
    }
    function benchConfig() {
        const picked = id => Array.from($(id).querySelectorAll('input:checked')).map(i => i.value);
        return {models: picked('b_models'), sequences: picked('b_seqs'), tracks: parseInt($('b_tracks').value || '0'),
                min_size: parseInt($('b_minsize').value || '8'), loss_rules: $('b_loss').checked};
    }
    function scheduleCheck() { clearTimeout(checkTimer); $('b_estimate').textContent = 'checking ...'; checkTimer = setTimeout(runCheck, 350); }
    function runCheck() {
        const cfg = benchConfig();
        postJ('/bench/check', cfg).then(r => {
            lastCheck = r;
            const box = $('b_checks'); box.innerHTML = '';
            r.checks.filter(c => c.key !== 'estimate').forEach(c => {
                const row = E('div', null, 'chk ' + c.status);
                row.append(E('span', c.status === 'ok' ? '✓' : c.status === 'warn' ? '!' : '✕', 'mark'), E('b', c.key + ' '), E('span', c.detail, 'hint'));
                box.appendChild(row);
            });
            $('b_estimate').innerHTML = r.estimate ? `<b>${r.tasks}</b> tasks · <b>${r.frames}</b> frames × ${cfg.models.length} model(s) · about <b>${r.estimate}</b>` : 'Fix the settings';
            $('b_run').disabled = !r.ok || watchingRunning;
        });
    }
    function startBench() {
        $('b_run').disabled = true;
        postJ('/bench/start', benchConfig()).then(r => {
            if (!r.ok) { $('b_estimate').textContent = 'Error: ' + r.error; $('b_run').disabled = false; return; }
            watching = r.id; pollProgress();
        });
    }
    let watchingRunning = false;
    function pollProgress() {
        clearTimeout(pollTimer);
        getJ('/bench/progress' + (watching ? '?id=' + watching : '')).then(r => {
            const p = r.progress;
            renderProgress(p);
            watchingRunning = !!(p && p.state === 'RUNNING');
            $('b_run').disabled = watchingRunning || !(lastCheck && lastCheck.ok);
            $('benchForm').querySelectorAll('input, select, button').forEach(x => { if (x.id !== 'b_run') x.disabled = watchingRunning; });
            if (opts) $('b_models').querySelectorAll('input').forEach((i, k) => { if (!opts.models[k].ok) i.disabled = true; });
            if (watchingRunning && tab === 'bench') pollTimer = setTimeout(pollProgress, 1000);
        });
    }
    function renderProgress(p) {
        const box = $('b_progress');
        if (!p) { box.hidden = true; return; }
        watching = p.id; box.hidden = false;
        const pct = Math.round(100 * (p.done_frac || 0));
        $('b_bar').style.width = pct + '%';
        $('b_bar').className = 'bar ' + (p.state === 'RUNNING' ? '' : p.state === 'DONE' ? 'good' : 'bad');
        $('b_state').textContent = `Run ${p.id} · ${p.state}` + (p.state === 'RUNNING' ? ` · ${pct}%` : '');
        $('b_eta').textContent = p.state === 'RUNNING'
            ? `about ${fmtS(p.left_s)} left · elapsed ${fmtS(p.elapsed_s)} · first estimate ${fmtS(p.estimate_s)}`
            : `took ${fmtS(p.elapsed_s)} · ${p.stage}`;
        $('b_current').textContent = p.current
            ? `task ${p.current.task} / ${p.tasks_total} · ${p.current.label} · frame ${p.current.frame} / ${p.current.frames}`
            : `${p.tasks_done || 0} / ${p.tasks_total || 0} tasks · ${p.frames_done || 0} / ${p.frames_total || 0} frames`;
        $('b_cancel').hidden = p.state !== 'RUNNING'; $('b_cancel').disabled = false;
        const open = $('b_open'); open.hidden = p.state === 'RUNNING';
        open.textContent = `Open run ${p.id} results →`; open.onclick = () => { selectedRun = p.id; showTab('runs'); };
    }

    //------------------------------------------------------------------------------------------------------------------
    // Runs tab: list of runs, one run's tables, per-task IoU chart
    //------------------------------------------------------------------------------------------------------------------
    let selectedRun = new URL(location).searchParams.get('run'), runsList = [], detail = null;
    function loadRuns() {
        getJ('/bench/runs').then(r => {
            runsList = r.runs;
            const ul = $('r_list'); ul.innerHTML = '';
            $('r_count').textContent = runsList.length + ' run(s)';
            runsList.forEach(x => {
                const li = E('li'); li.dataset.id = x.id;
                const top = E('div'); top.append(E('b', 'Run ' + x.id), E('span', x.state === 'DONE' ? '' : '  ' + (x.state || '?').toLowerCase(), 'hint'));
                li.append(top, E('div', `${x.created || ''} · ${(x.models || []).join(', ')}`, 'hint'));
                li.onclick = () => selectRun(x.id);
                ul.appendChild(li);
            });
            if (!selectedRun && runsList.length) selectedRun = runsList[0].id;
            if (selectedRun) selectRun(selectedRun); else $('r_detail').innerHTML = '<p class="hint">No benchmark runs yet - start one in the Benchmark tab.</p>';
        });
    }
    $('r_refresh').onclick = loadRuns;
    function selectRun(id) {
        selectedRun = id;
        document.querySelectorAll('#r_list li').forEach(li => li.classList.toggle('on', li.dataset.id === id));
        const u = new URL(location); u.searchParams.set('tab', 'runs'); u.searchParams.set('run', id); history.replaceState(null, '', u);
        getJ('/bench/run?id=' + id).then(d => { detail = d; renderRun(); });
    }
    // ---- one run: plain numbers, no judgement ------------------------------------------------------------------------
    const HELP = {
        auc: 'Accuracy (AUC): overlap of the box with the truth averaged over all overlap thresholds; 0 = never on the target, 1 = perfect.',
        success: 'Share of frames where the box overlaps the truth by at least half (IoU ≥ 0.5).',
        lost: 'In how many tasks the tracker left the target and did not come back to it.',
        ms: 'Median time of one tracker update on the Pi.',
    };
    const fps = ms => ms ? Math.round(1000 / ms) : '–';
    const pct = v => v === null || v === undefined ? '–' : Math.round(100 * v) + '%';
    function el(tag, cls, ...kids) { const e = E(tag, null, cls); kids.forEach(k => k !== null && k !== undefined && e.append(k instanceof Node ? k : document.createTextNode(String(k)))); return e; }
    function swatch(m) { const s = E('span', '■ ', 'sw'); s.style.color = COLORS[m] || '#888'; return s; }
    function th(text, key) { const t = E('th', text); if (key) { t.title = HELP[key]; t.classList.add('helpable'); } return t; }

    function renderRun() {
        const box = $('r_detail'); box.innerHTML = '';
        const d = detail; if (!d || !d.config) return;
        const cfgAll = d.config, c = cfgAll.config, p = d.progress || {}, res = d.results;
        const models = c.models.filter(m => res && res.models[m] && res.models[m].tasks);

        box.appendChild(el('div', 'titleline', E('h2', `Run ${d.id}`), E('span', (p.state || '?').toLowerCase(), 'hint')));
        box.appendChild(E('div', `${cfgAll.created} · ${c.sequences.join(', ')} · ${cfgAll.runs.length} tasks, ${cfgAll.frames} frames · ${fmtS(p.elapsed_s)}` +
                                 (res && !res.complete ? ' · cancelled, partial' : ''), 'hint'));
        if (p.state === 'RUNNING') { box.appendChild(E('p', 'Still running — progress is on the Benchmark tab.', 'hint')); return; }
        if (!res || !models.length) { box.appendChild(E('p', 'No results.', 'hint')); return; }

        // models
        const mt = E('table', null, 'grid');
        mt.append(el('tr', null, th('model'), th('accuracy', 'auc'), th('on target', 'success'), th('lost', 'lost'), th('ms / frame', 'ms'), th('fps')));
        models.forEach(m => {
            const v = res.models[m];
            mt.append(el('tr', null, el('td', null, swatch(m), m), el('td', 'n', v.auc), el('td', 'n', pct(v.success)),
                         el('td', 'n', `${v.lost} / ${v.tasks}`), el('td', 'n', v.ms), el('td', 'n', fps(v.ms))));
        });
        box.appendChild(mt);

        // by sequence
        if (Object.keys(res.by_sequence).length > 1) {
            box.appendChild(E('h3', 'Accuracy by sequence'));
            const st = E('table', null, 'grid');
            const hr = el('tr', null, th('sequence')); models.forEach(m => hr.append(el('th', null, swatch(m), m))); st.append(hr);
            Object.entries(res.by_sequence).forEach(([s, mv]) => {
                const tr = el('tr', null, E('td', s));
                models.forEach(m => tr.append(el('td', 'n', mv[m] && mv[m].tasks ? mv[m].auc : '–')));
                st.append(tr);
            });
            box.appendChild(st);
        }

        // tasks + overlap per frame
        box.appendChild(E('h3', 'Tasks'));
        const chart = el('div', 'chartbox'); chart.id = 'r_chart';
        const tasks = {};
        res.tasks.forEach(r => { (tasks[r.task] = tasks[r.task] || {meta: r, m: {}}).m[r.model] = r; });
        const tt = E('table', null, 'grid clickable');
        const hr = el('tr', null, th('#'), th('sequence'), th('target'), th('from frame'), th('frames'), th('size px'));
        models.forEach(m => hr.append(el('th', null, swatch(m), m))); tt.append(hr);
        const list = Object.values(tasks);
        list.forEach(t => {
            const tr = E('tr'); [t.meta.task + 1, t.meta.seq, t.meta.track, t.meta.start, t.meta.frames, t.meta.median_side].forEach(v => tr.append(E('td', v)));
            models.forEach(m => { const r = t.m[m]; tr.append(el('td', 'n', r ? r.auc : '–', r && r.lost_at !== null ? E('span', `  lost at ${r.lost_at}`, 'hint') : null)); });
            tr.onclick = () => { tt.querySelectorAll('tr').forEach(x => x.classList.remove('on')); tr.classList.add('on'); showSeries(t); };
            tt.append(tr);
        });
        box.append(tt, chart);
        if (list.length) { tt.querySelectorAll('tr')[1].classList.add('on'); showSeries(list[0]); }

        const det = E('details', null, 'more'); det.append(E('summary', 'Method, settings, full report'));
        det.append(E('pre', d.report || '', 'report'), E('div', `runs/bench/${d.id}/ — ${d.files.join(', ')}`, 'hint'));
        box.appendChild(det);
    }

    function showSeries(t) {
        const box = $('r_chart'); box.innerHTML = ''; const meta = t.meta;
        box.appendChild(E('div', `Task ${meta.task + 1} · ${meta.seq} · target ${meta.track}: overlap with the truth per frame (1 = exact, 0 = off the target)`, 'hint'));
        getJ(`/bench/series?id=${detail.id}&task=${meta.task}`).then(s => {
            const cv = E('canvas'), w = Math.max(320, (box.clientWidth || 800) - 4), hgt = 200, r = window.devicePixelRatio || 1;
            cv.width = w * r; cv.height = hgt * r; cv.style.width = w + 'px'; cv.style.height = hgt + 'px'; box.appendChild(cv);
            const g = cv.getContext('2d'); g.scale(r, r);
            const css = getComputedStyle(document.documentElement), L = 34, B = 22, T = 8, R = 10, n = meta.frames;
            const X = f => L + (w - L - R) * f / n, Y = v => hgt - B - (hgt - B - T) * v;
            g.font = '12px system-ui, sans-serif'; g.strokeStyle = css.getPropertyValue('--line'); g.fillStyle = css.getPropertyValue('--muted'); g.lineWidth = 1;
            [0, 0.5, 1].forEach(v => { g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(w - R, Y(v)); g.stroke(); g.fillText(v.toFixed(1), 6, Y(v) + 4); });
            for (let f = 0; f <= n; f += Math.max(10, Math.round(n / 100) * 10)) g.fillText(String(f), X(f) - 6, hgt - 6);
            const models = detail.config.config.models.filter(m => s[m]);
            models.forEach(m => {
                const d = s[m]; g.strokeStyle = COLORS[m] || '#888'; g.lineWidth = 1.6; g.beginPath(); let pen = false;
                d.frame.forEach((f, k) => { const v = d.iou[k]; if (v === null) { pen = false; return; } if (pen) g.lineTo(X(f), Y(v)); else g.moveTo(X(f), Y(v)); pen = true; });
                g.stroke();
            });
            const leg = el('div', 'legend'); models.forEach(m => leg.append(el('span', null, swatch(m), m))); box.appendChild(leg);
        });
    }

    //------------------------------------------------------------------------------------------------------------------
    const t0 = new URL(location).searchParams.get('tab');
    if (t0 === 'bench' || t0 === 'runs') showTab(t0, false);
    window.dtcBenchTab = () => tab;
})();
