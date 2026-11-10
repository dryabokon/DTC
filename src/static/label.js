// Label tab of the DTC web UI: one object per frame, the operator draws a box, the click-to-track tracker follows it
// (server: utils_label.py, routes /label/*). Boxes travel in source-video pixels; frames come from the server's JPEG cache.
(function () {
    'use strict';
    const $ = id => document.getElementById(id);
    const postJ = (url, body) => fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})})
        .then(r => r.json()).catch(e => ({ok: false, error: String(e)}));
    const getJ = url => fetch(url).then(r => r.json());
    const SRC = {m: 'manual', t: 'track', i: 'interp', a: 'absent'};
    const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

    const cv = $('l_canvas'), g = cv.getContext('2d'), tl = $('l_timeline');
    let S = null, L = [], ver = -1, cur = 0, shown = false, inited = false, pollTimer = null, loadingLabels = false;
    let drag = null;                                 // {x0, y0, x1, y1, shift} in canvas px while drawing a box
    let userStopped = false;                         // the operator stopped tracking: keep the cursor where they are
    let playTimer = null;                            // P: review playback of the labels
    const imgs = new Map();                          // frame index -> Image (a small cache around the cursor)

    //------------------------------------------------------------------------------------------------------------------
    function init() {
        inited = true;
        getJ('/label/sources').then(d => {
            fillSources(d.sources, d.state);
            const m = $('l_model'); m.innerHTML = '';
            d.models.forEach(x => { const o = document.createElement('option'); o.value = x.name; o.textContent = x.label; m.appendChild(o); });
            m.value = d.state ? d.state.model : d.default_model;
            const sp = $('l_speed'); sp.innerHTML = '';
            d.speeds.forEach(v => {
                const b = document.createElement('button'); b.type = 'button'; b.dataset.v = v; b.textContent = v ? v + '×' : 'max';
                b.title = v ? `${v} × the video's frame rate, so you can see where it drifts` : 'as fast as the tracker goes';
                b.onclick = () => { setSpeed(v); b.blur(); };
                sp.appendChild(b);
            });
            markSpeed(d.state ? d.state.speed : 1);
            if (d.state) setState(d.state);
            poll();
        });
    }
    // the list shows what is open: a label project is listed under its own name (refetched after opening a new video)
    function fillSources(list, st) {
        const sel = $('l_source');
        sel.innerHTML = '';
        const o0 = document.createElement('option'); o0.value = ''; o0.textContent = '— pick a video —'; sel.appendChild(o0);
        list.forEach(s => { const o = document.createElement('option'); o.value = s.name; o.textContent = s.label; sel.appendChild(o); });
        sel.value = st && [...sel.options].some(o => o.value === st.name) ? st.name : '';
        typedInList = false;
    }
    function refreshSources() { getJ('/label/sources').then(d => fillSources(d.sources, d.state)); }
    // typing while the list has the focus (a click that missed the path field) must not open another video:
    // a pick by keyboard opens on Enter only, a pick by mouse at once
    let typedInList = false;
    $('l_source').addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); typedInList = false; openVideo(e.target.value); e.target.blur(); }
        else typedInList = true;
    });
    function markSpeed(v) { document.querySelectorAll('#l_speed button').forEach(b => b.classList.toggle('on', +b.dataset.v === +v)); }
    function setSpeed(v) { markSpeed(v); if (S) postJ('/label/speed', {speed: v}); else pendingSpeed = v; }
    let pendingSpeed = null;

    const refreshedFor = {};                          // a new project shows up in the list once it has labels
    function status(text, bad) {
        $('l_status').textContent = text || ''; $('l_status').style.color = bad ? 'var(--bad)' : '';
        if (bad) $('l_status').dataset.err = 1; else delete $('l_status').dataset.err;
    }

    function openVideo(spec) {
        spec = (spec || '').trim(); if (!spec) return;
        status('opening ' + spec + ' …');
        postJ('/label/open', {source: spec, model: $('l_model').value}).then(d => {
            if (!d.ok) { status('Error: ' + d.error, true); return; }
            imgs.clear(); ver = -1; L = []; cur = 0;
            setState(d.state); status(''); refreshSources();
            if (pendingSpeed !== null) { setSpeed(pendingSpeed); pendingSpeed = null; }
            poll();
        });
    }
    $('l_source').addEventListener('change', e => {
        if (typedInList) { status('Enter: open ' + e.target.value); return; }
        openVideo(e.target.value); e.target.blur();
    });
    $('l_path').addEventListener('keydown', e => { if (e.key === 'Enter') { openVideo(e.target.value); e.target.blur(); } });
    $('l_upload').addEventListener('change', e => {
        const f = e.target.files[0]; if (!f) return;
        const fd = new FormData(); fd.append('file', f); fd.append('model', $('l_model').value);
        status(`uploading ${f.name} (${(f.size / 1e6).toFixed(1)} MB) …`);
        fetch('/label/upload', {method: 'POST', body: fd}).then(r => r.json()).then(d => {
            if (!d.ok) { status('Error: ' + d.error, true); return; }
            imgs.clear(); ver = -1; L = []; cur = 0; setState(d.state); status(''); refreshSources(); poll();
        }).catch(err => status('Error: ' + err, true));
        e.target.value = '';
    });
    $('l_model').addEventListener('change', e => { if (S) postJ('/label/model', {model: e.target.value}).then(d => { if (!d.ok) status('Error: ' + d.error, true); }); e.target.blur(); });

    //------------------------------------------------------------------------------------------------------------------
    function setState(st) {
        const first = !S || S.name !== st.name, wasTracking = S && S.tracking;
        S = st;
        if (first) { cur = 0; fit(); }
        if (S.tracking && S.track_pos !== null) cur = S.track_pos;          // follow the tracker
        if (wasTracking && !S.tracking && S.track_pos !== null && !userStopped) cur = S.track_pos;   // land on the frame where it stopped
        if (!S.tracking) userStopped = false;
        if (S.version !== ver && !loadingLabels) loadLabels();
        else if (S.decoding || first) drawTimeline();         // the grey "not decoded yet" part shrinks
        info(); draw();
    }
    function loadLabels() {
        loadingLabels = true;
        getJ('/label/labels').then(d => { L = d.labels || []; ver = d.version; loadingLabels = false; info(); draw(); drawTimeline(); })
            .catch(() => { loadingLabels = false; });
    }
    function poll() {
        clearTimeout(pollTimer);
        if (!shown) return;
        getJ('/label/state').then(d => {
            if (d.state) setState(d.state);
            pollTimer = setTimeout(poll, d.state && (d.state.tracking || d.state.decoding) ? 120 : 800);
        }).catch(() => { pollTimer = setTimeout(poll, 2000); });
    }
    window.dtcLabelShown = on => {
        shown = on;
        if (on && !inited) init(); else if (on) poll();
        if (on) setTimeout(() => { fit(); draw(); drawTimeline(); }, 0);
    };

    //------------------------------------------------------------------------------------------------------------------
    // frame + box
    //------------------------------------------------------------------------------------------------------------------
    function frameImg(i) {
        if (!S || i < 0 || i >= S.decoded) return null;
        let im = imgs.get(i);
        if (!im) {
            im = new Image(); im.onload = () => { if (i === cur) draw(); }; im.src = `/label/frame?i=${i}&n=${encodeURIComponent(S.name)}`;
            imgs.set(i, im);
            if (imgs.size > 400) for (const k of imgs.keys()) { if (Math.abs(k - cur) > 60) imgs.delete(k); if (imgs.size <= 300) break; }
        }
        return im;
    }
    function prefetch() { if (!S) return; for (let d = 1; d <= 6; d++) { frameImg(cur + d); if (d <= 3) frameImg(cur - d); } }

    function fit() {                                  // canvas = the cache frame size, shown as large as the stage allows
        if (!S) return;
        const st = $('l_stage'), W = st.clientWidth - 20, H = st.clientHeight - 20;
        const k = Math.min(1280 / S.width, 1);
        cv.width = Math.round(S.width * k); cv.height = Math.round(S.height * k);
        const s = Math.max(0.1, Math.min(W / cv.width, H / cv.height));
        cv.style.width = Math.floor(cv.width * s) + 'px'; cv.style.height = Math.floor(cv.height * s) + 'px';
    }
    window.addEventListener('resize', () => { if (shown) { fit(); draw(); drawTimeline(); } });

    const k = () => S ? cv.width / S.width : 1;     // source px -> canvas px
    function prevBox(i) { for (let j = i - 1; j >= Math.max(0, i - 300); j--) { const l = L[j]; if (l) return l.length >= 5 ? l : null; } return null; }

    function draw() {
        if (!S) { g.clearRect(0, 0, cv.width, cv.height); return; }
        const im = frameImg(cur);
        if (im && im.complete && im.naturalWidth) g.drawImage(im, 0, 0, cv.width, cv.height);
        else if (!im) { g.fillStyle = '#000'; g.fillRect(0, 0, cv.width, cv.height); }
        const l = L[cur], s = k(), lw = Math.max(2, cv.width / 500);
        if (l && l.length >= 5) {
            const col = {m: '#00e5ff', t: '#30d158', i: '#ff9f0a'}[l[0]] || '#fff';
            g.strokeStyle = col; g.lineWidth = lw; g.setLineDash([]);
            g.strokeRect(l[1] * s, l[2] * s, (l[3] - l[1]) * s, (l[4] - l[2]) * s);
        } else if (l && l[0] === 'a') {
            g.fillStyle = 'rgba(0,0,0,.55)'; g.fillRect(0, 0, 230 * cv.width / 1280, 34 * cv.width / 1280);
            g.fillStyle = '#ff9f0a'; g.font = `${Math.round(18 * cv.width / 1280)}px system-ui, sans-serif`; g.fillText('no object on this frame', 10 * cv.width / 1280, 23 * cv.width / 1280);
        } else {
            const p = prevBox(cur);                   // unlabeled: where it was, as a hint
            if (p) { g.strokeStyle = 'rgba(255,255,255,.55)'; g.lineWidth = lw / 1.5; g.setLineDash([6, 5]); g.strokeRect(p[1] * s, p[2] * s, (p[3] - p[1]) * s, (p[4] - p[2]) * s); g.setLineDash([]); }
        }
        if (drag) { g.strokeStyle = '#00e5ff'; g.lineWidth = lw; g.setLineDash([5, 4]); g.strokeRect(Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1), Math.abs(drag.x1 - drag.x0), Math.abs(drag.y1 - drag.y0)); g.setLineDash([]); }
        prefetch();
        drawCursorOnTimeline();
        drawLoupe();
    }

    //------------------------------------------------------------------------------------------------------------------
    // loupe: the box of this frame, enlarged, next to the box of the key frame it came from - is it still the same object?
    //------------------------------------------------------------------------------------------------------------------
    function keyBefore(i) {                           // nearest key frame with a box at or before i (else the first one after)
        for (let j = Math.min(i, L.length - 1); j >= 0; j--) { const l = L[j]; if (l && l[0] === 'm') return j; }
        for (let j = i + 1; j < L.length; j++) { const l = L[j]; if (l && l[0] === 'm') return j; }
        return -1;
    }
    function crop(canvas, i, box, col) {
        const c = canvas.getContext('2d'), W = canvas.width, H = canvas.height;
        c.fillStyle = cssVar('--field'); c.fillRect(0, 0, W, H);
        const im = i >= 0 && box ? frameImg(i) : null;
        if (!im || !im.complete || !im.naturalWidth) return;
        const s = k(), bw = (box[2] - box[0]) * s, bh = (box[3] - box[1]) * s, cx = (box[0] + box[2]) / 2 * s, cy = (box[1] + box[3]) / 2 * s;
        const z = Math.min(W / (bw * 2.0), H / (bh * 1.6));            // the box plus as much context again around it
        const sw = W / z, sh = H / z;
        c.imageSmoothingEnabled = true;
        c.drawImage(im, cx - sw / 2, cy - sh / 2, sw, sh, 0, 0, W, H);
        c.strokeStyle = col; c.lineWidth = 2;
        c.strokeRect(W / 2 - bw * z / 2, H / 2 - bh * z / 2, bw * z, bh * z);
    }
    function drawLoupe() {
        if (!S) return;
        const l = L[cur], kf = keyBefore(cur), kl = kf >= 0 ? L[kf] : null;
        const nowBox = l && l.length >= 5 ? l.slice(1, 5) : (prevBox(cur) || []).slice(1, 5);
        crop($('l_ref'), kf, kl ? kl.slice(1, 5) : null, '#00e5ff');
        crop($('l_now'), cur, nowBox.length ? nowBox : null, l && l.length >= 5 ? ({m: '#00e5ff', t: '#30d158', i: '#ff9f0a'}[l[0]]) : 'rgba(255,255,255,.6)');
        $('l_ref_t').textContent = kf >= 0 ? `key frame ${kf}` : 'no key frame yet';
        $('l_now_t').textContent = `frame ${cur}` + (l && l.length >= 5 ? '' : l && l[0] === 'a' ? ' · no object' : ' · not labeled');
    }

    function info() {
        if (!S) return;
        $('l_frame').textContent = S.decoding ? `frame ${cur}  (decoding: ${S.decoded} frames so far)` : `frame ${cur} / ${S.frames - 1}`;
        const l = L[cur];
        let kind = l ? SRC[l[0]] : 'not labeled';
        if (l && l[0] === 't' && l[5] !== null && l[5] !== undefined) kind += `  · appearance ${l[5]}`;
        if (l && l[6]) kind += '  · check this frame';
        if (S.tracking) kind = `tracking ${S.model} …  ` + kind;
        $('l_kind').textContent = kind;
        $('l_why').textContent = !S.tracking && S.stop_reason ? 'stopped: ' + S.stop_reason : (S.decode_error ? S.decode_error : '');
        const c = S.counts, pct = v => S.frames ? ` (${(100 * v / S.frames).toFixed(0)}%)` : '';
        $('l_counts').innerHTML = '';
        [['#00e5ff', 'key frames (yours)', c.manual], ['#30d158', 'tracked', c.track], ['#ff9f0a', 'interpolated', c.interp], ['#8e8e93', 'no object', c.absent],
         [null, 'labeled', c.labeled + ' / ' + S.frames + pct(c.labeled)]].forEach(([col, name, v]) => {
            const d = document.createElement('div'), a = document.createElement('span'), b = document.createElement('b');
            if (col) { const sw = document.createElement('span'); sw.className = 'swatch'; sw.style.background = col; a.appendChild(sw); }
            a.appendChild(document.createTextNode(name)); b.textContent = v; d.append(a, b); $('l_counts').appendChild(d);
        });
        $('l_out').textContent = 'saved to ' + S.out_dir.replace(/\\/g, '/');
        if (!$('l_status').dataset.err) $('l_status').textContent = 'open: ' + S.source.split(/[\\/]/).slice(-2).join('/');
        if (S.counts.labeled && $('l_source').value !== S.name && !refreshedFor[S.name]) { refreshedFor[S.name] = 1; refreshSources(); }
        if ($('l_model').value !== S.model && document.activeElement !== $('l_model')) $('l_model').value = S.model;
        markSpeed(S.speed);
    }

    //------------------------------------------------------------------------------------------------------------------
    // timeline: one colour per frame state, the cursor, click / drag to go to a frame
    //------------------------------------------------------------------------------------------------------------------
    let tlBase = null;
    function drawTimeline() {
        if (!S || !shown) return;
        const W = tl.clientWidth, H = 26, n = Math.max(1, S.frames);
        tl.width = W; tl.height = H;
        const x = tl.getContext('2d');
        x.fillStyle = cssVar('--field'); x.fillRect(0, 0, W, H);
        const col = {m: '#00e5ff', t: '#30d158', i: '#ff9f0a', a: '#8e8e93'};
        for (let px = 0; px < W; px++) {              // the most "decided" state in the frames under this pixel
            const a = Math.floor(px * n / W), b = Math.max(a + 1, Math.floor((px + 1) * n / W));
            let best = null;
            for (let i = a; i < b && i < L.length; i++) { const l = L[i]; if (!l) continue; if (l[0] === 'm') { best = 'm'; break; } if (!best || l[0] === 't') best = l[0]; }
            if (best) { x.fillStyle = col[best]; x.fillRect(px, best === 'm' ? 0 : 6, 1, best === 'm' ? H : H - 12); }
        }
        x.fillStyle = '#ff453a';                      // where the tracker stopped by itself or jumped: look here (, .)
        for (let i = 0; i < L.length; i++) { const l = L[i]; if (l && l[6]) x.fillRect(Math.min(W - 3, Math.floor(i * W / n)) - 1, 0, 3, H); }
        if (S.decoding) { x.fillStyle = 'rgba(128,128,128,.25)'; x.fillRect(W * S.decoded / n, 0, W, H); }
        tlBase = x.getImageData(0, 0, W, H);
        drawCursorOnTimeline();
    }
    function drawCursorOnTimeline() {
        if (!tlBase || !S) return;
        const x = tl.getContext('2d'); x.putImageData(tlBase, 0, 0);
        const px = Math.round(cur * tl.width / Math.max(1, S.frames));
        x.fillStyle = cssVar('--text'); x.fillRect(px - 1, 0, 2, tl.height);
    }
    let tlDrag = false;
    const tlFrame = e => { const r = tl.getBoundingClientRect(); return Math.round(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * (S.frames - 1)); };
    tl.addEventListener('mousedown', e => { if (!S) return; tlDrag = true; stopIfTracking(); go(tlFrame(e)); });
    document.addEventListener('mousemove', e => { if (tlDrag) go(tlFrame(e)); });
    document.addEventListener('mouseup', () => { tlDrag = false; });

    //------------------------------------------------------------------------------------------------------------------
    // actions
    //------------------------------------------------------------------------------------------------------------------
    function go(i) {
        if (!S) return;
        cur = Math.max(0, Math.min(S.frames - 1, Math.min(i, S.decoded - 1)));
        info(); draw();
    }
    function act(url, body, then) {
        return postJ(url, body).then(d => {
            if (!d.ok) { $('l_why').textContent = d.error; return d; }
            setState(d.state); loadLabels();
            if (then) then(d);
            return d;
        });
    }
    function stopIfTracking() {
        stopPlay();
        if (S && S.tracking) { userStopped = true; S.tracking = false; postJ('/label/stop').then(d => d.state && setState(d.state)); }
    }
    function toggleTrack() {
        if (!S) return;
        stopPlay();
        if (S.tracking) { userStopped = true; act('/label/stop'); return; }
        act('/label/track', {i: cur});
    }
    function check(dir) {                             // previous / next frame marked "check" (a jump, or where the tracker stopped)
        for (let j = cur + dir; j >= 0 && j < L.length; j += dir) { const l = L[j]; if (l && l[6]) { go(j); return; } }
    }
    function undo() {                                 // back one step; the cursor goes to the last label that is left at or before it
        stopPlay();
        act('/label/undo', {}, () => getJ('/label/labels').then(d => {
            L = d.labels || []; ver = d.version;
            let j = Math.min(cur, L.length - 1); while (j > 0 && !L[j]) j--;
            go(Math.max(0, j)); drawTimeline();
        }));
    }
    function stopPlay() { if (playTimer) { clearInterval(playTimer); playTimer = null; info(); } }
    function togglePlay() {                           // P: play the labels for review (no tracking), at the speed buttons' pace
        if (playTimer) { stopPlay(); return; }
        stopIfTracking();
        const fps = S.fps * (S.speed || 4);
        playTimer = setInterval(() => {
            if (cur >= Math.min(S.frames, S.decoded) - 1) { stopPlay(); return; }
            const im = frameImg(cur + 1);
            if (im && im.complete) go(cur + 1);      // never runs ahead of the frames it can show
        }, 1000 / fps);
        $('l_kind').textContent = 'reviewing (P / Space: stop) …';
    }
    function key(dir) {                               // previous / next key frame (manual or absent)
        for (let j = cur + dir; j >= 0 && j < L.length; j += dir) { const l = L[j]; if (l && (l[0] === 'm' || l[0] === 'a')) { go(j); return; } }
    }

    // drawing a box on the frame -> key frame -> tracking starts from it
    const canvasPt = e => { const r = cv.getBoundingClientRect(); return [Math.max(0, Math.min(cv.width, (e.clientX - r.left) * cv.width / r.width)), Math.max(0, Math.min(cv.height, (e.clientY - r.top) * cv.height / r.height))]; };
    cv.addEventListener('mousedown', e => {
        if (!S || e.button !== 0) return;
        e.preventDefault();
        if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
        stopIfTracking();
        const [x, y] = canvasPt(e); drag = {x0: x, y0: y, x1: x, y1: y, shift: e.shiftKey};
    });
    document.addEventListener('mousemove', e => { if (!drag) return; const [x, y] = canvasPt(e); drag.x1 = x; drag.y1 = y; draw(); });
    document.addEventListener('mouseup', e => {
        if (!drag || e.button !== 0) return;
        const d = drag; drag = null;
        const s = k(), box = [Math.min(d.x0, d.x1) / s, Math.min(d.y0, d.y1) / s, Math.max(d.x0, d.x1) / s, Math.max(d.y0, d.y1) / s];
        if (Math.abs(d.x1 - d.x0) < 3 || Math.abs(d.y1 - d.y0) < 3) { draw(); return; }
        const i = cur;
        L[i] = ['m', ...box]; draw();                 // at once on screen; the server confirms
        act('/label/box', {i, box}, () => { if (!d.shift) act('/label/track', {i}); });     // Shift+drag: key frame only
    });
    cv.addEventListener('contextmenu', e => { e.preventDefault(); stopIfTracking(); });

    document.addEventListener('keydown', e => {
        if (!window.dtcBenchTab || window.dtcBenchTab() !== 'label' || !S) return;
        const t = e.target;
        if (t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || (t.tagName === 'INPUT' && !['checkbox', 'radio', 'range', 'button', 'file'].includes(t.type))) {
            if (e.key === 'Escape') t.blur();
            return;
        }
        if (t.tagName === 'BUTTON' && e.key === 'Enter') return;
        const step = e.shiftKey ? 10 : 1;
        let handled = true;
        // e.code = the physical key: the letters work with any keyboard layout (Russian: N is 'т', [ is 'х', ...)
        if ((e.ctrlKey || e.metaKey) && e.code !== 'KeyZ') return;          // leave the browser's own shortcuts alone
        switch (e.code) {
            case 'ArrowRight': stopIfTracking(); go(cur + step); break;
            case 'ArrowLeft': stopIfTracking(); go(cur - step); break;
            case 'Home': stopIfTracking(); go(0); break;
            case 'End': stopIfTracking(); go(S.frames - 1); break;
            case 'BracketLeft': stopIfTracking(); key(-1); break;
            case 'BracketRight': stopIfTracking(); key(+1); break;
            case 'Space': if (playTimer) stopPlay(); else toggleTrack(); break;
            case 'Comma': stopIfTracking(); check(-1); break;
            case 'Period': stopIfTracking(); check(+1); break;
            case 'KeyP': togglePlay(); break;
            case 'KeyZ': if (e.ctrlKey || e.metaKey) undo(); else handled = false; break;
            case 'Escape': stopIfTracking(); break;
            case 'KeyN': {
                const i = cur;
                act('/label/absent', {i, to_next_key: e.shiftKey}, () => { if (!e.shiftKey) go(i + 1); });
                break;
            }
            case 'KeyI': act('/label/interp', {i: cur}); break;
            case 'KeyK': {                            // this box is right: make it a key frame (no tracking)
                const l = L[cur];
                if (l && l.length >= 5 && l[0] !== 'm') act('/label/box', {i: cur, box: l.slice(1, 5)});
                break;
            }
            case 'KeyX': act('/label/clear', {i: cur}); break;
            case 'Delete': case 'Backspace': act('/label/delete', {i: cur}); break;
            default: handled = false;
        }
        if (handled) e.preventDefault();
    });

    //------------------------------------------------------------------------------------------------------------------
    if (window.dtcBenchTab && window.dtcBenchTab() === 'label') window.dtcLabelShown(true);   // the page was opened on ?tab=label
})();
