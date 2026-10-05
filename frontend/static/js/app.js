/* ==========================================================================
   BlockFusion — app.js
   Vanilla JS controller: API integration, Chart.js, canvas background,
   toasts, wallet/tx/mining flows, auto-refresh.
   ========================================================================== */
(() => {
  'use strict';

  /* ------------------------------------------------------------------ *
   * Config
   * ------------------------------------------------------------------ */
  // Bug #7 fix: derive API base from the current page origin instead of
  // hardcoding 127.0.0.1:5000.  The hardcoded value breaks every non-localhost
  // deployment because every fetch() fails (8 s timeout) before the demo
  // fallback kicks in, making the whole UI feel frozen on load.
  const API_BASE = window.location.origin;
  const ENDPOINTS = {
    root:        '/',
    mine:        '/mine',
    chain:       '/chain',
    walletCreate:'/wallet/create',
    walletTx:   '/wallet/transactions',
    sign:        '/api/sign',
    stats:       '/api/stats',
    balance:     '/api/balance',
    health:      '/api/health',
    mempool:     '/api/mempool',
    search:      '/api/search',
  };
  const REFRESH_INTERVAL_MS = 10000;

  /* ------------------------------------------------------------------ *
   * State
   * ------------------------------------------------------------------ */
  const state = {
    chain: [],
    length: 0,
    liveMode: true,      // true once we confirm real backend; false => demo/mock fallback
    wallet: null,
    countdown: REFRESH_INTERVAL_MS / 1000,
    charts: {},
  };

  /* ------------------------------------------------------------------ *
   * Utilities
   * ------------------------------------------------------------------ */
  const $ = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

  function fmtTime(ts) {
    if (!ts) return '—';
    const ms = ts < 10_000_000_000 ? ts * 1000 : ts;
    const d = new Date(ms);
    if (isNaN(d.getTime())) return String(ts);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function truncateHash(hash, len = 16) {
    if (!hash) return '—';
    hash = String(hash);
    if (hash.length <= len) return hash;
    return `${hash.slice(0, len / 2)}…${hash.slice(-len / 2)}`;
  }

  function truncateKey(key, len = 24) {
    if (!key) return '—';
    key = String(key);
    return key.length <= len ? key : `${key.slice(0, len)}…`;
  }

  async function safeFetch(path, options = {}) {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 8000);
    try {
      const res = await fetch(API_BASE + path, { ...options, signal: ctrl.signal, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
      clearTimeout(timeout);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      clearTimeout(timeout);
      throw err;
    }
  }

  /* ------------------------------------------------------------------ *
   * Toast notifications
   * ------------------------------------------------------------------ */
  function showToast(title, message, type = 'info') {
    const container = $('#toastContainer');
    const iconMap = { success: 'bi-check-circle-fill', error: 'bi-x-circle-fill', info: 'bi-info-circle-fill' };
    const el = document.createElement('div');
    el.className = `toast bf-toast-${type}`;
    el.setAttribute('role', 'alert');
    el.innerHTML = `
      <div class="toast-header">
        <i class="bi ${iconMap[type] || iconMap.info} me-2"></i>
        <strong class="me-auto">${title}</strong>
        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="toast"></button>
      </div>
      <div class="toast-body">${message}</div>`;
    container.appendChild(el);
    const toast = new bootstrap.Toast(el, { delay: 4500 });
    toast.show();
    el.addEventListener('hidden.bs.toast', () => el.remove());
  }

  /* ------------------------------------------------------------------ *
   * Demo / mock data generator (used when Flask backend is unreachable)
   * ------------------------------------------------------------------ */
  function sha256ish(input) {
    // Lightweight deterministic pseudo-hash for demo visuals only (NOT cryptographic).
    let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
    for (let i = 0; i < input.length; i++) {
      const ch = input.charCodeAt(i);
      h1 = Math.imul(h1 ^ ch, 2654435761);
      h2 = Math.imul(h2 ^ ch, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    const hex = (n) => (n >>> 0).toString(16).padStart(8, '0');
    return (hex(h1) + hex(h2) + hex(h1 ^ h2) + hex(h2 ^ 0x9e3779b9)).slice(0, 64);
  }

  function genDemoChain(count = 6) {
    const chain = [];
    let prevHash = '0'.repeat(64);
    let ts = Math.floor(Date.now() / 1000) - count * 240;
    for (let i = 0; i < count; i++) {
      const txCount = i === 0 ? 0 : 1 + Math.floor(Math.random() * 4);
      const transactions = Array.from({ length: txCount }, (_, j) => ({
        sender: i === 0 ? 'genesis' : sha256ish(`sender-${i}-${j}`).slice(0, 20),
        recipient: sha256ish(`recipient-${i}-${j}`).slice(0, 20),
        amount: +(Math.random() * 12 + 0.1).toFixed(3),
      }));
      const proof = 10000 + Math.floor(Math.random() * 89999);
      const block = {
        index: i + 1,
        timestamp: ts,
        transactions,
        proof,
        previous_hash: prevHash,
      };
      block.hash = sha256ish(JSON.stringify(block));
      prevHash = block.hash;
      ts += 180 + Math.floor(Math.random() * 300);
      chain.push(block);
    }
    return chain;
  }

  function genDemoWallet() {
    const rand = () => Math.random().toString(36).slice(2);
    const body = () => Array.from({ length: 6 }, rand).join('').toUpperCase();
    const publicKey = `-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A${body()}\n${body()}\n-----END PUBLIC KEY-----`;
    const privateKey = `-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA${body()}${body()}\n${body()}\n-----END RSA PRIVATE KEY-----`;
    return { public_key: publicKey, private_key: privateKey };
  }

  /* ------------------------------------------------------------------ *
   * API status panel
   * ------------------------------------------------------------------ */
  function setStatus(id, ok, labelOk = 'Online', labelDown = 'Demo Mode') {
    const el = $(id);
    if (!el) return;
    el.textContent = ok ? labelOk : labelDown;
    el.classList.toggle('bf-badge-live', ok);
    el.classList.toggle('bf-badge-down', !ok);
  }

  function updateStatusPanel(ok) {
    setStatus('#status-api', ok);
    setStatus('#status-db', ok);
    setStatus('#status-chain', ok, 'Running', 'Simulated');
    $('#mini-api').textContent = ok ? 'Live' : 'Demo';
    $('#mini-db').textContent = ok ? 'Live' : 'Demo';
    $('#mini-chain').textContent = ok ? 'Live' : 'Demo';
    const pill = $('#navNetworkPill');
    if (pill) pill.innerHTML = ok
      ? '<span class="bf-dot bf-dot-live"></span> Mainnet Sim'
      : '<span class="bf-dot" style="background:var(--bf-amber)"></span> Demo Mode';
  }

  /* ------------------------------------------------------------------ *
   * Data loading
   * ------------------------------------------------------------------ */
  async function loadChain({ silent = false } = {}) {
    try {
      const data = await safeFetch(ENDPOINTS.chain);
      const chain = Array.isArray(data) ? data : (data.chain || []);
      if (!chain.length) throw new Error('Empty chain payload');
      state.chain = chain;
      state.length = data.length || chain.length;
      state.liveMode = true;
      updateStatusPanel(true);
    } catch (err) {
      // Fallback to demo data so the dashboard always looks alive.
      if (!state.chain.length) state.chain = genDemoChain(7);
      state.length = state.chain.length;
      state.liveMode = false;
      updateStatusPanel(false);
      if (!silent) console.info('[BlockFusion] Using demo data — backend not reachable:', err.message);
    }
    renderAll();
  }

  /* ------------------------------------------------------------------ *
   * Rendering — Stats
   * ------------------------------------------------------------------ */
  // Helper: set a stat card value, clearing any skeleton child elements first
  function setStatValue(id, html) {
    const el = $(id);
    if (!el) return;
    // Remove skeleton spans so they don't overlap the real value
    el.querySelectorAll('.bf-skeleton-line').forEach(s => s.remove());
    el.innerHTML = html;
  }

  function renderStats() {
    const chain = state.chain;
    const totalBlocks = chain.length;
    const totalTx = chain.reduce((sum, b) => sum + ((b.transactions && b.transactions.length) || 0), 0);
    const wallets = new Set();
    chain.forEach(b => (b.transactions || []).forEach(t => {
      if (t.sender && t.sender !== 'genesis' && t.sender !== '0') wallets.add(t.sender);
      if (t.recipient) wallets.add(t.recipient);
    }));

    setStatValue('#stat-blocks', totalBlocks.toLocaleString());
    setStatValue('#stat-tx', totalTx.toLocaleString());
    setStatValue('#stat-wallets', wallets.size.toLocaleString());
    setStatValue('#stat-network', state.liveMode
      ? '<span style="color:var(--bf-green)">Healthy</span>'
      : '<span style="color:var(--bf-amber)">Demo</span>');

    $('#stat-blocks-trend').textContent = totalBlocks ? `+${totalBlocks} blocks total` : 'No blocks yet';
    $('#stat-tx-trend').textContent = totalTx ? `${totalTx} confirmed` : 'No activity yet';
    $('#stat-wallets-trend').textContent = wallets.size ? 'Across all blocks' : '';
    $('#stat-network-trend').textContent = state.liveMode ? 'All systems nominal' : 'Simulated feed';

    // Pull accurate stats from the backend (overrides chain-derived counts)
    if (state.liveMode) {
      safeFetch(ENDPOINTS.stats).then(data => {
        if (data && typeof data.unique_wallets === 'number') {
          setStatValue('#stat-wallets', data.unique_wallets.toLocaleString());
          setStatValue('#stat-blocks', data.total_blocks.toLocaleString());
          setStatValue('#stat-tx', data.total_transactions.toLocaleString());
          setStatValue('#stat-network', data.chain_valid
            ? '<span style="color:var(--bf-green)">Healthy</span>'
            : '<span style="color:var(--bf-amber)">Invalid!</span>');
          $('#stat-blocks-trend').textContent = `+${data.total_blocks} blocks total`;
          $('#stat-tx-trend').textContent = `${data.total_transactions} confirmed`;

          // Quick-win: block time + pending tx stats
          if (typeof data.avg_block_time === 'number') {
            const bt = data.avg_block_time;
            setStatValue('#stat-blocktime', bt > 0 ? `${bt}s` : '—');
            $('#stat-blocktime-trend').textContent = bt > 0 ? 'Per block avg' : 'Need 2+ blocks';
          }
          if (typeof data.pending_tx === 'number') {
            setStatValue('#stat-pending', data.pending_tx.toLocaleString());
            $('#stat-pending-trend').textContent = data.pending_tx ? 'Awaiting mining' : 'None pending';
          }
        }
      }).catch(() => { /* silent — chain data already displayed */ });
    } else {
      // Demo fallback values
      setStatValue('#stat-blocktime', '—');
      setStatValue('#stat-pending', '0');
    }
  }

  /* ------------------------------------------------------------------ *
   * Rendering — Explorer table
   * ------------------------------------------------------------------ */
  function renderExplorer(filterIndex = null) {
    const tbody = $('#blockTableBody');
    const emptyState = $('#explorerEmpty');
    tbody.innerHTML = '';

    let blocks = [...state.chain].sort((a, b) => b.index - a.index);
    if (filterIndex !== null && filterIndex !== '') {
      blocks = blocks.filter(b => String(b.index) === String(filterIndex));
    }

    if (!blocks.length) {
      emptyState.classList.remove('d-none');
      return;
    }
    emptyState.classList.add('d-none');

    blocks.forEach(block => {
      const txCount = (block.transactions && block.transactions.length) || 0;
      const row = document.createElement('tr');
      row.dataset.index = block.index;
      row.innerHTML = `
        <td><i class="bi bi-chevron-right bf-expand-icon"></i></td>
        <td class="bf-mono fw-semibold">#${block.index}</td>
        <td>${fmtTime(block.timestamp)}</td>
        <td class="bf-mono">${block.proof ?? '—'}</td>
        <td class="bf-hash" title="${block.previous_hash || ''}">${truncateHash(block.previous_hash)}</td>
        <td class="text-end"><span class="badge rounded-pill" style="background:rgba(61,127,255,0.12); color:var(--bf-primary); font-weight:600;">${txCount}</span></td>`;

      row.addEventListener('click', () => toggleTxDetail(row, block));
      tbody.appendChild(row);
    });
  }

  function toggleTxDetail(row, block) {
    const existing = row.nextElementSibling;
    if (existing && existing.classList.contains('bf-tx-detail-row')) {
      existing.remove();
      row.classList.remove('expanded');
      return;
    }
    // collapse any other open rows
    $$('.bf-tx-detail-row').forEach(r => r.remove());
    $$('#blockTableBody tr.expanded').forEach(r => r.classList.remove('expanded'));

    row.classList.add('expanded');
    const detailRow = document.createElement('tr');
    detailRow.className = 'bf-tx-detail-row';
    const txs = block.transactions || [];
    const txHtml = txs.length
      ? txs.map(t => `
          <div class="bf-tx-mini">
            <span>${truncateKey(t.sender, 18)} <i class="bi bi-arrow-right mx-1"></i> ${truncateKey(t.recipient, 18)}</span>
            <span class="amt">${t.amount ?? '0'} BFC</span>
          </div>`).join('')
      : '<p class="text-muted mb-0" style="font-size:0.85rem;">No transactions in this block.</p>';

    detailRow.innerHTML = `<td colspan="6"><div class="bf-tx-detail-inner">${txHtml}</div></td>`;
    row.after(detailRow);
  }

  function initExplorerSearch() {
    const input = $('#blockSearchInput');
    const btn = $('#blockSearchBtn');
    const clearBtn = $('#blockSearchClear');
    btn.addEventListener('click', () => renderExplorer(input.value.trim()));
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); renderExplorer(input.value.trim()); } });
    clearBtn.addEventListener('click', () => { input.value = ''; renderExplorer(); });
  }

  /* ------------------------------------------------------------------ *
   * Rendering — Recent transactions feed
   * ------------------------------------------------------------------ */
  function renderRecentTx() {
    const feed = $('#recentTxFeed');
    const allTx = [];
    const chainLen = state.chain.length;

    // Confirmed transactions from mined blocks
    [...state.chain].sort((a, b) => b.index - a.index).forEach(block => {
      (block.transactions || []).forEach(t => allTx.push({ ...t, blockIndex: block.index, ts: block.timestamp }));
    });

    // Bug #11 fix: pending demo transactions live in state.pendingTx (a separate
    // virtual pool), not inside mined block objects.  Prepend them so they appear
    // at the top of the feed, visually distinct as "(pending)".
    const pending = (state.pendingTx || []).slice().reverse();
    pending.forEach(t => allTx.unshift(t));

    const latest = allTx.slice(0, 8);

    if (!latest.length) {
      feed.innerHTML = `<div class="bf-card bf-glass bf-empty-state"><i class="bi bi-inboxes"></i><p>No transactions yet. Submit one to see it here.</p></div>`;
      return;
    }

    feed.innerHTML = latest.map(t => {
      // Quick-win: confirmation count
      const isPending = t.blockIndex === '(pending)';
      const confs = isPending ? 0 : Math.max(0, chainLen - t.blockIndex);
      const confBadge = isPending
        ? `<span class="bf-confirm-badge bf-confirm-low"><i class="bi bi-hourglass-split"></i> Pending</span>`
        : confs <= 2
          ? `<span class="bf-confirm-badge bf-confirm-low"><i class="bi bi-shield-check"></i> ${confs} conf${confs !== 1 ? 's' : ''}</span>`
          : `<span class="bf-confirm-badge"><i class="bi bi-shield-fill-check"></i> ${confs} confs</span>`;

      return `
      <div class="bf-tx-item fade-up">
        <div class="bf-tx-avatar"><i class="bi bi-arrow-left-right"></i></div>
        <div class="bf-tx-main">
          <div class="bf-tx-parties">${truncateKey(t.sender, 14)} <i class="bi bi-arrow-right mx-1"></i> ${truncateKey(t.recipient, 14)}</div>
          <div class="bf-tx-meta">Block #${t.blockIndex} · ${fmtTime(t.ts)} ${confBadge}</div>
        </div>
        <div class="bf-tx-amount">${t.amount ?? '0'} BFC</div>
      </div>`;
    }).join('');
  }

  /* ------------------------------------------------------------------ *
   * Charts
   * ------------------------------------------------------------------ */
  function chartTheme() {
    return {
      grid: 'rgba(148,163,184,0.08)',
      text: '#8993AC',
      blue: '#3D7FFF',
      green: '#16C784',
    };
  }

  function initCharts() {
    // Safety: Chart.js CDN may fail to load (offline/blocked)
    if (typeof Chart === 'undefined') {
      console.warn('[BlockFusion] Chart.js not available — charts disabled.');
      ['#blockGrowthChart', '#txVolumeChart', '#blockTimeChart'].forEach(sel => {
        const canvas = $(sel);
        if (canvas && canvas.parentElement) {
          canvas.style.display = 'none';
          const msg = document.createElement('div');
          msg.className = 'bf-chart-empty';
          msg.innerHTML = '<i class="bi bi-wifi-off"></i><p>Charts require an internet connection to load Chart.js.</p>';
          canvas.parentElement.appendChild(msg);
        }
      });
      return;
    }
    try {
      const t = chartTheme();
      Chart.defaults.font.family = "'IBM Plex Sans', sans-serif";
      Chart.defaults.color = t.text;

      const growthCtx = $('#blockGrowthChart').getContext('2d');
      const growthGradient = growthCtx.createLinearGradient(0, 0, 0, 260);
      growthGradient.addColorStop(0, 'rgba(61,127,255,0.35)');
      growthGradient.addColorStop(1, 'rgba(61,127,255,0)');

      state.charts.growth = new Chart(growthCtx, {
        type: 'line',
        data: { labels: [], datasets: [{
          label: 'Blocks',
          data: [],
          borderColor: t.blue,
          backgroundColor: growthGradient,
          fill: true,
          tension: 0.4,
          pointRadius: 4,
          pointHoverRadius: 6,
          pointBackgroundColor: t.blue,
          pointBorderColor: '#0C0F17',
          borderWidth: 2,
        }] },
        options: chartOptions(t, false),
      });

      const volCtx = $('#txVolumeChart').getContext('2d');
      const volGradient = volCtx.createLinearGradient(0, 0, 0, 260);
      volGradient.addColorStop(0, 'rgba(22,199,132,0.85)');
      volGradient.addColorStop(1, 'rgba(22,199,132,0.15)');

      state.charts.volume = new Chart(volCtx, {
        type: 'bar',
        data: { labels: [], datasets: [{
          label: 'Transactions',
          data: [],
          backgroundColor: volGradient,
          borderRadius: 6,
          maxBarThickness: 40,
        }] },
        options: chartOptions(t, true),
      });

      // Quick-win: Block Time Chart
      const timeCtx = $('#blockTimeChart').getContext('2d');
      const timeGradient = timeCtx.createLinearGradient(0, 0, 0, 260);
      timeGradient.addColorStop(0, 'rgba(139,92,246,0.5)');
      timeGradient.addColorStop(1, 'rgba(139,92,246,0.05)');

      state.charts.blockTime = new Chart(timeCtx, {
        type: 'line',
        data: { labels: [], datasets: [{
          label: 'Block Time (s)',
          data: [],
          borderColor: '#8B5CF6',
          backgroundColor: timeGradient,
          fill: true,
          tension: 0.4,
          pointRadius: 4,
          pointHoverRadius: 6,
          pointBackgroundColor: '#8B5CF6',
          pointBorderColor: '#0C0F17',
          borderWidth: 2,
        }] },
        options: chartOptions(t, false),
      });

    } catch (err) {
      console.error('[BlockFusion] Chart initialisation failed:', err);
    }
  }

  function chartOptions(t, isBar) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#131826',
          borderColor: 'rgba(148,163,184,0.2)',
          borderWidth: 1,
          titleColor: '#E8ECF4',
          bodyColor: '#E8ECF4',
          padding: 10,
          cornerRadius: 8,
        },
      },
      scales: {
        x: { grid: { color: t.grid, display: !isBar }, ticks: { color: t.text, font: { size: 11 } }, border: { color: t.grid } },
        y: { grid: { color: t.grid }, ticks: { color: t.text, font: { size: 11 }, precision: 0 }, border: { display: false }, beginAtZero: true },
      },
      interaction: { intersect: false, mode: 'index' },
      animation: { duration: 700, easing: 'easeOutQuart' },
    };
  }

  function updateCharts() {
    const sorted = [...state.chain].sort((a, b) => a.index - b.index);
    const hasRealData = sorted.length >= 2;

    // Toggle empty-state overlays
    const growthEmpty = $('#growthChartEmpty');
    const volEmpty    = $('#volChartEmpty');
    const timeEmpty   = $('#timeChartEmpty');
    if (growthEmpty) growthEmpty.classList.toggle('d-none', hasRealData);
    if (volEmpty)    volEmpty.classList.toggle('d-none', hasRealData);
    if (timeEmpty)   timeEmpty.classList.toggle('d-none', hasRealData);

    // Build chart data — pad to at least 5 visible points so bars/lines render
    let labels, cumulative, txCounts, blockTimes;
    if (sorted.length === 0) {
      labels     = ['#1','#2','#3','#4','#5'];
      cumulative = [1,2,3,4,5];
      txCounts   = [0,0,0,0,0];
      blockTimes = [0,0,0,0,0];
    } else if (sorted.length === 1) {
      const base = sorted[0];
      labels     = [`#${base.index}`, '#2', '#3', '#4', '#5'];
      cumulative = [1, 2, 3, 4, 5];
      txCounts   = [(base.transactions && base.transactions.length) || 0, 0, 0, 0, 0];
      blockTimes = [0, 0, 0, 0, 0];
    } else {
      labels     = sorted.map(b => `#${b.index}`);
      cumulative = sorted.map((_, i) => i + 1);
      txCounts   = sorted.map(b => (b.transactions && b.transactions.length) || 0);
      // Quick-win: block time between consecutive blocks (first block = 0)
      blockTimes = sorted.map((b, i) =>
        i === 0 ? 0 : Math.round(b.timestamp - sorted[i - 1].timestamp)
      );
    }

    if (state.charts.growth) {
      state.charts.growth.data.labels = labels;
      state.charts.growth.data.datasets[0].data = cumulative;
      state.charts.growth.update();
    }
    if (state.charts.volume) {
      state.charts.volume.data.labels = labels;
      state.charts.volume.data.datasets[0].data = txCounts;
      state.charts.volume.update();
    }
    // Quick-win: update block time chart
    if (state.charts.blockTime) {
      state.charts.blockTime.data.labels = labels;
      state.charts.blockTime.data.datasets[0].data = blockTimes;
      state.charts.blockTime.update();
    }
  }

  /* ------------------------------------------------------------------ *
   * Render orchestrator
   * ------------------------------------------------------------------ */
  function renderAll() {
    renderStats();
    // Bug #12 fix: pass explicit null so renderExplorer() skips filtering on
    // initial render.  The previous code passed an empty string '' which worked
    // by coincidence (the '' guard existed) but was fragile and misleading.
    const searchInput = $('#blockSearchInput');
    const currentFilter = (searchInput && searchInput.value.trim()) ? searchInput.value.trim() : null;
    renderExplorer(currentFilter);
    renderRecentTx();
    updateCharts();
  }

  /* ------------------------------------------------------------------ *
   * Wallet creation
   * ------------------------------------------------------------------ */
  function initWallet() {
    const btn = $('#createWalletBtn');
    const pubOut = $('#publicKeyOut');
    const privOut = $('#privateKeyOut');
    const downloadBtn = $('#downloadWalletBtn');

    btn.addEventListener('click', async () => {
      setBtnLoading(btn, true);
      try {
        let wallet;
        try {
          wallet = await safeFetch(ENDPOINTS.walletCreate);
          if (!wallet.public_key || !wallet.private_key) throw new Error('Malformed wallet payload');
        } catch (err) {
          wallet = genDemoWallet();
          if (state.liveMode) showToast('Wallet Service', 'Backend unreachable — generated a demo wallet instead.', 'info');
        }
        state.wallet = wallet;
        pubOut.textContent  = wallet.public_key;
        privOut.textContent = wallet.private_key;
        downloadBtn.disabled = false;
        // Quick-win: enable QR button now that wallet exists
        const qrBtn = $('#showQrBtn');
        if (qrBtn) qrBtn.disabled = false;
        showToast('Wallet Created', 'A new keypair has been generated successfully.', 'success');
      } catch (err) {
        showToast('Wallet Error', 'Could not generate a wallet. Please try again.', 'error');
      } finally {
        setBtnLoading(btn, false);
      }
    });

    downloadBtn.addEventListener('click', () => {
      if (!state.wallet) return;
      const blob = new Blob([JSON.stringify(state.wallet, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `blockfusion-wallet-${Date.now()}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      showToast('Download Started', 'Wallet JSON saved to your device.', 'success');
    });

    $$('[data-copy-target]').forEach(btn => {
      btn.addEventListener('click', () => {
        const targetEl = document.getElementById(btn.dataset.copyTarget);
        const text = targetEl ? targetEl.textContent.trim() : '';
        if (!text || text.includes('No wallet generated')) {
          showToast('Nothing to Copy', 'Generate a wallet first.', 'info');
          return;
        }
        // Try modern Clipboard API first, fall back to execCommand for non-secure contexts
        const doCopy = () => {
          if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(text);
          }
          // Fallback: create a temporary textarea and use execCommand
          return new Promise((resolve, reject) => {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            try {
              document.execCommand('copy') ? resolve() : reject(new Error('execCommand failed'));
            } catch (e) {
              reject(e);
            } finally {
              ta.remove();
            }
          });
        };
        doCopy()
          .then(() => showToast('Copied!', 'Key copied to clipboard.', 'success'))
          .catch(() => showToast('Copy Failed', 'Could not access clipboard. Select the key manually.', 'error'));
      });
    });

    // Balance lookup
    const balanceCheckBtn = $('#balanceCheckBtn');
    const balanceMyWalletBtn = $('#balanceMyWalletBtn');
    const balanceInput = $('#balanceInput');
    const balanceResult = $('#balanceResult');

    async function lookupBalance(address) {
      if (!address) { showToast('No Address', 'Enter a public key to check.', 'info'); return; }
      balanceResult.classList.remove('d-none');
      balanceResult.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Looking up…';
      try {
        // Use POST with JSON body to avoid URL-length issues with long RSA hex keys
        const data = await safeFetch(ENDPOINTS.balance, {
          method: 'POST',
          body: JSON.stringify({ address })
        });
        balanceResult.innerHTML = `
          <i class="bi bi-wallet2 me-1"></i>
          Balance: <strong>${data.balance.toFixed(8)} BFC</strong>
          &nbsp;&middot;&nbsp; Received: ${data.received.toFixed(8)}
          &nbsp;&middot;&nbsp; Sent: ${data.sent.toFixed(8)}`;
      } catch (err) {
        balanceResult.innerHTML = '<i class="bi bi-exclamation-triangle me-1"></i> Could not fetch balance — backend may be offline.';
      }
    }

    if (balanceCheckBtn) balanceCheckBtn.addEventListener('click', () => lookupBalance(balanceInput.value.trim()));
    if (balanceMyWalletBtn) balanceMyWalletBtn.addEventListener('click', () => {
      if (!state.wallet) { showToast('No Wallet', 'Create a wallet first.', 'info'); return; }
      balanceInput.value = state.wallet.public_key;
      lookupBalance(state.wallet.public_key);
    });
    if (balanceInput) balanceInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') lookupBalance(balanceInput.value.trim());
    });
  }

  function setBtnLoading(btn, loading) {
    const label = btn.querySelector('.btn-label');
    const spinner = btn.querySelector('.btn-spinner');
    if (!label || !spinner) return;
    label.classList.toggle('d-none', loading);
    spinner.classList.toggle('d-none', !loading);
    btn.disabled = loading;
  }

  /* ------------------------------------------------------------------ *
   * Transaction form
   * ------------------------------------------------------------------ */
  function initTxForm() {
    const form = $('#txForm');
    const submitBtn = $('#txSubmitBtn');
    const clearBtn = $('#txClearBtn');
    const autoFillBtn = $('#txAutoFillBtn');

    clearBtn.addEventListener('click', () => {
      form.reset();
      $$('.bf-input', form).forEach(el => el.classList.remove('is-invalid'));
    });

    // "Use My Wallet" quick-fill for the Sender field
    const useSenderBtn = $('#txUseSenderBtn');
    if (useSenderBtn) {
      useSenderBtn.addEventListener('click', () => {
        if (!state.wallet) {
          showToast('No Wallet', 'Generate a wallet in the Wallet section first.', 'info');
          return;
        }
        $('#txSender').value = state.wallet.public_key;
        $('#txSender').classList.remove('is-invalid');
        showToast('Sender Filled', 'Your wallet public key has been set as the sender.', 'success');
      });
    }

    // Auto-fill sender from wallet and generate signature via /api/sign
    if (autoFillBtn) {
      autoFillBtn.addEventListener('click', async () => {
        if (!state.wallet) {
          showToast('No Wallet', 'Generate a wallet first, then use Auto-fill.', 'info');
          return;
        }
        const senderEl = $('#txSender');
        const recipientEl = $('#txRecipient');
        const amountEl = $('#txAmount');
        const sigEl = $('#txSignature');

        senderEl.value = state.wallet.public_key;

        const recipient = recipientEl.value.trim();
        const amount = parseFloat(amountEl.value.trim());

        if (!recipient || !amount) {
          showToast('Fill Recipient & Amount', 'Enter the recipient public key and amount first.', 'info');
          return;
        }

        // Bug #8 mitigation: warn the user if the private key would be sent
        // over a non-HTTPS connection where it could be intercepted.
        if (window.location.protocol !== 'https:' && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
          showToast(
            '⚠️ Security Warning',
            'You are not on HTTPS. Your private key will be sent unencrypted. Use HTTPS in production.',
            'error'
          );
        }

        autoFillBtn.disabled = true;
        autoFillBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Signing…';
        try {
          const payload = {
            private_key: state.wallet.private_key,
            sender: state.wallet.public_key,
            recipient,
            amount
          };
          const result = await safeFetch(ENDPOINTS.sign, { method: 'POST', body: JSON.stringify(payload) });
          sigEl.value = result.signature;
          showToast('Signed', 'Transaction signed with your private key.', 'success');
        } catch (err) {
          showToast('Sign Failed', 'Could not sign — backend may be offline.', 'error');
        } finally {
          autoFillBtn.disabled = false;
          autoFillBtn.innerHTML = '<i class="bi bi-lightning-charge-fill"></i> Auto-fill &amp; Sign';
        }
      });
    }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const sender = $('#txSender').value.trim();
      const recipient = $('#txRecipient').value.trim();
      const amount = $('#txAmount').value.trim();
      const signature = $('#txSignature').value.trim();

      let valid = true;
      [['#txSender', sender], ['#txRecipient', recipient], ['#txAmount', amount], ['#txSignature', signature]].forEach(([sel, val]) => {
        const el = $(sel);
        const bad = !val;
        el.classList.toggle('is-invalid', bad);
        if (bad) valid = false;
      });
      if (!valid) {
        showToast('Missing Fields', 'Please fill in every field before submitting.', 'error');
        return;
      }

      setBtnLoading(submitBtn, true);
      try {
        const payload = { sender, recipient, amount: parseFloat(amount), signature };
        try {
          // Use the signed wallet endpoint (primary)
          await safeFetch(ENDPOINTS.walletTx, { method: 'POST', body: JSON.stringify(payload) });
        } catch (err) {
          // Bug #11 fix: the old fallback mutated the LAST MINED block's
          // transaction list, which is incorrect — mined blocks are immutable.
          // Pending transactions belong in a separate pool, not in a confirmed block.
          // We push to state.pendingTx (a virtual pool) and surface it in the feed.
          state.pendingTx = state.pendingTx || [];
          state.pendingTx.push({ sender, recipient, amount: parseFloat(amount), blockIndex: '(pending)', ts: Date.now() / 1000 });
          if (state.liveMode) showToast('Offline Mode', 'Backend unreachable — transaction queued locally for demo purposes.', 'info');
        }
        showToast('Transaction Submitted', 'Your transaction has been queued for the next block.', 'success');
        form.reset();
        renderAll();
      } catch (err) {
        showToast('Transaction Failed', 'Something went wrong while submitting. Please try again.', 'error');
      } finally {
        setBtnLoading(submitBtn, false);
      }
    });
  }

  /* ------------------------------------------------------------------ *
   * Mining
   * ------------------------------------------------------------------ */
  function initMining() {
    const btn = $('#mineBtn');
    const panel = $('#minedBlockPanel');

    btn.addEventListener('click', async () => {
      btn.classList.add('mining');
      setBtnLoading(btn, true);
      try {
        let block;
        try {
          const data = await safeFetch(ENDPOINTS.mine);
          block = data.block || data;
          if (!block || !block.index) throw new Error('Malformed mine response');
          await loadChain({ silent: true });
        } catch (err) {
          // Demo fallback: synthesize a plausible new block.
          const last = state.chain[state.chain.length - 1] || { index: 0, hash: '0'.repeat(64) };
          const newBlock = {
            index: (last.index || 0) + 1,
            timestamp: Math.floor(Date.now() / 1000),
            transactions: [],
            proof: 10000 + Math.floor(Math.random() * 89999),
            previous_hash: last.hash || sha256ish(JSON.stringify(last)),
          };
          newBlock.hash = sha256ish(JSON.stringify(newBlock));
          state.chain.push(newBlock);
          block = newBlock;
          if (state.liveMode) showToast('Offline Mode', 'Backend unreachable — simulated a new block locally.', 'info');
        }

        $('#minedIndex').textContent = `#${block.index}`;
        $('#minedProof').textContent = block.proof ?? '—';
        $('#minedHash').textContent = block.hash || truncateHash(block.previous_hash);
        panel.classList.remove('d-none');

        showToast('Block Mined ⛏', `Block #${block.index} was sealed successfully.`, 'success');
        renderAll();
        loadMempool(); // refresh mempool after mining (pending txs just got confirmed)
      } catch (err) {
        showToast('Mining Failed', 'Unable to mine a new block right now.', 'error');
      } finally {
        btn.classList.remove('mining');
        setBtnLoading(btn, false);
      }
    });
  }

  /* ------------------------------------------------------------------ *
   * Sidebar / navbar interactions
   * ------------------------------------------------------------------ */
  function initSidebar() {
    const sidebar = $('#sidebar');
    const backdrop = $('#sidebarBackdrop');
    const toggle = $('#sidebarToggle');

    function openSidebar() { sidebar.classList.add('open'); backdrop.classList.add('show'); }
    function closeSidebar() { sidebar.classList.remove('open'); backdrop.classList.remove('show'); }

    toggle.addEventListener('click', () => {
      sidebar.classList.contains('open') ? closeSidebar() : openSidebar();
    });
    backdrop.addEventListener('click', closeSidebar);

    $$('.bf-side-link').forEach(link => {
      link.addEventListener('click', () => {
        $$('.bf-side-link').forEach(l => l.classList.remove('active'));
        link.classList.add('active');
        closeSidebar();
      });
    });

    // Sync active nav state on scroll
    const sections = $$('.bf-section, .bf-hero').filter(s => s.id);
    const navLinks = $$('.bf-nav-links .nav-link');
    const sideLinks = $$('.bf-side-link');
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        const id = entry.target.id;
        navLinks.forEach(l => l.classList.toggle('active', l.getAttribute('href') === `#${id}`));
        sideLinks.forEach(l => l.classList.toggle('active', l.dataset.target === id));
      });
    }, { rootMargin: '-40% 0px -55% 0px', threshold: 0 });
    sections.forEach(s => observer.observe(s));
  }

  function initRefreshButton() {
    const btn = $('#refreshBtn');
    const icon = $('#refreshIcon');
    btn.addEventListener('click', async () => {
      icon.classList.add('spinning');
      await loadChain();
      resetCountdown();
      icon.classList.remove('spinning');
      showToast('Refreshed', 'Blockchain data is up to date.', 'info');
    });
  }

  /* ------------------------------------------------------------------ *
   * Auto-refresh countdown
   * ------------------------------------------------------------------ */
  let countdownTimer = null;
  function resetCountdown() {
    state.countdown = REFRESH_INTERVAL_MS / 1000;
    const el = $('#refreshCountdown');
    if (el) el.textContent = state.countdown;
  }

  function startAutoRefresh() {
    resetCountdown();
    countdownTimer = setInterval(() => {
      state.countdown -= 1;
      const el = $('#refreshCountdown');
      if (el) el.textContent = Math.max(state.countdown, 0);
      if (state.countdown <= 0) {
        loadChain({ silent: true });
        resetCountdown();
      }
    }, 1000);
  }

  /* ------------------------------------------------------------------ *
   * Canvas network background (particle nodes + connecting lines)
   * ------------------------------------------------------------------ */
  function initNetworkCanvas() {
    const canvas = $('#networkCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    let particles = [];
    let width, height;
    let animId;
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function resize() {
      width = canvas.width = canvas.offsetWidth * devicePixelRatio;
      height = canvas.height = canvas.offsetHeight * devicePixelRatio;
    }

    function initParticles() {
      const count = Math.min(70, Math.floor((canvas.offsetWidth * canvas.offsetHeight) / 18000));
      particles = Array.from({ length: count }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.35 * devicePixelRatio,
        vy: (Math.random() - 0.5) * 0.35 * devicePixelRatio,
        r: (Math.random() * 1.6 + 0.8) * devicePixelRatio,
      }));
    }

    function step() {
      ctx.clearRect(0, 0, width, height);
      const maxDist = 150 * devicePixelRatio;

      particles.forEach(p => {
        p.x += p.vx; p.y += p.vy;
        if (p.x < 0 || p.x > width) p.vx *= -1;
        if (p.y < 0 || p.y > height) p.vy *= -1;
      });

      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const a = particles[i], b = particles[j];
          const dx = a.x - b.x, dy = a.y - b.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < maxDist) {
            ctx.strokeStyle = `rgba(61,127,255,${0.14 * (1 - dist / maxDist)})`;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
      }

      particles.forEach(p => {
        ctx.beginPath();
        ctx.fillStyle = 'rgba(61,127,255,0.55)';
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
      });

      animId = requestAnimationFrame(step);
    }

    function start() {
      resize();
      initParticles();
      if (!prefersReducedMotion) {
        step();
      } else {
        ctx.clearRect(0, 0, width, height);
        particles.forEach(p => {
          ctx.beginPath();
          ctx.fillStyle = 'rgba(61,127,255,0.4)';
          ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
          ctx.fill();
        });
      }
    }

    let resizeTimer;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        cancelAnimationFrame(animId);
        start();
      }, 200);
    });

    start();
  }

  /* ------------------------------------------------------------------ *
   * Quick-win: QR Code for public key
   * ------------------------------------------------------------------ */
  function initQRCode() {
    const showBtn  = $('#showQrBtn');
    const overlay  = $('#qrOverlay');
    const closeBtn = $('#qrCloseBtn');
    const canvas   = $('#qrCodeCanvas');
    if (!showBtn || !overlay) return;

    showBtn.addEventListener('click', () => {
      if (!state.wallet) return;
      canvas.innerHTML = '';
      try {
        new QRCode(canvas, {
          text:          state.wallet.public_key,
          width:         220,
          height:        220,
          colorDark:    '#0F172A',
          colorLight:   '#FFFFFF',
          correctLevel:  QRCode.CorrectLevel.M,
        });
      } catch (e) {
        canvas.innerHTML = '<p class="bf-hint">QR library not loaded.</p>';
      }
      overlay.classList.remove('d-none');
    });

    const close = () => overlay.classList.add('d-none');
    closeBtn.addEventListener('click', close);
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });

    // Enable QR button when wallet is generated
    const origWalletCreate = $('#createWalletBtn');
    if (origWalletCreate) {
      const observer = new MutationObserver(() => {
        showBtn.disabled = !state.wallet;
      });
      observer.observe(origWalletCreate, { attributes: true });
    }
  }

  /* ------------------------------------------------------------------ *
   * Quick-win: Export Chain as JSON / CSV
   * ------------------------------------------------------------------ */
  function initExport() {
    const jsonBtn = $('#exportJsonBtn');
    const csvBtn  = $('#exportCsvBtn');

    function download(content, filename, mime) {
      const blob = new Blob([content], { type: mime });
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      a.href     = url;
      a.download = filename;
      a.style.display = 'none';          // prevent any layout flash
      document.body.appendChild(a);
      a.click();
      // Delay revocation — revoking synchronously cancels the download in
      // Firefox and some Chrome versions before the browser can start it.
      setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 150);
    }

    if (jsonBtn) {
      jsonBtn.addEventListener('click', async () => {
        jsonBtn.disabled = true;
        jsonBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        try {
          // Always fetch fresh chain data so the export reflects the real DB state
          let chainData = state.chain;
          if (state.liveMode) {
            try {
              const fresh = await safeFetch(ENDPOINTS.chain);
              chainData = Array.isArray(fresh) ? fresh : (fresh.chain || state.chain);
            } catch { /* fall back to cached state */ }
          }
          if (!chainData.length) { showToast('No Data', 'Chain is empty — mine a block first.', 'info'); return; }
          download(
            JSON.stringify(chainData, null, 2),
            `blockfusion-chain-${Date.now()}.json`,
            'application/json'
          );
          showToast('Exported', `Chain (${chainData.length} blocks) downloaded as JSON.`, 'success');
        } finally {
          jsonBtn.disabled = false;
          jsonBtn.innerHTML = '<i class="bi bi-filetype-json"></i> JSON';
        }
      });
    }

    if (csvBtn) {
      csvBtn.addEventListener('click', async () => {
        csvBtn.disabled = true;
        csvBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        try {
          let chainData = state.chain;
          if (state.liveMode) {
            try {
              const fresh = await safeFetch(ENDPOINTS.chain);
              chainData = Array.isArray(fresh) ? fresh : (fresh.chain || state.chain);
            } catch { /* fall back to cached state */ }
          }
          if (!chainData.length) { showToast('No Data', 'Chain is empty — mine a block first.', 'info'); return; }
          const rows = ['Block Index,Timestamp,Proof,Previous Hash,Tx Count'];
          chainData.forEach(b => {
            const ts = b.timestamp ? new Date(b.timestamp * 1000).toISOString() : '';
            const prevHash = `"${b.previous_hash || ''}"`;   // quote to avoid CSV comma issues
            rows.push(`${b.index},${ts},${b.proof},${prevHash},${(b.transactions||[]).length}`);
          });
          download(rows.join('\n'), `blockfusion-chain-${Date.now()}.csv`, 'text/csv');
          showToast('Exported', `Chain (${chainData.length} blocks) downloaded as CSV.`, 'success');
        } finally {
          csvBtn.disabled = false;
          csvBtn.innerHTML = '<i class="bi bi-filetype-csv"></i> CSV';
        }
      });
    }
  }

  /* ------------------------------------------------------------------ *
   * Quick-win: Mempool
   * ------------------------------------------------------------------ */
  async function loadMempool() {
    const feed  = $('#mempoolFeed');
    const count = $('#mempoolCount');
    if (!feed) return;

    try {
      const data = await safeFetch(ENDPOINTS.mempool);
      const pending = data.pending || [];
      count.textContent = `${pending.length} pending`;

      if (!pending.length) {
        feed.innerHTML = '<div class="bf-empty-state"><i class="bi bi-inbox"></i><p>Mempool is empty — all transactions are confirmed.</p></div>';
        return;
      }

      feed.innerHTML = pending.map(tx => `
        <div class="bf-mempool-item">
          <div class="bf-mempool-avatar"><i class="bi bi-hourglass-split"></i></div>
          <div class="bf-mempool-info">
            <div class="bf-mempool-parties">${truncateKey(tx.sender,20)} → ${truncateKey(tx.recipient,20)}</div>
            <div class="bf-mempool-label">UNCONFIRMED · ID #${tx.id}</div>
          </div>
          <div class="bf-mempool-amount">${tx.amount} BFC</div>
        </div>`).join('');
    } catch {
      feed.innerHTML = '<div class="bf-empty-state"><i class="bi bi-wifi-off"></i><p>Could not load mempool — backend offline.</p></div>';
    }
  }

  function initMempool() {
    loadMempool();
    const refreshBtn = $('#mempoolRefreshBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', loadMempool);
  }

  /* ------------------------------------------------------------------ *
   * Quick-win: Transaction Search
   * ------------------------------------------------------------------ */
  function initTxSearch() {
    const input     = $('#txSearchInput');
    const searchBtn = $('#txSearchBtn');
    const clearBtn  = $('#txSearchClear');
    const results   = $('#txSearchResults');
    const countEl   = $('#txSearchCount');
    if (!input || !results) return;

    async function doSearch() {
      const q = input.value.trim();
      if (q.length < 3) {
        showToast('Too Short', 'Enter at least 3 characters to search.', 'info');
        return;
      }
      results.classList.remove('d-none');
      results.innerHTML = '<div class="bf-hint"><span class="spinner-border spinner-border-sm me-1"></span> Searching…</div>';
      try {
        const data = await safeFetch(`${ENDPOINTS.search}?q=${encodeURIComponent(q)}`);
        const list = data.results || [];
        countEl.textContent = `${list.length} result${list.length !== 1 ? 's' : ''}`;

        if (!list.length) {
          results.innerHTML = '<div class="bf-hint"><i class="bi bi-search me-1"></i>No transactions found for that address fragment.</div>';
          return;
        }

        results.innerHTML = list.map(r => `
          <div class="bf-search-result">
            <div class="bf-search-result-meta">
              <div class="bf-search-result-parties">${truncateKey(r.sender,22)} → ${truncateKey(r.recipient,22)}</div>
              <div class="bf-search-result-block"><i class="bi bi-box-seam me-1"></i>Block #${r.block_index} · ${fmtTime(r.timestamp)}</div>
            </div>
            <div class="bf-search-result-amount">${r.amount} BFC</div>
          </div>`).join('');
      } catch {
        results.innerHTML = '<div class="bf-hint"><i class="bi bi-exclamation-triangle me-1"></i>Search unavailable — backend offline.</div>';
      }
    }

    searchBtn.addEventListener('click', doSearch);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
    clearBtn.addEventListener('click', () => {
      input.value = '';
      results.classList.add('d-none');
      results.innerHTML = '';
      countEl.textContent = '';
    });
  }

  /* ------------------------------------------------------------------ *
   * Init
   * ------------------------------------------------------------------ */
  document.addEventListener('DOMContentLoaded', () => {
    initNetworkCanvas();
    initSidebar();
    initRefreshButton();
    initExplorerSearch();
    initWallet();
    initTxForm();
    initMining();
    initCharts();
    // Quick-win inits
    initQRCode();
    initExport();
    initMempool();
    initTxSearch();

    loadChain().then(() => {
      // Force a chart update after data is loaded to guarantee canvas renders
      requestAnimationFrame(() => {
        updateCharts();
        if (state.charts.growth) state.charts.growth.resize();
        if (state.charts.volume) state.charts.volume.resize();
      });
      startAutoRefresh();
    });
  });
})();