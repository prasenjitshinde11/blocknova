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
  const API_BASE = 'http://127.0.0.1:5000';
  const ENDPOINTS = {
    root: '/',
    mine: '/mine',
    chain: '/chain',
    walletCreate: '/wallet/create',
    txNew: '/transactions/new',
    walletTx: '/wallet/transactions',
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
  function renderStats() {
    const chain = state.chain;
    const totalBlocks = chain.length;
    const totalTx = chain.reduce((sum, b) => sum + ((b.transactions && b.transactions.length) || 0), 0);
    const wallets = new Set();
    chain.forEach(b => (b.transactions || []).forEach(t => {
      if (t.sender && t.sender !== 'genesis') wallets.add(t.sender);
      if (t.recipient) wallets.add(t.recipient);
    }));

    $('#stat-blocks').textContent = totalBlocks.toLocaleString();
    $('#stat-tx').textContent = totalTx.toLocaleString();
    $('#stat-wallets').textContent = wallets.size.toLocaleString();
    $('#stat-network').innerHTML = state.liveMode
      ? '<span style="color:var(--bf-green)">Healthy</span>'
      : '<span style="color:var(--bf-amber)">Demo</span>';

    $('#stat-blocks-trend').textContent = totalBlocks ? `+${Math.min(totalBlocks, 3)} this session` : '';
    $('#stat-tx-trend').textContent = totalTx ? `${totalTx} confirmed` : 'No activity yet';
    $('#stat-wallets-trend').textContent = wallets.size ? 'Across all blocks' : '';
    $('#stat-network-trend').textContent = state.liveMode ? 'All systems nominal' : 'Simulated feed';
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
    [...state.chain].sort((a, b) => b.index - a.index).forEach(block => {
      (block.transactions || []).forEach(t => allTx.push({ ...t, blockIndex: block.index, ts: block.timestamp }));
    });
    const latest = allTx.slice(0, 8);

    if (!latest.length) {
      feed.innerHTML = `<div class="bf-card bf-glass bf-empty-state"><i class="bi bi-inboxes"></i><p>No transactions yet. Submit one to see it here.</p></div>`;
      return;
    }

    feed.innerHTML = latest.map(t => `
      <div class="bf-tx-item fade-up">
        <div class="bf-tx-avatar"><i class="bi bi-arrow-left-right"></i></div>
        <div class="bf-tx-main">
          <div class="bf-tx-parties">${truncateKey(t.sender, 14)} <i class="bi bi-arrow-right mx-1"></i> ${truncateKey(t.recipient, 14)}</div>
          <div class="bf-tx-meta">Block #${t.blockIndex} · ${fmtTime(t.ts)}</div>
        </div>
        <div class="bf-tx-amount">${t.amount ?? '0'} BFC</div>
      </div>`).join('');
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
    const t = chartTheme();
    Chart.defaults.font.family = "'IBM Plex Sans', sans-serif";
    Chart.defaults.color = t.text;

    const growthCtx = $('#blockGrowthChart').getContext('2d');
    const growthGradient = growthCtx.createLinearGradient(0, 0, 0, 260);
    growthGradient.addColorStop(0, 'rgba(61,127,255,0.35)');
    growthGradient.addColorStop(1, 'rgba(61,127,255,0)');

    state.charts.growth = new Chart(growthCtx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [{
          label: 'Blocks',
          data: [],
          borderColor: t.blue,
          backgroundColor: growthGradient,
          fill: true,
          tension: 0.4,
          pointRadius: 3,
          pointBackgroundColor: t.blue,
          pointBorderColor: '#0C0F17',
          borderWidth: 2,
        }],
      },
      options: chartOptions(t, false),
    });

    const volCtx = $('#txVolumeChart').getContext('2d');
    const volGradient = volCtx.createLinearGradient(0, 0, 0, 260);
    volGradient.addColorStop(0, 'rgba(22,199,132,0.85)');
    volGradient.addColorStop(1, 'rgba(22,199,132,0.15)');

    state.charts.volume = new Chart(volCtx, {
      type: 'bar',
      data: {
        labels: [],
        datasets: [{
          label: 'Transactions',
          data: [],
          backgroundColor: volGradient,
          borderRadius: 6,
          maxBarThickness: 34,
        }],
      },
      options: chartOptions(t, true),
    });
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
    const labels = sorted.map(b => `#${b.index}`);
    const cumulative = sorted.map((_, i) => i + 1);
    const txCounts = sorted.map(b => (b.transactions && b.transactions.length) || 0);

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
  }

  /* ------------------------------------------------------------------ *
   * Render orchestrator
   * ------------------------------------------------------------------ */
  function renderAll() {
    renderStats();
    renderExplorer($('#blockSearchInput') ? $('#blockSearchInput').value.trim() : null);
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
        pubOut.textContent = wallet.public_key;
        privOut.textContent = wallet.private_key;
        downloadBtn.disabled = false;
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
        navigator.clipboard.writeText(text)
          .then(() => showToast('Copied', 'Key copied to clipboard.', 'success'))
          .catch(() => showToast('Copy Failed', 'Your browser blocked clipboard access.', 'error'));
      });
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

    clearBtn.addEventListener('click', () => {
      form.reset();
      $$('.bf-input', form).forEach(el => el.classList.remove('is-invalid'));
    });

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
          await safeFetch(ENDPOINTS.txNew, { method: 'POST', body: JSON.stringify(payload) });
        } catch (err) {
          // Demo fallback: append to the most recent block's pending list visually.
          if (state.chain.length) {
            const last = state.chain[state.chain.length - 1];
            last.transactions = last.transactions || [];
            last.transactions.push({ sender, recipient, amount: parseFloat(amount) });
          }
          if (state.liveMode) showToast('Offline Mode', 'Backend unreachable — transaction recorded locally for demo purposes.', 'info');
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

    loadChain().then(() => {
      startAutoRefresh();
    });
  });
})();