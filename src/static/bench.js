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
        $('b_tracks').value = p.tracks; $('b_minsize').value = p.min_size;
        scheduleCheck();
    }
    function benchConfig() {
        const picked = id => Array.from($(id).querySelectorAll('input:checked')).map(i => i.value);
        return {models: picked('b_models'), sequences: picked('b_seqs'), tracks: parseInt($('b_tracks').value || '0'),
                min_size: parseInt($('b_minsize').value || '8')};
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
    // Runs tab: list of runs, one run's tables, Gantt of the objects
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
    // ---- one run: precision / recall / F1 / fps, and a Gantt of the objects over time for one model ----------------------
    const HELP = {
        precision: 'Of the frames where the tracker showed a box, the share where the box was on the target.',
        recall: 'Of the frames where the target was there, the share where the tracker had its box on it.',
        f1: 'Harmonic mean of precision and recall.',
        fps: 'Frames per second the tracker manages on the Pi (1000 / median ms per update).',
    };
    const TL = {caught: '#30c8d8', missed: '#ff453a', onTarget: '#30d158', falseBox: '#ff9f0a'};   // lost = red, false box = orange
    function el(tag, cls, ...kids) { const e = E(tag, null, cls); kids.forEach(k => k !== null && k !== undefined && e.append(k instanceof Node ? k : document.createTextNode(String(k)))); return e; }
    function swatch(m) { const s = E('span', '■ ', 'sw'); s.style.color = COLORS[m] || '#888'; return s; }
    function th(text, key) { const t = E('th', text); if (key) { t.title = HELP[key]; t.classList.add('helpable'); } return t; }
    const v3 = v => v === null || v === undefined ? '–' : v.toFixed(2);
    let tlModel = null;

    function renderRun() {
        const box = $('r_detail'); box.innerHTML = '';
        const d = detail; if (!d || !d.config) return;
        const cfgAll = d.config, c = cfgAll.config, p = d.progress || {}, res = d.results;
        box.appendChild(el('div', 'titleline', E('h2', `Run ${d.id}`), E('span', (p.state || '?').toLowerCase(), 'hint')));
        box.appendChild(E('div', `${cfgAll.created} · ${c.sequences.join(', ')} · ${cfgAll.runs.length} objects, ${cfgAll.frames} frames · ${fmtS(p.elapsed_s)}` +
                                 (res && res.complete === false ? ' · cancelled, partial' : ''), 'hint'));
        if (p.state === 'RUNNING') { box.appendChild(E('p', 'Still running — progress is on the Benchmark tab.', 'hint')); return; }
        if (!res) { box.appendChild(E('p', 'No results.', 'hint')); return; }
        if (res.version !== 2) { box.appendChild(E('p', 'Made by an older version of the benchmark (other metrics) — run it again to compare.', 'hint')); return; }
        const models = c.models.filter(m => res.models[m] && res.models[m].tasks);
        if (!models.includes(tlModel)) tlModel = models[0];

        // models: click a row to pick the model of the timeline
        const mt = E('table', null, 'grid clickable');
        mt.append(el('tr', null, th('model'), th('precision', 'precision'), th('recall', 'recall'), th('F1', 'f1'), th('fps', 'fps'), th('ms / frame')));
        models.forEach(m => {
            const v = res.models[m];
            const tr = el('tr', m === tlModel ? 'on' : null, el('td', null, swatch(m), m), el('td', 'n', v3(v.precision)), el('td', 'n', v3(v.recall)),
                          el('td', 'n', el('b', null, v3(v.f1))), el('td', 'n', v.fps), el('td', 'n', v.ms));
            tr.onclick = () => { tlModel = m; renderRun(); };
            mt.append(tr);
        });
        box.appendChild(mt);

        if (Object.keys(res.by_sequence).length > 1) {
            box.appendChild(E('h3', 'F1 by video'));
            const st = E('table', null, 'grid');
            const hr = el('tr', null, th('video')); models.forEach(m => hr.append(el('th', null, swatch(m), m))); st.append(hr);
            Object.entries(res.by_sequence).forEach(([s, mv]) => {
                const tr = el('tr', null, E('td', s)); models.forEach(m => tr.append(el('td', 'n', mv[m] && mv[m].tasks ? v3(mv[m].f1) : '–'))); st.append(tr);
            });
            box.appendChild(st);
        }

        // Gantt per video: one row per object (sorted by when it appears), recall over precision, for one model
        const tasks = {};
        res.tasks.forEach(r => { (tasks[r.task] = tasks[r.task] || {meta: r, m: {}}).m[r.model] = r; });
        const sel = E('select', null, 'small'); models.forEach(m => sel.append(Object.assign(E('option', m), {value: m}))); sel.value = tlModel;
        sel.onchange = () => { tlModel = sel.value; renderRun(); };
        box.appendChild(el('h3', 'ganttHead', 'Objects over time · ', sel));
        const leg = el('div', 'legend');
        [['recall:', null], [TL.caught, 'target caught'], [TL.missed, 'target lost'], ['precision:', null], [TL.onTarget, 'box on the target'], [TL.falseBox, 'false box'],
         ['grey = no target / no box on that frame · white tick = the tracker dropped its box', null]]
            .forEach(([a, b]) => { if (b === null) { leg.append(E('span', a, 'hint')); return; } const s = E('span', '■ '); s.style.color = a; leg.append(el('span', null, s, b)); });
        box.appendChild(leg);
        const gantt = el('div', 'gantt'); box.appendChild(gantt);
        const videoFrames = Object.fromEntries((cfgAll.per_sequence || []).map(s => [s.seq, s.frames_in_video]));
        getJ(`/bench/timelines?id=${d.id}&model=${tlModel}`).then(tl => {
            c.sequences.forEach(seq => {
                const rows = Object.values(tasks).filter(t => t.meta.seq === seq && tl[t.meta.task]).sort((a, b) => a.meta.start - b.meta.start || a.meta.track - b.meta.track);
                if (!rows.length) return;
                const n = Math.max(videoFrames[seq] || 0, ...rows.map(t => t.meta.start + 1 + tl[t.meta.task].recall.length));
                gantt.append(drawGantt(seq, rows, tl, n));
            });
        });
    }

    function drawGantt(seq, rows, tl, n) {
        // one video: recall Gantt on the left, precision Gantt on the right, the same objects in the same rows
        const wrap = el('div', 'ganttBox');
        wrap.append(el('div', 'ganttTitle', el('b', null, seq), E('span', `  ·  ${n} frames`, 'hint')));
        const pair = el('div', 'ganttPair'); wrap.append(pair);
        // equal plot widths, so a frame sits at the same x on both sides (only the left one has the object labels)
        const full = Math.max(600, (document.getElementById('r_detail').clientWidth || 1000) - 4), gapPx = 24, plot = Math.floor((full - gapPx - 140 - 16) / 2);
        const colR = {c: TL.caught, m: TL.missed}, colP = {g: TL.onTarget, f: TL.falseBox};
        pair.append(ganttCanvas('recall', rows, tl, n, 'recall', colR, 140 + plot + 8, true), ganttCanvas('precision', rows, tl, n, 'precision', colP, 8 + plot + 8, false));
        return wrap;
    }

    function ganttCanvas(title, rows, tl, n, key, cols, w, labels) {
        const L = labels ? 140 : 8, R = 8, top = 22, bar = 11, rowH = 17, hgt = top + rows.length * rowH + 20, ratio = window.devicePixelRatio || 1;
        const cv = E('canvas'); cv.width = w * ratio; cv.height = hgt * ratio; cv.style.width = w + 'px'; cv.style.height = hgt + 'px';
        const g = cv.getContext('2d'); g.scale(ratio, ratio);
        const css = getComputedStyle(document.documentElement), muted = css.getPropertyValue('--muted').trim(), line = css.getPropertyValue('--line').trim(),
              text = css.getPropertyValue('--text').trim(), field = css.getPropertyValue('--field').trim();
        const X = f => L + (w - L - R) * f / n;
        g.font = '600 12px system-ui, sans-serif'; g.fillStyle = muted; g.fillText(title.toUpperCase(), L, 13);
        g.font = '12px system-ui, sans-serif'; g.strokeStyle = line; g.lineWidth = 1;
        const step = [10, 20, 50, 100, 200, 500, 1000].find(s => n / s <= 8) || 1000;
        for (let f = 0; f <= n; f += step) { g.beginPath(); g.moveTo(X(f), top - 2); g.lineTo(X(f), hgt - 16); g.stroke(); g.fillStyle = muted; g.fillText(String(f), Math.min(X(f) - 4, w - 28), hgt - 3); }
        rows.forEach((t, i) => {
            const y = top + i * rowH, s = tl[t.meta.task][key], r = t.m[tlModel], start = t.meta.start + 1;
            if (labels) {
                g.fillStyle = muted; g.fillText(`object ${t.meta.track}`, 4, y + bar - 1);
                g.fillStyle = text; g.fillText(r && r.f1 !== null ? `F1 ${r.f1.toFixed(2)}` : '', 82, y + bar - 1);
            }
            g.fillStyle = field; g.fillRect(X(start), y, Math.max(1, X(start + s.length) - X(start)), bar);   // the object's lifetime
            let k = 0;
            while (k < s.length) {                         // runs of one state: one rectangle each
                let j = k; while (j < s.length && s[j] === s[k]) j++;
                if (cols[s[k]]) { g.fillStyle = cols[s[k]]; g.fillRect(X(start + k), y, Math.max(1, X(start + j) - X(start + k)), bar); }
                k = j;
            }
            if (r && r.stopped_at) { g.fillStyle = text; g.fillRect(X(start + r.stopped_at - 1) - 0.5, y - 2, 1.5, bar + 4); }
        });
        return cv;
    }

    //------------------------------------------------------------------------------------------------------------------
    const t0 = new URL(location).searchParams.get('tab');
    if (t0 === 'bench' || t0 === 'runs') showTab(t0, false);
    window.dtcBenchTab = () => tab;
})();
