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
        $('b_frames').value = p.max_frames; $('b_minsize').value = p.min_size; $('b_loss').checked = p.loss_rules;
        scheduleCheck();
    }
    function benchConfig() {
        const picked = id => Array.from($(id).querySelectorAll('input:checked')).map(i => i.value);
        return {models: picked('b_models'), sequences: picked('b_seqs'), order: $('b_order').value, tracks: parseInt($('b_tracks').value || '0'),
                starts: $('b_starts').value, max_frames: parseInt($('b_frames').value || '0'), min_size: parseInt($('b_minsize').value || '8'), loss_rules: $('b_loss').checked};
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
                const chips = E('div', null, 'chips');
                (x.models || []).forEach(m => { const v = (x.headline || {})[m]; const ch = E('span', `${m} ${v && v.auc !== null && v.auc !== undefined ? v.auc : ''}`, 'chip'); ch.style.borderColor = COLORS[m] || '#888'; chips.append(ch); });
                li.append(top, E('div', (x.created || '') + ` · ${x.tasks} tasks`, 'hint'), chips);
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
    // ---- one run -----------------------------------------------------------------------------------------------------
    const HELP = {
        auc: 'Accuracy (AUC): how well the box overlaps the truth, averaged over all overlap thresholds. 0 = never on the target, 1 = perfect. The main number.',
        success: 'On target: share of frames where the box overlaps the truth by at least half (IoU >= 0.5).',
        precision20: 'Centre within 20 px: share of frames where the box centre is at most 20 px from the true centre. The fair measure for tiny targets.',
        center_err: 'Centre error: median distance between the box centre and the true centre, px. Lower is better.',
        ms: 'Speed: median time of one tracker update on the Pi, ms per frame (fps = 1000 / ms).',
        lost: 'Lost: in how many tasks the box left the target (overlap < 0.1 for 5 frames in a row).',
    };
    const fps = ms => ms ? Math.round(1000 / ms) : null;
    const pct = v => v === null || v === undefined ? '–' : Math.round(100 * v) + '%';
    function el(tag, cls, ...kids) { const e = E(tag, null, cls); kids.forEach(k => k !== null && k !== undefined && e.append(k instanceof Node ? k : document.createTextNode(String(k)))); return e; }
    function swatch(m) { const s = E('span', '■', 'sw'); s.style.color = COLORS[m] || '#888'; return s; }
    function help(key, text) { const s = E('span', text, 'helpable'); s.title = HELP[key]; return s; }
    function arrow(v, w, better, digits) {
        if (v === null || v === undefined || w === null || w === undefined) return null;
        const d = v - w; if (Math.abs(d) < Math.pow(10, -(digits || 3))) return E('span', ' =', 'hint');
        return E('span', ` ${d > 0 ? '▲' : '▼'}${Math.abs(d).toFixed(digits === undefined ? 3 : digits)}`, (d > 0) === (better === 'higher') ? 'up' : 'down');
    }
    function heat(v) { return v === null || v === undefined ? '' : `rgba(48,209,88,${(0.08 + 0.42 * Math.max(0, Math.min(1, v))).toFixed(2)})`; }

    function renderRun() {
        const box = $('r_detail'); box.innerHTML = '';
        const d = detail; if (!d || !d.config) return;
        const cfgAll = d.config, c = cfgAll.config, p = d.progress || {}, res = d.results;
        const models = c.models.filter(m => res && res.models[m] && res.models[m].tasks);
        const other = compare && compare.results ? compare.results.models : {};

        // header: run, state, one readable line; compare + details on the right
        const head = el('div', 'runhead2');
        const left = el('div', null, el('div', 'titleline', E('h2', `Run ${d.id}`), E('span', p.state || '?', 'pill ' + (p.state === 'DONE' ? 'good-static' : p.state === 'RUNNING' ? 'warn' : 'bad'))),
                        E('div', `${cfgAll.created} · ${cfgAll.runs.length} tasks on ${c.sequences.length} sequence(s) · ${cfgAll.frames} frames · took ${fmtS(p.elapsed_s)}`, 'hint'));
        const sel = E('select', null, 'small'); sel.appendChild(Object.assign(E('option', 'Compare with…'), {value: ''}));
        runsList.filter(x => x.id !== d.id && x.state === 'DONE').forEach(x => sel.appendChild(Object.assign(E('option', `run ${x.id} · ${(x.models || []).join(', ')}`), {value: x.id})));
        if (compare) sel.value = compare.id;
        sel.onchange = () => { if (!sel.value) { compare = null; renderRun(); } else getJ('/bench/run?id=' + sel.value).then(x => { compare = x; renderRun(); }); };
        head.append(left, sel); box.appendChild(head);

        if (p.state === 'RUNNING') { box.appendChild(E('p', 'Still running — the Benchmark tab shows its progress.', 'note')); return; }
        if (!res || !models.length) { box.appendChild(E('p', 'No results in this run.', 'note')); return; }

        // warnings the reader needs before reading the numbers
        const sides = res.tasks.map(r => r.median_side).sort((a, b) => a - b), side = sides[Math.floor(sides.length / 2)];
        const tiny = side < 12, nTasks = cfgAll.runs.length;
        const notes = [];
        if (!res.complete) notes.push(['bad', 'Cancelled: only the finished tasks are counted.']);
        if (tiny) notes.push(['warn', `The targets are tiny (median ${side} px): box overlap (accuracy, on-target) is unreliable at this size — compare the models by "centre within 20 px" and the centre error.`]);
        if (nTasks < 3) notes.push(['warn', `Only ${nTasks} task(s): a single unlucky moment changes the result — run more tracks / sequences before concluding.`]);
        if (compare) notes.push(['info', `▲▼ = difference to run ${compare.id}.`]);
        notes.forEach(([k, t]) => box.appendChild(E('div', t, 'note ' + k)));

        // verdict
        const accKey = tiny ? 'precision20' : 'auc';
        const byAcc = [...models].sort((a, b) => (res.models[b][accKey] || 0) - (res.models[a][accKey] || 0));
        const bySpeed = [...models].sort((a, b) => (res.models[a].ms || 1e9) - (res.models[b].ms || 1e9));
        const best = byAcc[0], fast = bySpeed[0], bm = res.models[best];
        const accText = m => tiny ? `centre within 20 px on ${pct(res.models[m].precision20)} of frames` : `accuracy ${res.models[m].auc}`;
        const verdict = el('div', 'verdict');
        if (models.length === 1) verdict.append(el('div', 'big', swatch(best), ` ${best}: ${accText(best)}, ${bm.ms} ms/frame (${fps(bm.ms)} fps)`));
        else if (best === fast) verdict.append(el('div', 'big', swatch(best), ` ${best} is best here: the most accurate (${accText(best)}) and the fastest (${bm.ms} ms/frame)`));
        else {
            verdict.append(el('div', 'big', swatch(best), ` Most accurate: ${best} — ${accText(best)}, ${bm.ms} ms/frame`));
            verdict.append(el('div', 'big2', swatch(fast), ` Fastest: ${fast} — ${res.models[fast].ms} ms/frame, ${accText(fast)}`));
        }
        box.appendChild(verdict);

        // one card per model
        const cards = el('div', 'mcards');
        models.forEach(m => {
            const v = res.models[m], o = other[m];
            const card = el('div', 'mcard'); card.style.borderTopColor = COLORS[m] || '#888';
            card.append(el('div', 'mname', swatch(m), ' ' + m));
            const main = tiny ? ['precision20', 'centre within 20 px', pct(v.precision20), v.precision20] : ['auc', 'accuracy', v.auc, v.auc];
            const big = el('div', 'mbig', main[2]); const a = o ? arrow(v[main[0]], o[main[0]], 'higher') : null; if (a) big.append(a);
            card.append(el('div', 'mlabel', help(main[0], main[1])), big);
            const bar = el('div', 'mbar'); const fill = el('div', 'mfill'); fill.style.width = Math.round(100 * (main[3] || 0)) + '%'; fill.style.background = COLORS[m]; bar.append(fill); card.append(bar);
            const row = (key, label, value, extra) => el('div', 'mrow', help(key, label), el('b', null, value, extra || null));
            card.append(row('ms', 'speed', `${v.ms} ms · ${fps(v.ms)} fps`, o ? arrow(v.ms, o.ms, 'lower', 1) : null));
            if (!tiny) card.append(row('success', 'on target', pct(v.success)), row('precision20', 'centre within 20 px', pct(v.precision20)));
            card.append(row('center_err', 'centre error', `${v.center_err} px`), row('lost', 'lost the target', `${v.lost} of ${v.tasks} tasks`));
            if (c.loss_rules) card.append(el('div', 'mrow', help('lost', 'own loss detection'), el('b', null, `${v.caught} caught · ${v.early_stop} too early · ${v.missed} missed`)));
            cards.append(card);
        });
        box.appendChild(cards);

        // accuracy vs speed
        if (models.length > 1) {
            box.appendChild(E('h3', tiny ? 'Centre within 20 px ↔ speed' : 'Accuracy ↔ speed  (up = more accurate, left = faster)'));
            const wrap = el('div', 'chartbox'); box.appendChild(wrap);
            scatter(wrap, models.map(m => ({m, x: res.models[m].ms, y: res.models[m][accKey]})));
        }

        // by sequence: best cell of a row highlighted
        box.appendChild(E('h3', 'By sequence'));
        const st = E('table', null, 'grid');
        const hr = E('tr'); hr.append(E('th', 'sequence')); models.forEach(m => hr.append(el('th', null, swatch(m), ' ' + m))); st.append(hr);
        Object.entries(res.by_sequence).forEach(([s, mv]) => {
            const tr = E('tr'); tr.append(E('td', s));
            const vals = models.map(m => mv[m] && mv[m].tasks ? mv[m][accKey] : null), top = Math.max(...vals.filter(v => v !== null));
            models.forEach((m, k) => {
                const v = mv[m]; const td = E('td');
                if (!v || !v.tasks) td.textContent = '–';
                else { td.append(el('b', null, tiny ? pct(v.precision20) : v.auc), E('span', `  ${v.ms} ms`, 'hint')); td.style.background = heat(vals[k]); if (vals[k] === top && models.length > 1) td.classList.add('best'); }
                tr.append(td);
            });
            st.append(tr);
        });
        box.appendChild(st);

        // tasks + per-frame chart
        box.appendChild(E('h3', 'Tasks — pick one to see the overlap frame by frame'));
        const chart = el('div', 'chartbox'); chart.id = 'r_chart'; box.appendChild(chart);
        const tasks = {};
        res.tasks.forEach(r => { (tasks[r.task] = tasks[r.task] || {meta: r, m: {}}).m[r.model] = r; });
        const tt = E('table', null, 'grid clickable');
        const th = E('tr'); ['#', 'sequence', 'target', 'from frame', 'frames', 'size px'].forEach(h => th.append(E('th', h)));
        models.forEach(m => th.append(el('th', null, swatch(m), ` ${m}`))); tt.append(th);
        const list = Object.values(tasks);
        list.forEach((t, k) => {
            const tr = E('tr'); [t.meta.task + 1, t.meta.seq, t.meta.track, t.meta.start, t.meta.frames, t.meta.median_side].forEach(v => tr.append(E('td', v)));
            models.forEach(m => {
                const r = t.m[m], td = E('td');
                if (r) {
                    td.append(el('b', null, tiny ? pct(r.precision20) : r.auc));
                    td.append(E('span', r.lost_at === null ? '  kept it' : `  lost at ${r.lost_at}`, 'hint'));
                    if (c.loss_rules) td.append(E('span', `  · ${r.loss_verdict.replace('_', ' ')}`, 'hint'));
                    td.style.background = heat(tiny ? r.precision20 : r.auc);
                } else td.textContent = '–';
                tr.append(td);
            });
            tr.onclick = () => { tt.querySelectorAll('tr').forEach(x => x.classList.remove('on')); tr.classList.add('on'); showSeries(t); };
            tt.append(tr);
        });
        box.appendChild(tt);
        if (list.length) { tt.querySelectorAll('tr')[1].classList.add('on'); showSeries(list[0]); }

        // details, folded
        const det = E('details', null, 'more'); det.append(E('summary', 'Details: method, settings, tracker scores, files'));
        det.append(E('p', `Settings: ${c.order} ${c.tracks || 'all'} track(s) per sequence, at least ${c.min_size} px and ${c.min_frames} frames, start(s): ${c.starts}, at most ${c.max_frames} frames per task, frames wider than ${c.max_width} px downscaled, loss rules ${c.loss_rules ? 'on' : 'off'}. Sequences: ${c.sequences.join(', ')}.`, 'hint'));
        det.append(E('p', `Machine ${cfgAll.host} · DTC ${cfgAll.commit} (${cfgAll.branch}) · tools ${cfgAll.tools_commit} · estimate was ${fmtS(cfgAll.estimate_s)}.`, 'hint'));
        det.append(E('div', 'Tracker score on / off the target (p5 / median / p95) — a model can detect its own loss from its score only if the two ranges do not overlap:', 'hint'));
        const sc = E('table', null, 'grid'); const sh = E('tr'); ['model', 'on the target', 'off the target'].forEach(h => sh.append(E('th', h))); sc.append(sh);
        models.forEach(m => {
            const rs = res.tasks.filter(r => r.model === m), med = k => { const v = rs.map(r => r[k]).filter(Boolean); return v.length ? [0, 1, 2].map(i => v.map(x => x[i]).sort((a, b) => a - b)[Math.floor(v.length / 2)]).join(' / ') : '–'; };
            const tr = E('tr'); tr.append(el('td', null, swatch(m), ' ' + m), E('td', med('score_on')), E('td', med('score_off'))); sc.append(tr);
        });
        det.append(sc, E('pre', d.report || '', 'report'), E('div', `Files on the Pi: runs/bench/${d.id}/ — ${d.files.join(', ')}`, 'hint'));
        box.appendChild(det);
    }

    function canvasIn(box, h) {
        const cv = E('canvas'), w = Math.max(320, box.clientWidth || 800), r = window.devicePixelRatio || 1;
        cv.width = w * r; cv.height = h * r; cv.style.width = w + 'px'; cv.style.height = h + 'px';
        box.appendChild(cv); const g = cv.getContext('2d'); g.scale(r, r);
        const css = getComputedStyle(document.documentElement);
        return {g, w, h, line: css.getPropertyValue('--line').trim(), muted: css.getPropertyValue('--muted').trim(), text: css.getPropertyValue('--text').trim()};
    }
    function scatter(box, pts) {
        const {g, w, h, line, muted, text} = canvasIn(box, 260), L = 52, B = 34, T = 14, R = 24;
        const xs = pts.map(p => Math.log10(Math.max(1, p.x))), x0 = Math.floor(Math.min(...xs) * 2) / 2 - 0.25, x1 = Math.ceil(Math.max(...xs) * 2) / 2 + 0.25;
        const X = v => L + (w - L - R) * (Math.log10(Math.max(1, v)) - x0) / (x1 - x0), Y = v => h - B - (h - B - T) * v;
        g.font = '12px system-ui, sans-serif'; g.strokeStyle = line; g.fillStyle = muted; g.lineWidth = 1;
        [0, 0.25, 0.5, 0.75, 1].forEach(v => { g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(w - R, Y(v)); g.stroke(); g.fillText(v.toFixed(2), 8, Y(v) + 4); });
        [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000].filter(v => Math.log10(v) >= x0 && Math.log10(v) <= x1).forEach(v => {
            g.beginPath(); g.moveTo(X(v), T); g.lineTo(X(v), h - B); g.stroke(); g.fillText(v + ' ms', X(v) - 12, h - B + 16); });
        g.fillText('ms per frame on the Pi (log scale) →', w - R - 210, h - 4);
        pts.forEach(p => {
            if (p.y === null || p.y === undefined) return;
            g.fillStyle = COLORS[p.m] || '#888'; g.beginPath(); g.arc(X(p.x), Y(p.y), 7, 0, 2 * Math.PI); g.fill();
            g.fillStyle = text; g.font = '600 13px system-ui, sans-serif'; g.fillText(`${p.m}  ${p.y} · ${p.x} ms`, X(p.x) + 11, Y(p.y) + 4); g.font = '12px system-ui, sans-serif';
        });
    }
    function showSeries(t) {
        const box = $('r_chart'); box.innerHTML = ''; const meta = t.meta;
        box.appendChild(E('div', `Task ${meta.task + 1}: ${meta.seq}, target ${meta.track}, from frame ${meta.start}, ${meta.frames} frames, target ≈ ${meta.median_side} px. Overlap with the truth (IoU) per frame; dashed line = where the model lost it.`, 'hint'));
        getJ(`/bench/series?id=${detail.id}&task=${meta.task}`).then(s => {
            const {g, w, h, line, muted} = canvasIn(box, 230), L = 40, B = 26, T = 10, R = 12, n = meta.frames;
            const X = f => L + (w - L - R) * f / n, Y = v => h - B - (h - B - T) * v;
            g.font = '12px system-ui, sans-serif'; g.strokeStyle = line; g.fillStyle = muted; g.lineWidth = 1;
            [0, 0.5, 1].forEach(v => { g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(w - R, Y(v)); g.stroke(); g.fillText(v.toFixed(1), 8, Y(v) + 4); });
            for (let f = 0; f <= n; f += Math.max(10, Math.round(n / 10 / 10) * 10)) g.fillText(String(f), X(f) - 6, h - 8);
            const models = detail.config.config.models.filter(m => s[m]);
            models.forEach(m => {
                const d = s[m]; g.strokeStyle = COLORS[m] || '#888'; g.lineWidth = 1.8; g.setLineDash([]); g.beginPath(); let pen = false;
                d.frame.forEach((f, k) => { const v = d.iou[k]; if (v === null) { pen = false; return; } if (pen) g.lineTo(X(f), Y(v)); else g.moveTo(X(f), Y(v)); pen = true; });
                g.stroke();
                const lost = t.m[m] && t.m[m].lost_at;
                if (lost) { g.setLineDash([4, 4]); g.lineWidth = 1.2; g.beginPath(); g.moveTo(X(lost), T); g.lineTo(X(lost), h - B); g.stroke(); g.setLineDash([]); }
            });
            const leg = el('div', 'legend'); models.forEach(m => { const r = t.m[m]; leg.append(el('span', null, swatch(m), ` ${m} ${r ? (r.lost_at === null ? 'kept it' : 'lost at ' + r.lost_at) : ''}`)); }); box.appendChild(leg);
        });
    }

    //------------------------------------------------------------------------------------------------------------------
    const t0 = new URL(location).searchParams.get('tab');
    if (t0 === 'bench' || t0 === 'runs') showTab(t0, false);
    window.dtcBenchTab = () => tab;
})();
