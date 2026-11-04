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
            fill($('b_order'), o.orders); fill($('b_starts'), o.starts);
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
        $('b_order').value = p.order; $('b_tracks').value = p.tracks; $('b_starts').value = p.starts;
        $('b_frames').value = p.max_frames; $('b_loss').checked = p.loss_rules;
        scheduleCheck();
    }
    function benchConfig() {
        const picked = id => Array.from($(id).querySelectorAll('input:checked')).map(i => i.value);
        return {models: picked('b_models'), sequences: picked('b_seqs'), order: $('b_order').value, tracks: parseInt($('b_tracks').value || '0'),
                starts: $('b_starts').value, max_frames: parseInt($('b_frames').value || '0'), loss_rules: $('b_loss').checked};
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
    // Runs tab: list, one run's tables, per-task IoU chart, compare with another run
    //------------------------------------------------------------------------------------------------------------------
    let selectedRun = new URL(location).searchParams.get('run'), runsList = [], detail = null, compare = null;
    function loadRuns() {
        getJ('/bench/runs').then(r => {
            runsList = r.runs;
            const ul = $('r_list'); ul.innerHTML = '';
            $('r_count').textContent = runsList.length + ' run(s)';
            runsList.forEach(x => {
                const li = E('li'); li.dataset.id = x.id;
                const top = E('div'); top.append(E('b', x.id + ' '), E('span', x.state || '?', 'pill ' + (x.state === 'DONE' ? 'good-static' : x.state === 'RUNNING' ? 'warn' : 'bad')));
                const auc = Object.entries(x.headline || {}).map(([m, v]) => `${m} ${num(v.auc)}`).join(' · ');
                li.append(top, E('div', (x.created || '') + ` · ${x.tasks} tasks`, 'hint'), E('div', auc || (x.models || []).join(', '), 'hint'));
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
        getJ('/bench/run?id=' + id).then(d => { detail = d; compare = null; renderRun(); });
    }
    function table(head, rows) {
        const t = E('table'); const tr = E('tr'); head.forEach(h => tr.appendChild(E('th', h))); t.appendChild(tr);
        rows.forEach(r => { const row = E('tr'); r.forEach(c => { const td = E('td'); if (c instanceof Node) td.appendChild(c); else td.textContent = num(c); row.appendChild(td); }); t.appendChild(row); });
        return t;
    }
    function delta(v, w, better) {
        if (v === null || v === undefined || w === null || w === undefined) return null;
        const d = v - w, s = E('span', (d >= 0 ? ' +' : ' ') + (Math.abs(d) < 10 ? d.toFixed(3) : d.toFixed(0)));
        s.className = Math.abs(d) < 1e-9 ? 'hint' : ((d > 0) === (better === 'higher') ? 'up' : 'down');
        return s;
    }
    function renderRun() {
        const box = $('r_detail'); box.innerHTML = '';
        const d = detail; if (!d) return;
        const c = (d.config || {}).config || {}, p = d.progress || {}, res = d.results;
        const h = E('div', null, 'runhead');
        h.append(E('h2', `Run ${d.id}`), E('span', p.state || '?', 'pill ' + (p.state === 'DONE' ? 'good-static' : p.state === 'RUNNING' ? 'warn' : 'bad')));
        box.appendChild(h);
        box.appendChild(E('div', `${d.config.created} · ${d.config.host} · DTC ${d.config.commit} (${d.config.branch}) · tools ${d.config.tools_commit} · ${fmtS(p.elapsed_s)} (estimate ${fmtS(d.config.estimate_s)})`, 'hint'));
        box.appendChild(E('div', `models ${c.models.join(', ')} · ${c.order} ${c.tracks || 'all'} track(s) · starts ${c.starts} · ≤ ${c.max_frames} frames · loss rules ${c.loss_rules ? 'on' : 'off'} · ${d.config.runs.length} tasks, ${d.config.frames} frames`, 'hint'));
        if (p.state === 'RUNNING') { box.appendChild(E('p', 'Still running - see the Benchmark tab for progress.', 'hint')); }
        if (!res) return;
        if (!res.complete) box.appendChild(E('p', 'Cancelled: the tables cover the finished tasks only.', 'hint warnText'));

        // compare with
        const cmpRow = E('div', null, 'row cmp'); const sel = E('select'); sel.appendChild(Object.assign(E('option', 'compare with: none'), {value: ''}));
        runsList.filter(x => x.id !== d.id && x.state === 'DONE').forEach(x => sel.appendChild(Object.assign(E('option', `run ${x.id} · ${x.created} · ${(x.models || []).join(', ')}`), {value: x.id})));
        if (compare) sel.value = compare.id;
        sel.onchange = () => { if (!sel.value) { compare = null; renderRun(); } else getJ('/bench/run?id=' + sel.value).then(x => { compare = x; renderRun(); }); };
        cmpRow.appendChild(sel); box.appendChild(cmpRow);
        const other = compare && compare.results ? compare.results.models : {};

        box.appendChild(E('h3', 'Models · mean over tasks'));
        const cols = [['auc', 'AUC', 'higher'], ['success', 'success', 'higher'], ['iou', 'IoU', 'higher'], ['precision20', 'prec@20px', 'higher'],
                      ['center_err', 'centre err px', 'lower'], ['lost', 'lost', 'lower'], ['ms', 'ms', 'lower'], ['ms_p95', 'ms p95', 'lower']]
            .concat(c.loss_rules ? [['caught', 'caught', 'higher'], ['missed', 'missed', 'lower'], ['early_stop', 'early stop', 'lower'], ['kept', 'kept', 'higher']] : []);
        box.appendChild(table(['model', 'tasks'].concat(cols.map(x => x[1])), Object.entries(res.models).map(([m, v]) => {
            const name = E('span', '■ ' + m); name.style.color = COLORS[m];
            return [name, v.tasks].concat(cols.map(([k, , b]) => { const s = E('span', num(v[k])); const dd = other[m] ? delta(v[k], other[m][k], b) : null; if (dd) s.appendChild(dd); return s; }));
        })));

        box.appendChild(E('h3', 'By sequence · AUC / ms'));
        box.appendChild(table(['sequence'].concat(c.models), Object.entries(res.by_sequence).map(([s, mv]) => [s].concat(c.models.map(m => mv[m] && mv[m].tasks ? `${num(mv[m].auc)} / ${num(mv[m].ms)}` : '–')))));

        box.appendChild(E('h3', 'Tasks · click a row for the IoU per frame'));
        const tasks = {};
        res.tasks.forEach(r => { (tasks[r.task] = tasks[r.task] || {meta: r, m: {}}).m[r.model] = r; });
        const tt = table(['#', 'sequence', 'target', 'start', 'frames', 'side px'].concat(c.models.map(m => m + ' AUC · lost at' + (c.loss_rules ? ' · stop' : ''))),
            Object.values(tasks).map(t => [t.meta.task + 1, t.meta.seq, t.meta.track, t.meta.start, t.meta.frames, t.meta.median_side]
                .concat(c.models.map(m => { const r = t.m[m]; return r ? `${num(r.auc)} · ${r.lost_at === null ? '–' : r.lost_at}` + (c.loss_rules ? ` · ${r.loss_verdict}` : '') : '–'; }))));
        tt.classList.add('clickable');
        Array.from(tt.querySelectorAll('tr')).slice(1).forEach((tr, k) => tr.onclick = () => {
            tt.querySelectorAll('tr').forEach(x => x.classList.remove('on')); tr.classList.add('on'); showSeries(Object.values(tasks)[k].meta);
        });
        const chart = E('div', null, 'chartbox'); chart.id = 'r_chart'; chart.appendChild(E('p', 'Pick a task.', 'hint'));
        box.append(chart, tt);

        box.appendChild(E('h3', 'Score on / off the target · p5 / median / p95 (a usable loss threshold separates them)'));
        box.appendChild(table(['model', 'on target (IoU ≥ 0.3)', 'off target (IoU < 0.1)'], c.models.map(m => {
            const rs = res.tasks.filter(r => r.model === m), med = k => { const v = rs.map(r => r[k]).filter(Boolean); if (!v.length) return '–'; return [0, 1, 2].map(i => (v.map(x => x[i]).sort((a, b) => a - b)[Math.floor(v.length / 2)])).join(' / '); };
            return [m, med('score_on'), med('score_off')];
        })));

        const det = E('details'); det.append(E('summary', 'Method and the full report (report.md)'), E('pre', d.report || '', 'report')); box.appendChild(det);
        box.appendChild(E('div', 'Files: runs/bench/' + d.id + '/ ' + d.files.join(', '), 'hint'));
    }
    function showSeries(meta) {
        const box = $('r_chart'); box.innerHTML = '';
        box.appendChild(E('div', `task ${meta.task + 1}: ${meta.seq} · target ${meta.track} · from frame ${meta.start} · ${meta.frames} frames · median side ${meta.median_side} px`, 'hint'));
        getJ(`/bench/series?id=${detail.id}&task=${meta.task}`).then(s => {
            const cv = E('canvas'); cv.width = 900; cv.height = 220; box.appendChild(cv);
            const g = cv.getContext('2d'), W = cv.width, H = cv.height, L = 34, B = 20, css = getComputedStyle(document.documentElement);
            g.strokeStyle = css.getPropertyValue('--line'); g.fillStyle = css.getPropertyValue('--muted'); g.font = '11px sans-serif';
            [0, 0.5, 1].forEach(v => { const y = H - B - v * (H - B - 8); g.beginPath(); g.moveTo(L, y); g.lineTo(W, y); g.stroke(); g.fillText(v.toFixed(1), 4, y + 4); });
            g.fillText('IoU', 4, 12); g.fillText('frame →', W - 50, H - 4);
            const n = meta.frames;
            Object.entries(s).forEach(([m, d]) => {
                g.strokeStyle = COLORS[m] || '#888'; g.lineWidth = 1.5; g.beginPath(); let pen = false;
                d.frame.forEach((f, k) => { const v = d.iou[k]; if (v === null) { pen = false; return; } const x = L + (W - L) * f / n, y = H - B - v * (H - B - 8); if (pen) g.lineTo(x, y); else g.moveTo(x, y); pen = true; });
                g.stroke();
            });
            const leg = E('div', null, 'hint'); Object.keys(s).forEach(m => { const sp = E('span', '■ ' + m + '  '); sp.style.color = COLORS[m]; leg.appendChild(sp); }); box.appendChild(leg);
        });
    }

    //------------------------------------------------------------------------------------------------------------------
    const t0 = new URL(location).searchParams.get('tab');
    if (t0 === 'bench' || t0 === 'runs') showTab(t0, false);
    window.dtcBenchTab = () => tab;
})();
