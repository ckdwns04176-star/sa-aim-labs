/* =====================================================================
   SA AIM LABS · ui/bridge.js
   ---------------------------------------------------------------------
   index.html 의 정적 화면을 pywebview.api (app/api.py) 에 연결합니다.

   · 창 제어 (최소화 / 닫기 확인 팝업 / 트레이로 숨기기)
   · 병영수첩 전적 불러오기 · 순위표 일괄 갱신 · 닉네임 등록·삭제
   · 스카우팅(솔로/파티 분리) · 듀오 시너지 · 내전 팀 · 파티 맵 밴픽
   · 인게임 오버레이 (대기실 맵 밴 · 상대 분석 · 경기 종료 토스트)
   · 최적화 유틸 (Drs 라플·스나, 마우스 가속, 전원/게임 모드, 백업/복원)
   · 설정 (닉네임 · 오버레이 단축키 · 회원 로그인/클라우드 동기화)
   · 서버가 돌려준 state 로 홈·병영수첩·맵 분석·순위표 다시 그리기

   브라우저로 index.html 을 열면 (pywebview 없음) AL.real = false 로 두고
   index.html 의 샘플 데이터 동작을 그대로 둡니다.
   ===================================================================== */
(function () {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const n0 = (v, d = 0) => (v === null || v === undefined || v === '' || Number.isNaN(Number(v)))
    ? '—' : Number(v).toLocaleString('ko-KR', { minimumFractionDigits: d, maximumFractionDigits: d });

  const AL = (window.AL = window.AL || {});
  AL.real = false;
  AL.ready = false;
  AL.state = null;

  /* v1.0.1 (#7) — 단축키 선택지: F1~F12 + Insert/Delete/Home/End/PgUp/PgDn + 수식키 조합 */
  const HOTKEY_OPTIONS = ['F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12',
    'Insert', 'Delete', 'Home', 'End', 'PgUp', 'PgDn',
    'Ctrl+F8', 'Ctrl+F9', 'Alt+F8', 'Alt+F9',
    'Ctrl+Insert', 'Ctrl+Delete', 'Shift+Insert', 'Shift+Delete',
    'Alt+Insert', 'Alt+Delete', 'None'];

  /* ------------------------------------------------------------------
     v1.0.1 (#10) — 로그인 상태인데 서든 닉네임이 없으면 홈에서 바로 연결 팝업
     · 닉네임 / 병영수첩 주소(…SA) / ouid 모두 connect_nickname 이 처리한다
     ------------------------------------------------------------------ */
  function openNickLink(auto) {
    const mask = $('#nick-mask');
    if (!mask) return;
    if (auto) AL.nickAutoShown = true;
    mask.classList.add('show');
    const input = $('#nick-input');
    if (input) setTimeout(() => input.focus(), 60);
    const box = $('#nick-result');
    if (box) { box.hidden = true; box.textContent = ''; }
  }

  function closeNickLink(later) {
    const mask = $('#nick-mask');
    if (mask) mask.classList.remove('show');
    if (later) AL.nickLater = true;
  }

  async function submitNickLink() {
    const input = $('#nick-input');
    const value = input ? String(input.value || '').trim() : '';
    const box = $('#nick-result');
    if (!value) { toast('닉네임 또는 병영수첩 주소를 입력해 주세요', false); return; }
    if (!AL.real) { toast('앱(.exe)에서 연결할 수 있습니다', false); return; }
    if (box) { box.hidden = false; box.textContent = '조회 중…'; }
    busy(true, '닉네임을 확인하고 전적을 불러오는 중…');
    const res = await call('connect_nickname', value);
    busy(false);
    if (!res || !res.ok) {
      if (box) box.textContent = (res && res.message) || '연결하지 못했습니다';
      toast((res && res.message) || '연결하지 못했습니다', false, 5200);
      return;
    }
    if (res.state) applyState(res.state);
    if (box) box.textContent = res.message || `${res.nickname || value} 연결 완료`;
    toast(`🪖 ${res.message || (res.nickname || value) + ' 연결 완료'}`, true, 5200);
    setTimeout(() => closeNickLink(false), 700);
  }

  AL.openNickLink = openNickLink;
  AL.closeNickLink = closeNickLink;

  /* v1.0.1 — 65초 자동 최신화 등 백그라운드 갱신 후 화면 다시 그리기 */
  async function reloadState() {
    if (!AL.real) return null;
    const res = await call('get_state');
    if (res && res.state) { applyState(res.state); }
    return res && res.state;
  }
  AL.stateReload = reloadState;

  /* v1.0.1 (#11) — 대기실 단축키로 읽은 5인을 파티랭크 슬롯에 채우고 바로 분석 */
  AL.partyAutoFill = async function (names) {
    const list = (Array.isArray(names) ? names : []).map((n) => String(n || '').trim()).filter(Boolean).slice(0, 5);
    if (!list.length) return;
    const input = $('#party-names');
    if (input) input.value = list.join(', ');
    AL.partyAutoNames = list;
    /* 같은 5인을 2분 안에 다시 읽었으면 또 돌리지 않는다 (API 한도 보호) */
    const key = list.join('|').toLowerCase();
    const now = Date.now();
    if (AL.partyAutoKey === key && AL.partyAutoAt && now - AL.partyAutoAt < 120000) return;
    AL.partyAutoKey = key;
    AL.partyAutoAt = now;
    if (typeof runPartyMap === 'function') await runPartyMap(true);
    toast(`파티 ${list.length}명 자동 입력 · 맵 밴·픽 분석 완료`, true, 4200);
  };

  const hasApi = (name) => !!(window.pywebview && window.pywebview.api && typeof window.pywebview.api[name] === 'function');

  /* ------------------------------------------------------------------ 스타일 */
  const style = document.createElement('style');
  style.textContent = [
    '.field input.inp{width:100%;outline:none;font-family:var(--num);font-size:11px;color:#CBD8E8}',
    '.field input.inp::placeholder{color:transparent}',
    /* v1.0.1 — 닉네임 연결 팝업은 라벨 외에 예시가 보여야 한다 */
    '#nick-input::placeholder{color:#6E7F95;opacity:1;font-family:Pretendard,sans-serif;font-size:10px;text-align:left}',
    '.field input.inp:focus{border-color:rgba(56,189,248,.55);box-shadow:0 0 0 2px rgba(56,189,248,.10)}',
    '.set-card{overflow:auto;min-height:0;gap:7px;padding:11px 13px}',
    '.set-card .rowline{padding:6px 0}',
    '.set-card .field{gap:4px}',
    '.set-card .field .inp{height:29px}',
    '.set-card::-webkit-scrollbar{width:6px}',
    '.set-card::-webkit-scrollbar-thumb{background:rgba(148,163,184,.22);border-radius:4px}',
    '.set-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:auto}',
    'input[type=number]::-webkit-inner-spin-button,input[type=number]::-webkit-outer-spin-button{-webkit-appearance:none;margin:0}',
    'input[type=number]{-moz-appearance:textfield;appearance:textfield}',
    '.set-note{font-size:9px;color:#64748B;font-weight:600;line-height:1.55}',
    '.set-note b{color:#A9B9CE}',
    '.bn-empty{position:relative;z-index:2;display:flex;align-items:center;gap:9px;color:#93A4BA;font-size:12px;font-weight:700}',
    '.bn-empty b{color:#FBD48F}',
    '.score-row{display:grid;grid-template-columns:minmax(0,1fr) 92px 40px;align-items:center;gap:9px}',
    '.score-row .nm{font-size:11px;font-weight:700;color:#E4ECF8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.score-row .ar{font-size:8.5px;color:#6E7F95;font-weight:600;margin-top:2px}',
    '.score-row input{height:29px;border-radius:7px;background:rgba(7,11,19,.8);border:1px solid rgba(148,163,184,.2);',
    '  color:#F1F6FF;font-family:var(--num);font-size:12px;text-align:right;padding:0 9px;outline:none}',
    '.score-row input:focus{border-color:rgba(245,158,11,.6);box-shadow:0 0 0 2px rgba(245,158,11,.12)}',
    '.score-row .unit{font-size:9px;color:#7E8FA6;font-weight:700}',
    '.br-view table.maps-t td.l{text-align:left}',
    '.set-status{font-size:9.5px;font-weight:700;color:#8B9BB1}',
    '.set-status.on{color:#9AE6C9}',
    '#al-bridge-error{position:fixed;inset:0;z-index:10000;display:none;align-items:center;justify-content:center;',
    '  background:radial-gradient(120% 120% at 50% 0%,#0D1626 0%,#070B13 60%);padding:28px}',
    '#al-bridge-error[hidden]{display:none!important}',
    '#al-bridge-error .abx-card{width:min(560px,100%);border:1px solid rgba(148,163,184,.18);border-radius:16px;',
    '  background:rgba(11,17,28,.92);box-shadow:0 24px 60px rgba(0,0,0,.55);padding:26px 26px 22px;',
    '  display:flex;flex-direction:column;gap:11px}',
    '#al-bridge-error .abx-tag{font-size:9.5px;font-weight:800;letter-spacing:.14em;color:#F0938C}',
    '#al-bridge-error .abx-title{margin:0;font-size:19px;font-weight:800;color:#EDF4FF}',
    '#al-bridge-error .abx-msg{margin:0;font-size:12.5px;line-height:1.65;color:#B9C7D9;white-space:pre-wrap;word-break:break-word}',
    '#al-bridge-error .abx-hints{margin:2px 0 0;padding-left:16px;font-size:11.5px;line-height:1.7;color:#8FA2BA}',
    '#al-bridge-error .abx-log{display:flex;align-items:center;gap:9px;flex-wrap:wrap;font-size:10px;color:#7E8FA6;',
    '  background:rgba(7,11,19,.75);border:1px solid rgba(148,163,184,.14);border-radius:9px;padding:8px 10px}',
    '#al-bridge-error .abx-log code{font-family:var(--num);font-size:10px;color:#C8D6E6;word-break:break-all}',
    '#al-bridge-error .abx-status{font-size:10.5px;font-weight:700;color:#FBD48F}',
    '#al-bridge-error .abx-status[hidden]{display:none}',
    '#al-bridge-error .abx-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}',
    '#al-bridge-error .abx-btn{height:34px;padding:0 15px;border-radius:9px;border:1px solid rgba(148,163,184,.26);',
    '  background:rgba(148,163,184,.08);color:#D7E3F2;font-family:inherit;font-size:11.5px;font-weight:800;cursor:pointer}',
    '#al-bridge-error .abx-btn:hover{background:rgba(148,163,184,.16)}',
    '#al-bridge-error .abx-btn.primary{background:#F59E0B;border-color:#F59E0B;color:#1B1300}',
    '#al-bridge-error .abx-btn.primary:hover{background:#FBBF24}',
    '#al-bridge-error .abx-btn.ghost{background:transparent;color:#8B9BB1}'
  ].join('\n');
  document.head.appendChild(style);

  /* ------------------------------------------------------------------ 토스트 */
  let toastTimer = null;
  function toast(message, ok, ms, action) {
    const t = $('#app-toast');
    if (!t) return;
    t.textContent = '';
    t.appendChild(document.createTextNode(String(message || '')));
    if (action && action.label) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'tt-act';
      btn.textContent = action.label;
      btn.addEventListener('click', (e) => { e.stopPropagation(); if (typeof action.onClick === 'function') action.onClick(); });
      t.appendChild(btn);
    }
    t.classList.toggle('ok', !!ok);
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), ms || (ok ? 2600 : 4200));
  }
  AL.toast = toast;

  function fail(result, fallback) {
    const message = (result && result.message) || fallback || '요청을 처리하지 못했습니다';
    const quota = !!(result && (result.quota || /한도|OPENAPI00007/.test(message)));
    toast(quota ? (message || '오늘 넥슨 API 일일 호출 한도(1,000건)를 초과했어요. 자정 이후 초기화됩니다')
                : message, false, quota ? 8000 : 4200);
    return result;
  }

  /* ------------------------------------------------------------------ API 호출 */
  async function call(name, ...args) {
    if (!hasApi(name)) {
      return { ok: false, bridge: false, message: '앱 브리지가 연결되지 않았습니다 · .exe 로 실행해 주세요' };
    }
    try {
      const result = await window.pywebview.api[name](...args);
      return result === undefined || result === null ? { ok: true } : result;
    } catch (err) {
      return { ok: false, message: String((err && err.message) || err) };
    }
  }
  AL.call = call;

  /* ------------------------------------------------------------------ 진행 표시 */
  let busyTimer = null;
  function busy(on, label) {
    const el = $('#busy');
    if (!el) return;
    clearTimeout(busyTimer);
    if (on) {
      const l = $('#busy-label');
      const c = $('#busy-count');
      if (l) l.textContent = label || '전적을 불러오는 중…';
      if (c) c.textContent = '';
      el.classList.add('show');
      busyTimer = setTimeout(() => el.classList.remove('show'), 120000);
    } else {
      el.classList.remove('show');
    }
  }
  window.__alProgress = function (done, total, label) {
    const el = $('#busy');
    if (!el || !el.classList.contains('show')) {
      if (total && done >= total) return;
      busy(true, label || '전적을 불러오는 중…');
    }
    const c = $('#busy-count');
    if (c) c.textContent = total ? ` ${done} / ${total}` : '';
    const l = $('#busy-label');
    if (l && label) l.textContent = label;
  };

  /* ==================================================================
     STATE → 기존 index.html 렌더러에 반영
     ================================================================== */
  const fmtLabel = (iso) => {
    if (!iso) return '—';
    const s = String(iso);
    if (s.length >= 16) return s.replace('T', ' ').slice(5, 16);
    return s;
  };
  const mdLabel = (iso) => String(iso || '').slice(5).replace('-', '.');



  /* 서버 rec → 화면 rec (오늘 내 점수 포함) */


  /* v1.3.0 — 기록 한 건의 종합 점수 (0~3,600) · 저장된 원점수를 게임 기준값으로 환산 */
  const GAME_REF = { kovak: 200, aimlab: 100000 };
  const AREA_KEYS = ['precision', 'wide', 'micro', 'track', 'switch'];

  let digestShown = false;
  /* v1.0.1 (#10) — 로그인(앱) 상태인데 내 서든 닉네임이 없으면 홈에서 바로 연결 팝업 */
  function maybeShowNickLink(st) {
    if (!AL.real || !st) return;
    if (AL.nickLater || AL.nickAutoShown) return;
    if (!(st.account || {}).signed_in) return;   /* 게스트는 로그인 안내가 먼저 */
    if (AL.peek) return;
    const nick = String(((st.nexon || {}).nickname) || ((st.settings || {}).nickname) || '').trim();
    const hasBarracks = !!(st.barracks && (st.barracks.nickname || (st.barracks.basic || {}).name));
    if (nick || hasBarracks) return;
    if ((st.onboarding || {}).needed) return;   // 첫 실행 온보딩이 먼저다
    const modalOpen = $$('.mask.show').some((m) => m.id !== 'nick-mask');
    if (modalOpen) return;
    setTimeout(() => { if (!AL.nickLater) openNickLink(true); }, 500);
  }

  /* v2.0.1 — 예전에는 로그인 안내 모달이 아예 뜨지 않았다(auto 호출 누락).
     이제는 앱 실행 후 한 번, 로그인 안 되어 있으면 로그인/회원가입 모달을 띄운다.
     - 첫 실행 온보딩이 먼저 필요하면 건너뛴다
     - 사용자가 한 번 닫으면 이번 실행 동안은 다시 띄우지 않는다 */
  function maybeShowLogin(st) {
    if (!AL.real || !st || AL.authAutoShown) return;
    const acc = st.account || {};
    if (acc.signed_in) return;
    if ((st.onboarding || {}).needed) return;
    if (digestShown) return;
    const blocking = $$('.mask.show').some((m) => m.id !== 'auth-mask');
    if (blocking) return;
    AL.authAutoShown = true;
    setTimeout(() => {
      if (AL.authLater) return;
      const stillOpen = $$('.mask.show').some((m) => m.id !== 'auth-mask');
      if (stillOpen) return;
      openAuth('login');
      const note = $('#auth-auto-note');
      if (note) note.hidden = false;
    }, 650);
  }

  function maybeShowDigest() {
    const d = AL.state && AL.state.digest;
    if (!d || !d.show || digestShown) return;
    if (AL.peek) return;
    digestShown = true;
    if (typeof renderDigest === 'function') renderDigest(d);
    const mask = $('#digest-mask');
    if (mask) mask.classList.add('show');
  }

  function applyTheme(key) {
    const theme = ['coral', 'ice', 'gold'].includes(String(key)) ? String(key) : 'coral';
    document.documentElement.setAttribute('data-theme', theme);
    AL.theme = theme;
  }

  function applyUiScale(px) {
    const value = Math.max(90, Math.min(125, Math.round(Number(px) || 100))) / 100;
    document.documentElement.style.setProperty('--ui', String(value));
    AL.uiScale = Math.round(value * 100);
  }

  function applyState(st) {
    if (!st) return;
    AL.state = st;
    applyTheme(st.theme || 'coral');
    if (st.ui_scale) applyUiScale(st.ui_scale);
    const meta = st.meta || {};
    const set = st.settings || {};
    const home = st.home || {};
    AL.meta = meta;

    /* ---------- 상단 · 사이드바 ---------- */
    const nick = String(home.nickname || (st.nexon || {}).nickname || set.nickname || '').trim();
    if (nick) ME = nick;
    const pfName = $('#pf-name');
    if (pfName) pfName.textContent = nick || '닉네임 미등록';
    const avatar = $('#pf-avatar');
    if (avatar && nick) {
      const letter = nick.charAt(0).toUpperCase();
      if (avatar.firstChild && avatar.firstChild.nodeType === 3) avatar.firstChild.nodeValue = letter;
      else avatar.insertBefore(document.createTextNode(letter), avatar.firstChild);
    }
    const pillText = $('#tb-pill-text');
    if (pillText) {
      const nx = st.nexon || {};
      const viaProxy = nx.endpoint === 'proxy' || (!nx.connected && !!nx.proxy);
      pillText.textContent = nx.connected ? 'NEXON API 연결됨'
        : viaProxy ? 'NEXON API 연결됨 · 프록시'
        : 'NEXON API 연결 안 됨';
    }
    const sync = $('#sync-line');
    if (sync) {
      const cloud = st.cloud || {};
      sync.innerHTML = cloud.signed_in
        ? `<span class="pulse"></span>로그인 · ${fmtLabel(cloud.last_sync) === '—' ? '동기화 전' : '마지막 ' + fmtLabel(cloud.last_sync)}`
        : `<span class="pulse"></span>자동 저장 · ${fmtLabel(meta.now)}`;
    }
    const clanEl = $('#pf-clan');
    if (clanEl) clanEl.textContent = ((st.barracks || {}).basic || {}).clan && (st.barracks || {}).basic.clan !== '-'
      ? `${st.barracks.basic.clan} 클랜` : '클랜 없음';
    const rank = home.rank || {};
    const solo = rank.solo || {};
    if (solo.rp > 0) {
      const rn = $('#pf-rank-name'); if (rn) rn.textContent = `솔로 ${solo.name || solo.band}`;
      const rp = $('#pf-rp'); if (rp) rp.textContent = `${n0(solo.rp)} RP`;
      const bar = $('#pf-bar-i'); if (bar) bar.style.width = `${Math.max(0, Math.min(100, Number((solo.progress || {}).pct || 0))).toFixed(1)}%`;
      const lo = $('#pf-cap-lo'); if (lo) lo.textContent = n0((solo.progress || {}).start || 0);
      const hi = $('#pf-cap-hi'); if (hi) hi.textContent = (solo.progress || {}).next ? `${n0((solo.progress || {}).end)}` : 'MAX';
    } else {
      const rn = $('#pf-rank-name'); if (rn) rn.textContent = '전적 미연동';
      const rp = $('#pf-rp'); if (rp) rp.textContent = '언랭크';
    }

    /* ---------- 공통 데이터 ---------- */
    const barracks = st.barracks || null;
    MAPS = (barracks && barracks.maps) || [];
    GOALS = (st.goals || []).map((g) => ({
      label: g.label, target: g.target || '', cur: g.cur, tgt: g.tgt, unit: g.unit || '',
      gap: g.gap, gaptxt: g.gaptxt
    }));
    MEMBERS = (st.leaderboard || []).map((m) => ({ ...m }));
    if (!MEMBERS.length && nick) {
      MEMBERS.push({ name: nick, me: true, clan: ((barracks || {}).basic || {}).clan || '친구', weapon: 'sniper',
        solo: 0, party: null, kd: null, dmg: null, win: null });
    }
    if (st.memos) { MEMOS = st.memos; AL.memos = st.memos; }
    if (Array.isArray(st.map_guides)) {
      MAP_GUIDES = {};
      st.map_guides.forEach((g) => { if (g && g.map) MAP_GUIDES[g.map] = g; });
    }
    if (st.digest) DIGEST = st.digest;

    /* ---------- 유틸 ---------- */
    const utils = st.utils || {};
    const nv = utils.mode;
    UTIL.rifle = nv === 'rifle';
    UTIL.sniper = nv === 'sniper';
    UTIL.alias = !!utils.alias;
    if (typeof utils.accel === 'boolean') UTIL.accel = utils.accel;
    UTIL.backups = (utils.backups || []).length;
    AL.utils = utils;
    const bkMembers = $('#bk-members');
    if (bkMembers) bkMembers.textContent = `${n0((st.leaderboard || []).length)}명`;
    const lbMe = $('#lb-me-name');
    if (lbMe) lbMe.textContent = nick || '—';

    /* ---------- 다시 그리기 ---------- */
    refreshAll();
    renderUtilsState(utils);
    if (AL.peek) renderPeek(); else renderBarracks(barracks, st);
    renderSettings(st);
    renderAccount(st);
    if (AL.syncOverlayPane) AL.syncOverlayPane(st);
    renderCloudState(st);
    renderUtilRecent(st);
    if (typeof compactGrayNotes === 'function') compactGrayNotes();
    renderUpdate(st.update);
    renderTray(st.tray);
    maybeShowDigest();
    if (typeof drawHome === 'function') drawHome();
    maybeShowOnboarding(st);
    maybeShowUpdateGate(st);
    maybeShowLogin(st);
    maybeShowNickLink(st);
    startTiltPoll();
  }
  AL.applyState = applyState;

  /* ==================================================================
     대시보드 · 기타 렌더 보조
     ================================================================== */

  function renderUtilsState(utils) {
    if (utils && utils.last_changes) LAST_CHANGES = utils.last_changes;
    const points = (utils.points || []);
    const box = $('#nv-points');
    if (box) {
      box.innerHTML = points.length
        ? points.map((p) => `<div class="gi"><b>${esc(String(p.at || '').slice(11, 19) || '—')}</b>${esc(p.label || '설정 변경')}<i>복구 가능</i></div>`).join('')
        : '<div class="gi"><b>—</b>복구 지점이 없습니다<i>백업 없음</i></div>';
    }
    const backup = $('#nv-backup');
    if (backup) backup.textContent = points.length ? (String(points[0].at || '').slice(11, 19) || '—') : '—';
    const lbl = $('#bk-last');
    if (lbl) lbl.textContent = utils.last_backup_at ? String(utils.last_backup_at).replace('T', ' ').slice(0, 16) : '—';
    const rst = $('#bk-restore');
    if (rst) rst.textContent = utils.last_restore_at ? String(utils.last_restore_at).replace('T', ' ').slice(0, 16) : '—';
    const chip = $('#accel-chip');
    if (chip && typeof utils.accel !== 'boolean') {
      chip.className = 'ustate';
      chip.textContent = '확인 불가';
    }
  }

  function partyBand(rp) {
    const bands = [
      { min: 0, n: '언랭크', img: 'solo_100' },
      { min: 1600, n: '실버 III', img: 'party_10' },
      { min: 1800, n: '골드 I', img: 'party_10' },
      { min: 2000, n: '골드 II', img: 'party_10' },
      { min: 2200, n: '골드 III', img: 'party_10' },
      { min: 2400, n: '마스터 I', img: 'party_10' },
      { min: 2600, n: '마스터 II', img: 'party_11' },
      { min: 2800, n: '마스터 III', img: 'party_12' },
      { min: 3000, n: '그랜드마스터 I', img: 'party_13' },
      { min: 3200, n: '그랜드마스터 II', img: 'party_14' },
      { min: 3400, n: '그랜드마스터 III', img: 'party_15' },
      { min: 3600, n: '레전드', img: 'party_16' }
    ];
    let out = bands[0];
    bands.forEach((b) => { if ((rp || 0) >= b.min) out = b; });
    return out;
  }

  function rankCard(kind, tierRow) {
    const rp = Number((tierRow || {}).rp || 0);
    const band = kind === 'party' ? partyBand(rp) : bandOf(rp);
    /* v2.0.1 — 이름 우선. 별레전드·불레전드는 RP 컷이 없어 이름으로만 구분된다 */
    const name = (window.tierNameOf ? tierNameOf((tierRow || {}).name, rp, kind === 'party') : null)
      || (tierRow || {}).name || band.n;
    const mark = window.tierMark ? tierMark((tierRow || {}).name, rp, kind === 'party') : band.img;
    let start = 0;
    let end = 200;
    if (kind === 'party') {
      start = [1600, 1800, 2000, 2200, 2400, 2600, 2800, 3000, 3200, 3400, 3600].filter((v) => rp >= v).pop() || 0;
      end = start ? start + 200 : 1600;
    } else {
      start = band.min;
      const next = SOLO_BANDS.find((b) => b.min > start);
      end = next ? next.min : start + 200;
    }
    const pct = end > start ? Math.max(0, Math.min(100, ((rp - start) / (end - start)) * 100)) : 100;
    const remain = Math.max(0, end - rp);
    return `<div class="card rank">
      <div class="rank-emb"><img src="assets/${escAttr(mark)}.png" alt="${escAttr(name)}"></div>
      <div class="rank-body">
        <div class="rank-mode"><span class="mode">${kind === 'party' ? '랭크전 파티' : '랭크전 솔로'}</span>${kind === 'party' ? '2인 이상' : '개인전'}</div>
        <div class="rank-title ${kind === 'party' ? 'v2' : ''}"><h3>${name}</h3><span class="rp">${n0(rp)}<i>RP</i></span></div>
        <div class="rank-bar ${kind === 'party' ? 'gold' : ''}"><i style="width:${pct.toFixed(1)}%"></i></div>
        <div class="rank-foot"><span>${n0(start)} → ${n0(end)}</span><span class="need">다음 구간까지 <b>${n0(remain)} RP</b></span></div>
      </div>
    </div>`;
  }

  function renderCoach(barracks) {
    const list = $('#br-coach-list');
    const chip = $('#br-coach-chip');
    const sub = $('#br-coach-sub');
    if (!list) return;
    const tips = (barracks && barracks.play_coaching) || [];
    if (!tips.length) {
      list.innerHTML = `<div class="coach-row"><div class="ic">📊</div><div class="tx">
        <div class="t">코칭할 표본이 아직 없습니다</div>
        <div class="d">병영수첩 <b>전적 불러오기</b>를 누르면 최근 100판 승률·K/D·딜량·맵별 강약으로 바로 계산합니다.</div>
      </div></div>`;
      if (chip) { chip.textContent = '표본 없음'; chip.className = 'chip'; }
      return;
    }
    list.innerHTML = tips.map((t) => `
      <div class="coach-row ${t.tone || ''}">
        ${(function(){
          const head=String(t.title||'').split(' ')[0];
          const src=window.mapImg?window.mapImg(head):'';
          return src?`<div class="coach-map"><img src="${src}" alt=""></div>`:`<div class="ic">${t.icon || '🧭'}</div>`;
        })()}
        <div class="tx">
          <div class="t" title="${esc(t.title)}">${esc(t.title)}</div>
          <div class="d">${esc(t.text)}</div>
        </div>
      </div>`).join('');
    if (chip) {
      const stats = barracks.stats || {};
      chip.textContent = `최근 ${stats.games || 0}판 · ${tips.length}가지`;
      chip.className = 'chip amber';
    }
    if (sub) {
      const tier = barracks.tier || {};
      const solo = (tier.solo || {}).rp || 0;
      const party = (tier.party || {}).rp || 0;
      sub.textContent = `솔로 ${Number(solo).toLocaleString()} RP · 파티 ${Number(party).toLocaleString()} RP`
        + ' · 최근 100판 기준';
    }
  }
  AL.renderCoach = renderCoach;

  /* ==================================================================
     v2.0.0 · 회원(로그인·회원가입·비밀번호 찾기) · 요금제
     ================================================================== */
  function renderAccount(st) {
    const acc = (st && st.account) || {};
    const plan = acc.plan || {};
    const signed = !!acc.signed_in;
    const btn = $('#home-plan');
    if (btn) {
      btn.textContent = signed ? (plan.label || '무료') : '로그인';
      btn.classList.toggle('pro', plan.plan === 'pro');
      btn.title = signed ? `${acc.email || ''} · ${plan.plan === 'pro' ? 'PRO' : '무료'}` : '로그인 · 회원가입';
    }
    const sub = $('#home-account-sub');
    if (sub) sub.textContent = signed ? (acc.email || '로그인됨') : '로그인하면 다른 PC에서도 이어집니다';
    const chip = $('#home-account-st');
    if (chip) {
      chip.textContent = signed ? (plan.plan === 'pro' ? 'PRO' : '무료') : '로그인';
      chip.className = 'st ' + (plan.plan === 'pro' ? 'on' : '');
    }
    const quota = acc.quota || {};
    AL.quota = quota;
  }
  AL.renderAccount = renderAccount;

  const AUTH_LABEL = { login: ['로그인', '로그인'], signup: ['회원가입', '회원가입'], recover: ['비밀번호 찾기', '재설정 메일 보내기'] };

  function openAuth(mode) {
    const mask = $('#auth-mask');
    if (!mask) return;
    setAuthMode(mode || 'login');
    mask.classList.add('show');
    const email = $('#auth-email');
    const st = AL.state || {};
    if (email && !email.value) email.value = ((st.account || {}).email || (st.cloud || {}).email || '');
    setTimeout(() => (email || {}).focus && email.focus(), 80);
  }
  AL.openAuth = openAuth;

  function setAuthMode(mode) {
    AL.authMode = mode;
    $$('#auth-tabs [data-auth-tab]').forEach((b) => b.classList.toggle('on', b.dataset.authTab === mode));
    const labels = AUTH_LABEL[mode] || AUTH_LABEL.login;
    const title = $('#auth-title'); if (title) title.textContent = labels[0];
    const submit = $('#auth-submit'); if (submit) submit.textContent = labels[1];
    const pw = $('#auth-pw-field'); if (pw) pw.hidden = mode === 'recover';
    const note = $('#auth-note');
    if (note) {
      note.innerHTML = mode === 'recover'
        ? '가입한 이메일로 <b>비밀번호 재설정 링크</b>를 보냅니다 · 링크에서 새 비밀번호를 정하면 됩니다.'
        : mode === 'signup'
          ? '이메일 확인이 켜져 있으면 인증 메일이 먼저 옵니다 · 지금은 무료 운영 기간이라 모든 기능을 제한 없이 씁니다.'
          : '로그인하면 랭크전 전적·설정이 클라우드에 백업됩니다 · 지금은 무료 운영 기간입니다.';
    }
  }

  async function submitAuth() {
    const mode = AL.authMode || 'login';
    const emailEl = $('#auth-email'), pwEl = $('#auth-password');
    const email = emailEl ? String(emailEl.value || '').trim() : '';
    const pw = pwEl ? String(pwEl.value || '') : '';
    if (!email) { toast('이메일을 입력해 주세요', false); return; }
    if (mode !== 'recover' && !pw) { toast('비밀번호를 입력해 주세요', false); return; }
    if (!AL.real) { toast('앱(.exe)에서 로그인합니다', false); return; }
    busy(true, mode === 'recover' ? '재설정 메일을 보내는 중…' : '로그인하는 중…');
    const res = mode === 'login' ? await call('auth_login', email, pw)
      : mode === 'signup' ? await call('auth_signup', email, pw)
      : await call('auth_recover', email);
    busy(false);
    if (!res || !res.ok) { toast((res && res.message) || '요청을 처리하지 못했습니다', false, 5200); return; }
    if (res.state) applyState(res.state);
    toast(res.message || '완료했습니다', true, 4200);
    if (mode !== 'recover') {
      const mask = $('#auth-mask');
      if (mask) mask.classList.remove('show');
      if (pwEl) pwEl.value = '';
    }
  }
  AL.submitAuth = submitAuth;

  /* ==================================================================
     v2.0.0 · 타인 A + 타인 B 듀오 시너지 (자유 1:1 비교 대체)
     ================================================================== */
  function duoBar(label, value, unit) {
    const v = value === null || value === undefined ? null : Number(value);
    return `<span class="duo-stat"><i>${label}</i><b>${v === null || Number.isNaN(v) ? '—' : `${v.toFixed(1)}${unit || ''}`}</b></span>`;
  }
  function renderDuoBetween(res) {
    const box = $('#cmp-result');
    if (!box) return;
    if (!res || !res.ok) {
      box.innerHTML = `<div class="mnote" style="display:block">${esc((res && res.message) || '시너지를 계산하지 못했습니다')}</div>`;
      return;
    }
    const tone = res.tone === 'good' ? 'good' : res.tone === 'warn' ? 'warn' : 'mid';
    const a = res.a || {}, b = res.b || {}, sh = res.shared || {}, d = res.delta || {};
    box.innerHTML = `
      <div class="duo-verdict ${tone}">
        <b>${esc(res.verdict || '')}</b><span>${esc(res.label || '')}</span>
      </div>
      <div class="duo-grid">
        <div class="duo-card"><div class="nm">${esc(a.name || 'A')}</div>
          <div class="row">${duoBar('공동 승률', a.win, '%')}${duoBar('K/D', a.kd, '%')}</div>
          <div class="m">함께 ${n0(sh.games)}판 · 평소 승률 ${a.overall && a.overall.win != null ? `${a.overall.win}%` : '—'}</div></div>
        <div class="duo-card"><div class="nm">${esc(b.name || 'B')}</div>
          <div class="row">${duoBar('공동 승률', b.win, '%')}${duoBar('K/D', b.kd, '%')}</div>
          <div class="m">함께 ${n0(sh.games)}판 · 평소 승률 ${b.overall && b.overall.win != null ? `${b.overall.win}%` : '—'}</div></div>
      </div>
      <div class="duo-sum">
        <div><i>같은 판</i><b>${n0(sh.games)}</b></div>
        <div><i>공동 승률</i><b>${sh.win == null ? '—' : `${sh.win}%`}</b></div>
        <div><i>공동 K/D</i><b>${sh.kd == null ? '—' : `${sh.kd}%`}</b></div>
        <div><i>평소 대비</i><b class="${(d.win || 0) > 0 ? 'up' : (d.win || 0) < 0 ? 'down' : ''}">${d.win == null ? '—' : `${d.win > 0 ? '+' : ''}${d.win}%p`}</b></div>
      </div>
      <div class="mnote" style="display:block">${esc(res.note || '')}</div>
      ${(res.pairs || []).length ? `<div class="duo-pairs">${res.pairs.map((p) => `<div class="row"><span>${esc(p.date || '')}</span><span class="map">${esc(p.map || '')}</span><b class="${p.result === '승' ? 'up' : 'down'}">${esc(p.result || '')}</b><span>${p.a.k}/${p.a.d} · ${p.b.k}/${p.b.d}</span></div>`).join('')}</div>` : ''}`;
  }

  async function runDuoBetween() {
    const left = $('#cmp-left'), right = $('#cmp-right');
    const a = left ? String(left.value || '').trim() : '';
    const b = right ? String(right.value || '').trim() : '';
    if (!a || !b) { toast('두 사람의 닉네임을 모두 입력해 주세요', false); return; }
    if (!AL.real) { toast('앱(.exe)에서 계산합니다', false); return; }
    busy(true, '두 사람의 공동 경기를 찾는 중…');
    const res = await call('duo_between', a, b);
    busy(false);
    renderDuoBetween(res);
  }
  AL.openDuoBetween = function (a, b) {
    const mask = $('#compare-mask');
    if (mask) mask.classList.add('show');
    const left = $('#cmp-left'), right = $('#cmp-right');
    if (left && a) left.value = a;
    if (right && b) right.value = b;
    if (a && b) runDuoBetween();
  };
  AL.renderDuoBetween = renderDuoBetween;

  /* ==================================================================
     v2.0.0 · 최근 상대 팀 (서버 최신 조회 · stale 구분)
     ================================================================== */
  function renderRecentOpp(res) {
    const box = $('#util-opp');
    const meta = $('#util-recent-meta');
    if (!box) return;
    AL.recentOpp = res || null;
    const rows = ((res && res.opponents) || []);
    if (meta) {
      const m = (res && res.match) || {};
      const stamp = m.at || '';
      meta.textContent = res && res.stale
        ? `저장된 판 ${stamp} · 최신 조회 실패${res.error ? ` (${res.error})` : ''}`
        : (m.map ? `${m.map} · ${m.result || ''} · ${stamp}` : '최근 매치');
      meta.classList.toggle('warn', !!(res && res.stale));
    }
    if (!rows.length) {
      box.innerHTML = `<span style="font-size:10px;color:#6E7F95;font-weight:600">${esc((res && res.message) || '상대 5명을 찾지 못했습니다')}</span>`;
      return;
    }
    box.innerHTML = rows.map((n) => `<button class="chip" data-opp="${escAttr(n)}" style="height:24px;font-size:9.5px;cursor:pointer" title="이 닉네임으로 스카우팅">${esc(n)}</button>`).join('')
      + `<button class="ubtn" id="util-opp-run" style="height:24px;padding:0 10px;font-size:9.5px">이 ${rows.length}명 분석</button>`;
    const run = $('#util-opp-run');
    if (run) run.addEventListener('click', () => { AL.pendingScout = rows; runScoutAuto(); });
    box.querySelectorAll('[data-opp]').forEach((btn) => btn.addEventListener('click', () => {
      const input = $('#scout-nicks');
      if (input) input.value = btn.dataset.opp;
      openScout();
    }));
  }

  async function loadRecentOpponents(force) {
    if (!AL.real) return null;
    const res = await call('recent_opponents', force !== false);
    renderRecentOpp(res);
    return res;
  }
  AL.loadRecentOpponents = loadRecentOpponents;
  AL.refreshBarracks = async function () {
    if (!AL.real) { toast('앱(.exe)에서 전적을 최신화합니다', false); return; }
    const res = await call('fetch_barracks', null, null);
    if (res && res.state) applyState(res.state);
    toast((res && res.message) || '전적을 최신화했습니다', !!(res && res.ok), 4200);
    if (window.drawHome) drawHome();
  };
  AL.openWeakMap = function (name) { if (typeof openMapGuide === 'function') openMapGuide(name); };

  function renderBarracks(barracks, st) {
    try { renderCoach(barracks || null); } catch (err) { /* 코칭은 보조 정보 */ }
    const banner = $('#br-banner');
    const ranks = $('#br-ranks');
    const updated = $('#br-updated');
    const lbUpdated = $('#lb-updated');
    const meta = (st && st.meta) || {};

    if (updated) {
      updated.textContent = barracks && barracks.updated_at
        ? `최신화 ${String(barracks.updated_at).replace('T', ' ').slice(5, 16)}`
        : '최신화 —';
    }
    if (lbUpdated) {
      const stamp = (st.leaderboard || []).map((m) => m.updated_at).filter(Boolean).sort().pop();
      lbUpdated.textContent = stamp ? `최신화 ${String(stamp).replace('T', ' ').slice(5, 16)}` : '최신화 —';
    }

    if (!barracks) {
      if (banner) {
        banner.innerHTML = `<div class="bn-empty">🪖 <span>NEXON Open API 키와 닉네임을 저장한 뒤 <b>전적 불러오기</b>를 눌러 주세요</span></div>`;
      }
      if (ranks) ranks.innerHTML = '';
      return;
    }

    const basic = barracks.basic || {};
    const tier = barracks.tier || { solo: { rp: 0, name: '언랭크' }, party: { rp: 0, name: '언랭크' } };
    const stats = barracks.stats || {};
    const soloMark = window.tierMark ? tierMark((tier.solo || {}).name, Number(tier.solo.rp || 0), false) : bandOf(Number(tier.solo.rp || 0)).img;
    /* v1.3.1 — 무기 표기는 '스나' / '라플' 만 (앞의 '주무기 ' 제거) */
    const weaponLabel = Number((barracks.recent || {}).sniper || 0) >= Number((barracks.recent || {}).assault || 0)
      ? '스나' : '라플';
    /* v1.5.0 (#2) — 병영수첩 프로필 사진이 있으면 그대로, 없으면 전술 엠블럼 */
    const _links = (st && st.barracks_links) || {};
    const photo = String(_links.photo || '').trim();
    const hasPhoto = !!photo && !/user_img\.(jpg|png)/i.test(photo);
    const EMBLEM = '<span class="bn-emb"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><use href="#i-shield"/></svg></span>';
    const avaInner = hasPhoto
      ? `${EMBLEM}<img src="${esc(photo)}" alt="" loading="lazy" onerror="this.remove()">`
      : EMBLEM;
    const initial = (barracks.nickname || '?').charAt(0);
    const clanRaw = String(basic.clan || '').trim();
    const clan = (!clanRaw || clanRaw === '-') ? '클랜 없음' : clanRaw;
    /* v1.3.1 — 어제(직전 최신화) 대비 변화량: ▲/▼ + 상승 초록·하락 빨강 그라데이션 */
    const digest = (st && st.digest) || null;
    const deltaOf = (key) => {
      if (!digest || !digest.available) return null;
      const row = (digest.metrics || []).find((m) => m.key === key);
      if (!row || row.diff === null || row.diff === undefined) return null;
      return row.diff;
    };
    const deltaHtml = (key) => {
      const diff = deltaOf(key);
      if (diff === null) return '';
      const up = diff > 0;
      const txt = Math.abs(diff).toLocaleString('ko-KR', { maximumFractionDigits: 1 });
      return `<i class="bn-delta ${up ? 'up' : diff < 0 ? 'down' : 'same'}">${diff === 0 ? '—' : (up ? '▲' : '▼') + ' ' + txt}</i>`;
    };
    const deltaCls = (key) => {
      const diff = deltaOf(key);
      return diff === null ? '' : diff > 0 ? ' up' : diff < 0 ? ' down' : '';
    };

    if (banner) {
      const flow = (st && st.condition && st.condition.streak) || null;
      const flowBadge = (flow && flow.n >= 2)
        ? `<span class="streak-badge ${flow.kind === '승' ? 'win' : 'loss'}">${flow.n}연${flow.kind} 중</span>` : '';
      banner.innerHTML = `
        <div class="emb-bg"><img src="assets/${escAttr(soloMark)}.png" alt=""></div>
        <div class="bn-left">
          <div class="bn-ava${hasPhoto ? ' photo' : ''}">${avaInner}<i></i></div>
          <div>
            <div class="bn-name"><h2 class="bn-self" role="button" tabindex="0" data-ouid="${esc(barracks.ouid || '')}"
                title="내 병영수첩 상세 페이지 열기">${esc(barracks.nickname || '')}</h2>
              <span class="tag ${weaponLabel === '스나' ? 'amber' : 'cyan'}" id="bn-user">${weaponLabel}</span>${flowBadge}</div>
            <div class="bn-sub">
              <a class="bn-clan" role="button" tabindex="0" data-clan="${esc(clan)}" data-ouid="${esc(barracks.ouid || '')}"
                title="${esc(clan)} · 클랜 홈페이지 열기">${esc(clan)}</a>
              ${((st && st.delta) || []).slice(0, 4).map((d) => `<em class="bn-chip ${d.tone === 'up' ? 'up' : 'down'}" title="직전 최신화 대비">${esc(d.text)}</em>`).join('')}
            </div>
          </div>
        </div>
        <div class="bn-stats">
          <div class="bn-stat"><span>최근 100판</span><b>${n0(stats.games)}<i>판</i></b></div>
          <div class="bn-stat${deltaCls('win')}"><span>승률</span><b>${n0(stats.win, 1)}<i>%</i></b>${deltaHtml('win')}</div>
          <div class="bn-stat${deltaCls('kd')}"><span>K/D</span><b>${n0(stats.kd, 1)}<i>%</i></b>${deltaHtml('kd')}</div>
          <div class="bn-stat${deltaCls('dmg')}"><span>평균 딜량</span><b>${n0(stats.dmg)}</b>${deltaHtml('dmg')}</div>
        </div>`;
    }
    if (ranks) ranks.innerHTML = rankCard('solo', tier.solo) + rankCard('party', tier.party);
    /* 맵·약점 분석은 별도 화면 — 데이터만 갱신해 둔다 */
    MAPS = barracks.maps || [];
    if (typeof drawMaps === 'function') drawMaps();
  }

  function renderMatches(barracks) {
    const table = $('#map-table');
    if (!table) return;
    /* v1.3.1 — 저장은 최근 100판이지만 이 화면은 '최근 20판'만 보여 준다
       (맵별 전체 전적은 맵 행을 클릭하면 팝업으로 본다) */
    const rows = (barracks.matches || []).slice(0, 20);
    if (!rows.length) {
      table.innerHTML = '<tbody><tr><td class="l" style="color:#6E7F95">최근 경기가 없습니다</td></tr></tbody>';
      return;
    }
    table.innerHTML = `
      <thead><tr><th class="l" style="width:74px">일시</th><th class="l">맵</th><th>구분</th><th>결과</th><th>K</th><th>D</th><th>K/D</th><th>딜량</th></tr></thead>
      <tbody>${rows.map((m) => `
        <tr>
          <td class="l">${m.date.slice(5)} ${m.time}</td>
          <td class="l map"><span class="map-cell">${(function(){const src=(window.mapImg?window.mapImg(m.map):'');
            return src?`<span class="mthumb"><img src="${src}" alt=""></span>`:`<span class="mthumb"><svg viewBox="0 0 24 24"><use href="#i-map"/></svg></span>`;})()}${esc(m.map)}</span></td>
          <td>${esc(m.type)}</td>
          <td class="${m.result === '승' ? 'pos' : 'neg'}">${m.result}</td>
          <td>${m.k}</td><td>${m.d}</td>
          <td>${n0(m.kd, 1)}</td>
          <td>${m.damage === null || m.damage === undefined ? '—' : n0(m.damage)}</td>
        </tr>`).join('')}</tbody>`;
  }

  function renderMaps(barracks) {
    /* 정렬·약점 클릭은 index.html 의 drawMaps 가 담당한다 (v1.3.0) */
    MAPS = (barracks && barracks.maps) || MAPS || [];
    if (typeof drawMaps === 'function') drawMaps();
  }

  /* ==================================================================
     설정 화면
     ================================================================== */
  function inputRow(id, label, value, opts) {
    const o = opts || {};
    return `<div class="field"><label>${label}</label>
      <input id="${id}" class="inp" value="${value == null ? '' : String(value).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')}"
        placeholder="${o.placeholder || ''}" ${o.type ? `type="${o.type}"` : ''} ${o.maxlength ? `maxlength="${o.maxlength}"` : ''} ${o.readonly ? 'readonly' : ''}>
      ${o.hint ? `<div class="hint">${o.hint}</div>` : ''}</div>`;
  }

  function toggleRow(key, title, desc, on) {
    return `<div class="rowline"><div><div class="t">${title}</div><div class="d">${desc}</div></div>
      <div class="toggle ${on ? '' : 'off'}" data-toggle="${key}" role="switch" style="cursor:pointer"><i></i></div></div>`;
  }

  function renderSettings(st) {
    const set = (st && st.settings) || {};
    const nexon = (st && st.nexon) || {};
    const cloud = (st && st.cloud) || {};
    const meta = (st && st.meta) || {};
    const goals = set.goals || {};
    const account = (st && st.account) || {};
    const plan = account.plan || {};
    const ovSet = ((st && st.overlay) || {}).settings || {};
    const hot = ((st && st.overlay) || {}).hotkeys || {};
    const presets = (st && st.party_presets) || [];
    const reg = (hot.registered || []);

    /* ---------- 계정 · 화면 ---------- */
    const api = $('#set-api');
    if (api) {
      api.innerHTML = `
        <div class="card-hd" style="padding:0">
          <div><div class="card-title"><span class="bar"></span>서든 닉네임 · 화면</div>
            <div class="card-sub">병영수첩 · 친구 순위표 · 테마 · 배율</div></div>
        </div>
        ${inputRow('set-nick', '서든어택 닉네임', set.nickname || '', { maxlength: 12 })}
        <div class="set-row2" style="margin-top:2px">
          <div class="field"><label>테마</label><select id="set-theme" class="inp">
            ${(st.themes || []).map((t) => `<option value="${t.key}" ${st.theme === t.key ? 'selected' : ''}>${t.name}</option>`).join('')}
          </select></div>
          <div class="field"><label>화면 배율</label><select id="set-scale" class="inp">
            ${(st.ui_scales || [90, 100, 115, 125]).map((v) => `<option value="${v}" ${Number(st.ui_scale_setting) === Number(v) ? 'selected' : ''}>${v}%</option>`).join('')}
          </select></div>
        </div>
        ${switchRow('ui_scale_auto', '화면 배율 자동', 'QHD(2560×1440) 이상이면 115%로 자동', !!st.ui_scale_auto)}
        <div class="set-note" style="margin-top:8px">
          마지막 병영수첩 최신화 <b>${nexon.last_barracks_at ? String(nexon.last_barracks_at).replace('T', ' ').slice(0, 16) : '—'}</b> · 출처 Data based on NEXON Open API
        </div>
        <div class="set-actions">
          <button class="btn btn-primary" data-act="save-nick">저장 · 조회</button>
          <button class="btn btn-ghost" data-act="save-goals">목표 저장</button>
        </div>`;
    }

    /* ---------- 오버레이 ---------- */
    const ov = $('#set-overlay');
    if (ov) {
      const failTxt = Object.entries(hot.failed || {}).filter(([, v]) => v && v !== '사용 안 함').map(([k, v]) => `${k}: ${v}`).join(' · ');
      ov.innerHTML = `
        <div class="card-hd" style="padding:0">
          <div><div class="card-title"><span class="bar"></span>인게임 오버레이 · 결과 토스트</div>
            <div class="card-sub">Win32 레이어드 창 · 클릭 통과 · 포커스 비탈취</div></div>
          <span class="ustate ${reg.length ? 'on' : 'warn'}">${reg.length ? '단축키 작동' : '대기'}</span>
        </div>
        ${switchRow('overlay_enabled', '오버레이 사용', `${hot.hotkey_lobby || ovSet.hotkey_lobby || 'Insert'} 대기실·맵 밴 · ${hot.hotkey_scout || ovSet.hotkey_scout || 'Home'} 상대 분석 (게임 화면 위에 표시)`, ovSet.enabled !== false)}
        <div class="set-row2" style="margin-top:4px">
          <div class="field"><label>대기실 · 맵 밴 단축키</label>
            <select id="set-ov-hot1" class="inp">${HOTKEY_OPTIONS.map((k) => `<option value="${k}" ${ovSet.hotkey_lobby === k ? 'selected' : ''}>${k}</option>`).join('')}</select></div>
          <div class="field"><label>상대 분석 단축키</label>
            <select id="set-ov-hot2" class="inp">${HOTKEY_OPTIONS.map((k) => `<option value="${k}" ${ovSet.hotkey_scout === k ? 'selected' : ''}>${k}</option>`).join('')}</select></div>
        </div>
        <div class="set-row2">
          <div class="field"><label>맵 밴 자동 종료 (초)</label>
            <input id="set-ov-sec" class="inp" type="number" min="3" max="40" value="${Number(ovSet.mapban_seconds || 12)}"></div>
          <div class="field"><label>파티 배지 유지 (초)</label>
            <input id="set-ov-psec" class="inp" type="number" min="3" max="40" value="${Number(ovSet.party_seconds || 15)}"></div>
        </div>
        ${switchRow('toast_enabled', '경기 종료 결과 토스트', '한 판이 끝나면 승패·K/D·딜량·연승을 5초 표시', ovSet.toast_enabled !== false)}
        ${switchRow('toast_auto', '새 경기 자동 감지', '최근 전적을 확인해 새 판만 띄웁니다 (1분 5초마다 전적 최신화)', ovSet.toast_auto !== false)}
        <div class="field"><label>토스트 위치</label>
          <select id="set-ov-pos" class="inp">
            <option value="br" ${ovSet.toast_position !== 'tr' ? 'selected' : ''}>우하단 (기본)</option>
            <option value="tr" ${ovSet.toast_position === 'tr' ? 'selected' : ''}>우상단</option>
          </select></div>
        <div class="set-actions">
          <button class="btn btn-primary" data-act="ov-save">오버레이 설정 저장</button>
          <button class="btn btn-ghost" data-act="ov-toast-preview">토스트 미리보기</button>
          <button class="btn btn-ghost" data-act="ov-party-preview">파티 배지 미리보기</button>
          <button class="btn btn-ghost" data-act="ov-scout-preview">상대 분석 미리보기</button>
          <button class="btn btn-ghost" data-act="ov-hide">오버레이 끄기</button>
        </div>
        <div class="set-sub">고정 5인 파티 프리셋</div>
        <div class="set-row2">
          <input id="ov-preset-name" class="key-input" maxlength="20" placeholder="프리셋 이름 (예: 새벽 고정팟)">
          <button class="btn btn-cyan" data-act="preset-save">현재 5명 저장</button>
        </div>
        <div style="display:flex;flex-direction:column;gap:4px;margin-top:6px">
          ${presets.length ? presets.map((p) => `<div class="preset-line"><b>${esc(p.name)}</b><span class="mini">${p.count}명 · ${esc((p.members || []).join(', '))}</span><span class="sp"></span>
            <button class="btn btn-ghost" data-preset-load="${escAttr(p.name)}" style="height:22px;font-size:9.5px;padding:0 7px">불러오기</button>
            <button class="btn btn-ghost" data-preset-del="${escAttr(p.name)}" style="height:22px;font-size:9.5px;padding:0 7px">삭제</button></div>`).join('')
            : '<div class="preset-line"><span class="mini">저장된 프리셋이 없습니다 · 대기실을 스캔한 뒤 저장하면 5명이 그대로 담깁니다</span></div>'}
        </div>
        <div class="overlay-note">
          ${reg.length ? `등록된 단축키 · <b>${reg.join(', ')}</b>` : '단축키가 아직 등록되지 않았습니다 (Windows · 다른 프로그램이 키를 쓰는 중이면 바꿔 주세요)'}${failTxt ? `<br>${esc(failTxt)}` : ''}<br>
          오버레이는 <b>DLL 주입·후킹 없이</b> Win32 레이어드 창으로만 그립니다 · 맵 밴은 화면이 바뀌면 1초 안에 스스로 꺼집니다.
        </div>`;
    }

    /* ---------- 전적 · 알림 ---------- */
    const run = $('#set-run');
    if (run) {
      run.innerHTML = `
        <div class="card-hd" style="padding:0">
          <div><div class="card-title"><span class="bar"></span>전적 최신화 · 알림</div>
            <div class="card-sub">65초 자동 최신화 · 연패 브레이크 · 목표</div></div>
        </div>
        <div class="set-note" style="margin-top:2px">
          최근 전적을 <b>${n0((st.match_refresh || {}).interval || 65)}초</b>마다 새로 읽습니다 · 마지막 확인 ${((st.match_refresh || {}).at ? String(st.match_refresh.at).replace('T', ' ').slice(5, 16) : '—')}
        </div>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:7px">
          ${inputRow('set-goal-win', '승률 %', goals.win != null ? goals.win : 55, { type: 'number' })}
          ${inputRow('set-goal-kd', 'K/D %', goals.kd != null ? goals.kd : 60, { type: 'number' })}
          ${inputRow('set-goal-dmg', '딜량', goals.dmg != null ? goals.dmg : 1400, { type: 'number' })}
          ${inputRow('set-goal-rp', '솔로 RP', goals.rp != null ? goals.rp : 4000, { type: 'number' })}
        </div>
        ${switchRow('tilt_watch', '연패 브레이크 알림', '2~3연패면 잠깐 쉬라는 알림을 띄웁니다', set.tilt_watch !== false)}
        <div class="field"><label>알림 확인 주기 (초)</label>
          <input id="set-tilt-int" class="inp" type="number" min="60" max="1800" value="${Number(set.tilt_interval || 240)}"></div>
        ${switchRow('notify', '트레이 알림', '백그라운드에서 새 소식을 알려 줍니다', set.notify !== false)}
        <div class="set-actions">
          <button class="btn btn-primary" data-act="save-run">저장</button>
          <button class="btn btn-ghost" data-act="refresh-now">지금 전적 최신화</button>
        </div>`;
    }

    /* ---------- 클라우드 ---------- */
    const cl = $('#set-cloud');
    if (cl) {
      cl.innerHTML = `
        <div class="card-hd" style="padding:0">
          <div><div class="card-title"><span class="bar"></span>계정 · 클라우드</div>
            <div class="card-sub">${cloud.signed_in ? '로그인됨' : '로그인하면 다른 PC 와 이어집니다'}</div></div>
          <span class="ustate ${cloud.signed_in ? 'on' : 'warn'}">${cloud.signed_in ? (plan.label || '무료') : '로그아웃'}</span>
        </div>
        <div class="set-note" style="margin-top:2px">
          ${cloud.signed_in
            ? `<b>${esc(cloud.email || account.email || '')}</b> · ${plan.plan === 'pro' ? `PRO (${plan.pro_until_local || '기간제'})` : '무료'} · 지금은 무료 운영 기간이라 모든 기능을 제한 없이 씁니다.`
            : '이메일 하나로 로그인하면 순위표·설정·플레이 잔디가 계정에 백업됩니다.'}
          <br>마지막 동기화 <b>${cloud.last_sync ? String(cloud.last_sync).replace('T', ' ').slice(0, 16) : '—'}</b>
        </div>
        <div class="set-actions">
          ${cloud.signed_in ? '<button class="btn btn-ghost" data-act="auth-open">계정 · 비밀번호</button><button class="btn btn-ghost" data-act="auth-out">로그아웃</button>'
            : '<button class="btn btn-primary" data-act="auth-open">로그인 · 회원가입</button>'}
          <button class="btn btn-ghost" data-act="cloud-push">지금 올리기</button>
          <button class="btn btn-ghost" data-act="cloud-pull">내려받기</button>
        </div>
        ${switchRow('auto_cloud', '자동 백업', '로그인 상태면 전적을 불러올 때마다 클라우드에 백업합니다', set.auto_cloud !== false)}`;
    }

    /* ---------- 앱 · 데이터 ---------- */
    const more = $('#set-more');
    if (more) {
      more.innerHTML = `
        <div class="card-hd" style="padding:0">
          <div><div class="card-title"><span class="bar"></span>앱 · 데이터</div>
            <div class="card-sub">v${meta.version || '2.0.1'} · ${esc(meta.platform || '')}</div></div>
        </div>
        <div class="set-note" style="margin-top:2px">데이터 폴더<br><b style="word-break:break-all">${esc(meta.data_dir || '')}</b></div>
        <div class="set-actions">
          <button class="btn btn-ghost" data-act="open-folder">데이터 폴더 열기</button>
          <button class="btn btn-ghost" data-act="backup">백업 저장</button>
          <button class="btn btn-ghost" data-act="restore">백업 복원</button>
        </div>
        ${switchRow('hotkey_enabled', '단축키 Ctrl+Shift+A', '앱 창 숨기기 / 다시 열기', set.hotkey_enabled !== false)}
        <div class="set-note" style="margin-top:8px">백업 파일에는 API 키 · 로그인 토큰 · 비밀번호가 <b>포함되지 않습니다</b>.</div>`;
    }

    const data = $('#set-data');
    if (data) {
      data.innerHTML = `
        <div class="card-hd" style="padding:0">
          <div><div class="card-title"><span class="bar"></span>데이터 보관</div>
            <div class="card-sub">순위표 · 파티 프리셋 · 플레이 잔디는 계속 보관됩니다</div></div>
        </div>
        <div class="set-note" style="margin-top:2px">
          백업은 <b>설정 · 순위표 · 병영수첩 캐시 · 파티 프리셋 · 플레이 잔디</b>를 한 파일로 저장합니다.
          민감정보(API 키 · Supabase 토큰 · 비밀번호)는 빠집니다.
        </div>
        <button class="danger-btn" data-act="backup" style="padding:0 16px;flex:0 0 auto">지금 백업</button>`;
    }

    const metaLine = $('#set-meta-line');
    if (metaLine) metaLine.textContent = `v${meta.version || '2.0.1'} · 순위표 ${n0((st.leaderboard || []).length)}명`;
  }

  function switchRow(key, title, desc, on) {
    return `<div class="switch-row"><div class="tx">
        <div class="t">${title}</div><div class="d">${desc}</div></div>
      <div class="switch ${on ? 'on' : ''}" data-switch="${key}" role="switch" aria-checked="${on ? 'true' : 'false'}"><i></i></div></div>`;
  }

  function renderCloudState(st) {
    const cloud = (st && st.cloud) || {};
    const sub = $('#set-cloud-sub');
    if (sub) sub.textContent = cloud.signed_in ? `클라우드 · ${cloud.email || '로그인됨'}` : '클라우드 저장소';
    const note = $('#cloud-note');
    if (note) {
      note.innerHTML = cloud.signed_in
        ? `프로젝트 <b>${String(cloud.url || '').replace('https://', '')}</b> · 순위표와 훈련 기록이 계정에 저장되어 다른 PC 에서 이어집니다.`
        : '앱에 기본 클라우드 서버가 이미 들어 있습니다 · 이메일로 로그인하면 다른 PC 와 기록이 이어집니다.';
    }
  }

  function collectSettings() {
    const read = (id) => { const el = $('#' + id); return el ? String(el.value || '').trim() : ''; };
    const num = (id) => { const el = $('#' + id); return el && String(el.value).trim() !== '' ? Number(el.value) : undefined; };
    const patch = {};
    const nick = read('set-nick'); if (nick) patch.nickname = nick;
    const theme = read('set-theme'); if (theme) patch.theme = theme;
    const scale = read('set-scale'); if (scale) patch.ui_scale = Number(scale);
    const hot1 = $('#set-ov-hot1'), hot2 = $('#set-ov-hot2'), pos = $('#set-ov-pos');
    if (hot1) patch.overlay_hotkey_lobby = hot1.value;
    if (hot2) patch.overlay_hotkey_scout = hot2.value;
    if (pos) patch.toast_position = pos.value;
    const sec = num('set-ov-sec'); if (sec !== undefined) patch.overlay_mapban_seconds = sec;
    const psec = num('set-ov-psec'); if (psec !== undefined) patch.overlay_party_seconds = psec;
    const tiltInt = num('set-tilt-int'); if (tiltInt !== undefined) patch.tilt_interval = tiltInt;
    const goals = {};
    ['win', 'kd', 'dmg', 'rp'].forEach((k) => {
      const v = num('set-goal-' + k);
      if (v !== undefined && !Number.isNaN(v)) goals[k] = v;
    });
    if (Object.keys(goals).length) patch.goals = goals;
    /* 스위치 상태 (set-panes 안의 data-switch) */
    $$('#set-panes [data-switch]').forEach((el) => {
      const key = el.dataset.switch;
      patch[key] = el.classList.contains('on');
    });
    return patch;
  }

  /* ==================================================================
     병영수첩 · 순위표
     ================================================================== */

  async function fetchBarracks(andTest) {
    if (!AL.real) return notReady();
    const nickEl = $('#set-nick');
    const keyEl = $('#set-key');
    const nick = nickEl ? String(nickEl.value || '').trim() : '';
    const key = keyEl ? String(keyEl.value || '').trim() : '';
    if (key) {
      const saved = await call('save_settings', { api_key: key });
      if (saved && saved.state) applyState(saved.state);
      if (keyEl) keyEl.value = '';
    }
    busy(true, '병영수첩을 불러오는 중… (최근 100판 · 이미 받은 경기는 캐시 재사용)');
    const result = await call('fetch_barracks', nick || null, key || null);
    busy(false);
    if (!result || !result.ok) {
      const message = (result && result.message) || '전적을 불러오지 못했습니다';
      if (/닉네임|API 키/.test(message)) {
        /* v2.0.1 — 설정은 화면이 아니라 모달이다 (showScreen('settings') 는 없던 화면) */
        if (typeof window.openSettings === 'function') window.openSettings();
      }
      fail(result, message);
      return;
    }
    if (result.state) applyState(result.state);
    AL.peek = null;
    renderPeek();
    /* v1.3.1 — '전적 불러오기' 뒤에 어제(직전 최신화) 대비 변화를 자연스럽게 이어서 보여 준다 */
    try {
      const d = (AL.state && AL.state.digest) || (result.state && result.state.digest);
      if (d && d.available) {
        digestShown = true;
        if (typeof renderDigest === 'function') renderDigest(d);
        const mask = $('#digest-mask');
        if (mask) mask.classList.add('show');
      }
    } catch (err) { /* 변화 팝업은 보조 */ }
    const b = result.barracks || {};
    const calls = typeof result.calls === 'number' ? result.calls : null;
    toast(
      `🪖 ${b.nickname || '전적'} 최신화 · ${n0((b.stats || {}).games)}판 · 승률 ${n0((b.stats || {}).win, 1)}%`
      + (calls !== null ? ` · API ${calls}회` : ''),
      true
    );
  }

  async function fetchLeaderboard() {
    if (!AL.real) return notReady();
    const names = (MEMBERS || []).map((m) => m.name).filter(Boolean).slice(0, 10);
    busy(true, '순위표 전적을 갱신하는 중… (ouid 기준 · 상세는 캐시만 사용)');
    const result = await call('fetch_leaderboard', names);
    busy(false);
    if (!result || !result.ok) return fail(result, '순위표를 갱신하지 못했습니다');
    if (result.state) applyState(result.state);
    toast(result.message || `🔄 ${names.length}명 전체 갱신 완료`, true);
  }

  /* ==================================================================
     병영수첩 · 닉네임 검색 (임시 조회)
     ================================================================== */
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
  /* 속성값용 — 같은 함수로 충분하다 (따옴표까지 막는다) */
  const escAttr = esc;

  /* v2.0.1 — 티어 이름이 있으면 이름으로, 없으면 RP 구간으로 마크를 고른다 */
  function bandImg(rp, name, party) {
    try {
      if (window.tierMark) return tierMark(name, Number(rp) || 0, !!party) || 'solo_100';
      return (bandOf(Number(rp) || 0) || { img: 'solo_100' }).img || 'solo_100';
    } catch (e) { return 'solo_100'; }
  }

  function searchResultBox(html) {
    const box = $('#br-search-result');
    if (!box) return;
    box.hidden = false;
    box.innerHTML = html;
  }

  async function searchUser() {
    const input = $('#br-search-input');
    const nick = input ? String(input.value || '').trim() : '';
    if (!nick) return toast('검색할 서든어택 닉네임을 입력해 주세요', false);
    if (!AL.real) return notReady();
    /* v1.5.0 (#13) — 병영수첩 주소(...SA/detail)를 붙여넣으면 ID 로 바로 연결한다 */
    if (/[0-9A-Fa-f]{16}SA/.test(nick)) {
      busy(true, '병영수첩 ID 로 닉네임을 확인하는 중…');
      const linked = await call('link_barracks_url', nick);
      busy(false);
      if (linked && linked.state) applyState(linked.state);
      searchResultBox(`<div class="sr-none">${esc((linked && linked.message) || '병영수첩 ID 를 저장했습니다')}</div>`);
      toast((linked && linked.message) || '병영수첩 ID 를 저장했습니다', !!(linked && linked.ok), 5200);
      if (linked && linked.ok && linked.nickname) { input.value = linked.nickname; await searchUser(); }
      return;
    }
    searchResultBox('<div class="sr-none">조회 중…</div>');
    busy(true, `'${nick}' 계정을 확인하는 중…`);
    const result = await call('search_user', nick);
    busy(false);
    if (!result || !result.ok) {
      searchResultBox(`<div class="sr-none">${esc((result && result.message) || '조회하지 못했습니다')}</div>`);
      return;
    }
    if (!result.found) {
      searchResultBox(`<div class="sr-none"><b>'${esc(nick)}'</b> · 조회 결과 없음</div>`);
      return;
    }
    const u = result.user || {};
    searchResultBox(`
      <div class="sr-hit" data-peek="1" data-nick="${esc(u.name)}" data-ouid="${esc(u.ouid)}">
        <div class="emb"><img src="assets/${escAttr(bandImg(u.solo, u.solo_tier, false))}.png" alt="${escAttr(u.solo_tier || '')}"></div>
        <div style="min-width:0">
          <div class="nm">${esc(u.name)}</div>
          <div class="meta">솔로 ${esc(u.solo_tier || '언랭크')} · ${n0(u.solo)} RP · ${esc(u.clan && u.clan !== '-' ? u.clan + ' 클랜' : '클랜 없음')}${u.party ? ' · 파티 ' + n0(u.party) + ' RP' : ''}</div>
        </div>
        <span class="go">클릭해서 병영수첩 보기 ›</span>
      </div>`);
  }

  async function openPeek(nick, ouid) {
    if (!AL.real) return notReady();
    busy(true, `'${nick}' 병영수첩을 불러오는 중… (최근 100판)`);
    const result = await call('lookup_barracks', nick, ouid);
    busy(false);
    if (!result || !result.ok) return fail(result, '병영수첩을 불러오지 못했습니다');
    AL.peek = result.barracks || null;
    renderPeek();
    if (typeof showScreen === 'function') showScreen('barracks');
    toast(`🪖 ${(AL.peek || {}).nickname || nick} 병영수첩을 보고 있습니다`, true);
  }

  function peekGoals(barracks) {
    const st = (barracks && barracks.stats) || {};
    const tier = (barracks && barracks.tier) || {};
    const g = ((AL.state && AL.state.settings) || {}).goals || {};
    const rows = [
      ['목표 승률', Number(st.win || 0), Number(g.win || 55), '%'],
      ['목표 K/D', Number(st.kd || 0), Number(g.kd || 60), '%'],
      ['목표 평균 딜량', Number(st.dmg || 0), Number(g.dmg || 1400), ''],
      ['목표 솔로 RP', Number(((tier.solo || {}).rp) || 0), Number(g.rp || 4000), '']
    ];
    GOALS = rows.map(([label, cur, tgt, unit]) => {
      const gap = cur - tgt;
      return { label, cur: Math.round(cur * 10) / 10, tgt, unit, gap,
               gaptxt: `${Math.abs(gap).toFixed(1)}`.replace(/\.0$/, '') };
    });
    if (typeof drawGoals === 'function') drawGoals();
  }

  function renderPeek() {
    const bar = $('#br-peek-bar');
    const nameEl = $('#br-peek-name');
    const on = !!AL.peek;
    if (bar) bar.hidden = !on;
    if (nameEl) nameEl.textContent = on ? (AL.peek.nickname || '—') : '—';
    if (on) {
      renderBarracks(AL.peek, AL.state);
      peekGoals(AL.peek);
    } else if (AL.state) {
      renderBarracks(AL.state.barracks, AL.state);
      if (Array.isArray(AL.state.goals)) {
        GOALS = AL.state.goals.map((g) => ({ label: g.label, cur: g.cur, tgt: g.tgt,
          unit: g.unit || '', gap: g.gap, gaptxt: g.gaptxt }));
        if (typeof drawGoals === 'function') drawGoals();
      }
    }
  }
  AL.renderPeek = renderPeek;

  /* ==================================================================
     최적화 유틸
     ================================================================== */
  function changeBundle(result, mode) {
    const rows = (result && result.changes) || [];
    const baseRows = (result && result.base_changes) || [];
    const count = rows.length + baseRows.length;
    return {
      rows,
      base_rows: baseRows,
      files: (result && result.files) || [],
      profile_items: (result && result.profile_items) || [],
      checklist: (result && result.checklist) || [],
      reason: (result && result.unchanged_reason) || '',
      message: (result && result.message) || '',
      mode: (result && result.mode) || mode,
      at: (AL.state && AL.state.meta && AL.state.meta.now) || '',
      count
    };
  }

  async function applyNvidia(mode) {
    const label = mode === 'rifle' ? '라플' : '스나';
    busy(true, `${label} 프로필 적용 중… (Drs DB 백업 후 교체)`);
    let result = await call('apply_nvidia_profile', mode, false);
    busy(false);
    if (result && !result.ok && (result.can_elevate || result.needs_admin)) {
      // 관리자 권한이 필요 → UAC 승인 요청 (사용자 확인 후)
      const go = window.confirm(`${result.message}\n\n지금 관리자 권한으로 다시 적용할까요?`);
      if (!go) return;
      busy(true, '관리자 권한 요청 중… (UAC 창 확인)');
      const help = await call('apply_nvidia_profile', mode, true);
      if (!help || !help.ok) { busy(false); return fail(help, '관리자 권한 요청을 보내지 못했습니다'); }
      busy(true, '관리자 권한으로 적용하는 중… (UAC 승인 후 최대 30초)');
      result = await call('wait_elevated_result', 30);
      busy(false);
      if (!result || !result.ok) return fail(result, result && result.timeout
        ? 'UAC 창을 승인했는지 확인해 주세요' : '관리자 권한 적용에 실패했습니다');
    }
    if (!result || !result.ok) return fail(result, '프로필을 적용하지 못했습니다');
    if (result.state) applyState(result.state);
    const bundle = changeBundle(result, mode);
    LAST_CHANGES = bundle;
    AL.lastChanges = bundle;
    toast(result.message || (result.game_running
      ? '✅ 적용 완료! 서든어택을 재실행해야 적용됩니다'
      : `${mode === 'rifle' ? '🔫 라플' : '🎯 스나'} 프로필 적용 완료`), true, 5200);
    /* 항상 변경 내역 창을 연다 — 바뀐 값이 없으면 이유와 현재 프로필 값을 보여 준다 */
    setTimeout(() => {
      if (typeof window.openChanges === 'function') window.openChanges(bundle);
    }, 180);
  }

  async function toggleAlias() {
    const on = !UTIL.alias;
    busy(true, on ? '계단현상 설정 적용 중…' : '계단현상 설정 복구 중…');
    const result = on
      ? await call('apply_nvidia_profile', 'stairfix', false)
      : await call('toggle_nvidia_alias', false);
    busy(false);
    if (!result || !result.ok) return fail(result, '계단현상 설정을 바꾸지 못했습니다');
    if (result.state) applyState(result.state);
    toast(result.message || (on ? '✨ 계단현상 끄기 적용' : '✨ 계단현상 설정 복구'), true);
  }

  async function restoreNvidia() {
    if (!UTIL.rifle && !UTIL.sniper && !UTIL.alias) {
      toast('복구할 적용 내역이 없습니다 · 지금은 기본값 상태', false);
      return;
    }
    busy(true, '기본값 복구 중…');
    let result = await call('restore_nvidia_profile');
    if (result && !result.ok && (result.can_elevate || result.needs_admin)) {
      const go = window.confirm(`${result.message}\n\n지금 관리자 권한으로 복구할까요?`);
      if (!go) { busy(false); return; }
      const help = await call('restore_nvidia_profile', null, true);
      if (!help || !help.ok) { busy(false); return fail(help, '관리자 권한 요청을 보내지 못했습니다'); }
      busy(true, '관리자 권한으로 복구하는 중… (UAC 승인 후 최대 30초)');
      result = await call('wait_elevated_result', 30);
    }
    busy(false);
    if (!result || !result.ok) return fail(result, '복구하지 못했습니다 · 관리자 권한이 필요할 수 있습니다');
    if (result.state) applyState(result.state);
    toast(result.message || '↩️ 기본값 복구했습니다', true);
  }

  async function toggleAccel() {
    const next = !UTIL.accel;
    busy(true, next ? '마우스 가속 켜는 중…' : '마우스 가속 끄는 중…');
    const result = await call('set_mouse_accel', next);
    busy(false);
    if (!result || !result.ok) return fail(result, '마우스 설정을 바꾸지 못했습니다');
    if (result.state) applyState(result.state);
    toast(result.message || (next ? '마우스 가속을 켰습니다' : '🖱️ 마우스 가속 끄기 완료'), true);
  }

  async function backupNow() {
    busy(true, '내 기록을 백업하는 중…');
    const result = await call('export_backup');
    busy(false);
    if (!result || !result.ok) return fail(result, '백업하지 못했습니다');
    if (result.state) applyState(result.state);
    toast(result.message || `💾 백업 완료 · ${result.path || ''}`, true);
  }

  async function restoreBackup() {
    busy(true, '백업 파일을 불러오는 중…');
    const result = await call('import_backup');
    busy(false);
    if (result && result.cancelled) return toast(result.message || '파일 선택이 취소되었습니다', false);
    if (!result || !result.ok) return fail(result, '기록을 불러오지 못했습니다');
    if (result.state) applyState(result.state);
    toast(result.message || '📂 기록 불러오기 완료', true);
  }

  /* ==================================================================
     클라우드
     ================================================================== */
  let cloudMode = 'signin';

  function openCloudModal(mode) {
    cloudMode = mode || 'signin';
    const mask = $('#cloud-mask');
    if (!mask) return;
    const email = $('#cloud-email');
    if (email && !email.value && AL.state?.cloud?.email) email.value = AL.state.cloud.email;
    setCloudMode(cloudMode);
    mask.classList.add('show');
  }

  function setCloudMode(mode) {
    cloudMode = mode;
    $$('#cloud-tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === mode));
    const submit = $('#cloud-submit');
    if (submit) submit.textContent = mode === 'signup' ? '회원가입' : '로그인';
    const note = $('#cloud-note');
    if (note) {
      note.innerHTML = mode === 'signup'
        ? '가입 후 인증 메일의 링크를 눌러야 로그인됩니다 · 이미 가입된 이메일이면 로그인 탭을 쓰세요.'
        : 'Supabase 프로젝트에 이메일 로그인이 켜져 있어야 합니다.';
    }
  }

  async function submitCloud() {
    const email = ($('#cloud-email') || {}).value || '';
    const pw = ($('#cloud-pw') || {}).value || '';
    if (!email.includes('@')) return toast('이메일을 확인해 주세요', false);
    if (pw.length < 6) return toast('비밀번호는 6자 이상이어야 합니다', false);
    const btn = $('#cloud-submit');
    if (btn) { btn.disabled = true; btn.textContent = '처리 중…'; }
    busy(true, cloudMode === 'signup' ? '회원가입 처리 중…' : '로그인 중…');
    const result = cloudMode === 'signup'
      ? await call('cloud_signup', email, pw)
      : await call('cloud_signin', email, pw);
    busy(false);
    if (btn) {
      btn.disabled = false;
      btn.textContent = cloudMode === 'signup' ? '회원가입' : '로그인';
    }
    if (!result || !result.ok) return fail(result, '로그인에 실패했습니다');
    if (result.state) applyState(result.state);
    if (result.needs_confirmation) {
      toast(result.message || '가입 확인 메일을 보냈습니다 · 메일 인증 후 로그인해 주세요', true);
      return;
    }
    const mask = $('#cloud-mask');
    if (mask) mask.classList.remove('show');
    toast(result.message || '☁️ 클라우드 로그인 완료', true);
  }

  async function cloudAction(kind) {
    if (kind === 'check') {
      const url = ($('#set-sb-url') || {}).value || '';
      const key = ($('#set-sb-key') || {}).value || '';
      busy(true, 'Supabase 연결 확인 중…');
      const result = await call('save_cloud_config', url.trim(), key.trim());
      busy(false);
      if (result && result.state) applyState(result.state);
      const probe = (result && result.probe) || {};
      toast(probe.message || (probe.ok ? 'Supabase 연결 확인됨' : '연결하지 못했습니다'), !!probe.ok);
      return;
    }
    if (kind === 'out') {
      const result = await call('cloud_signout');
      if (result && result.state) applyState(result.state);
      toast((result && result.message) || '로그아웃했습니다', true);
      return;
    }
    if (kind === 'push') {
      busy(true, '클라우드에 올리는 중…');
      const result = await call('cloud_push');
      busy(false);
      if (!result || !result.ok) return fail(result, '클라우드에 올리지 못했습니다');
      if (result.state) applyState(result.state);
      toast(result.message || '☁️ 클라우드에 기록을 올렸습니다', true);
      return;
    }
    if (kind === 'pull') {
      if (!window.confirm('클라우드 기록으로 덮어씁니다. 현재 상태는 백업 파일로 보관됩니다. 계속할까요?')) return;
      busy(true, '클라우드 기록을 내려받는 중…');
      const result = await call('cloud_pull');
      busy(false);
      if (!result || !result.ok) return fail(result, '내려받지 못했습니다');
      if (result.state) applyState(result.state);
      toast(result.message || '☁️ 클라우드 기록을 내려받았습니다', true);
    }
  }

  /* ==================================================================
     v1.2.0 — 인사이트(잔디·최근 폼) · 업데이트 · 트레이
     · 5분 워밍업 · 리포트 카드 · 1:1 비교 · 전원/게임 모드 · 플레이리스트
     ================================================================== */

  function renderUpdate(update) {
    if (update) AL.update = update;
    const data = AL.update || null;
    const bar = $('#update-banner');
    const ver = $('#update-banner-ver');
    const show = !!(data && data.ok && data.newer);
    if (bar) {
      bar.hidden = !show;
      if (show) {
        bar.title = `${data.message || '새 버전이 있습니다'} · 클릭하면 릴리스 페이지가 열립니다`;
        if (ver) ver.textContent = data.latest ? ('v' + String(data.latest).replace(/^v/, '')) : (data.name || '새 빌드');
      }
    }
    const info = $('#set-update-info');
    if (info) {
      if (!data) info.textContent = `현재 v${(AL.state && AL.state.meta && AL.state.meta.version) || '1.2.0'} · 업데이트를 확인해 보세요`;
      else if (data.ok && data.newer && data.latest) info.textContent = `새 버전 v${data.latest} 있음 · ${fmtLabel(data.published_at)} 공개`;
      else if (data.ok && data.newer) info.textContent = `새 빌드 있음 · ${data.name || 'Latest build'} (${String(data.build_at || '').slice(0, 16).replace('T', ' ')})`;
      else if (data.ok) info.textContent = `최신입니다 · v${data.current}`;
      else info.textContent = data.message || '업데이트를 확인하지 못했습니다';
      info.classList.toggle('on', !!(data && data.ok && !data.newer));
    }
  }

  async function checkUpdate(force, silent) {
    if (!AL.real) {
      if (!silent) toast('앱(.exe)에서 실행하면 새 버전을 확인합니다', false);
      return;
    }
    if (!silent) toast('🔎 새 버전을 확인하는 중…', true, 1600);
    const result = await call('check_update', force !== false);
    if (!result) return;
    if (result.pending) { if (!silent) toast(result.message || '확인 중입니다', true); return; }
    AL.update = result;
    renderUpdate(result);
    if (silent) {
      if (result.ok && result.newer) toast(`${result.message} · 상단 배너를 눌러 내려받으세요`, true, 6000);
      return result;
    }
    if (!result.ok) return toast(result.message || '업데이트를 확인하지 못했습니다', false);
    if (!result.newer) return toast(result.message || '최신 버전입니다', true);
    if (window.confirm(`${result.message}\n\n지금 릴리스 페이지를 열까요?`)) {
      call('open_external', result.url || result.download || '');
    } else {
      toast(result.message, true);
    }
  }

  function renderTray(trayState) {
    if (trayState) AL.tray = trayState;
    renderSettingsTray(AL.tray);
  }

  function renderSettingsTray(trayState) {
    const box = $('#set-tray-state');
    if (!box) return;
    const t = trayState || {};
    if (!t.supported) {
      box.textContent = '트레이 · 단축키는 윈도우에서만 동작합니다';
      return;
    }
    const bits = [];
    bits.push(t.icon ? '트레이 아이콘 켜짐' : '트레이 아이콘 꺼짐');
    bits.push(t.hotkey ? `${t.hotkey_spec} 단축키 켜짐` : '단축키 꺼짐');
    if (t.background) bits.push('닫으면 숨기기');
    box.textContent = bits.join(' · ') + (t.error ? ` · ${t.error}` : '');
  }

  /* ------------------------------------------------------------ 백그라운드 상태 갱신 */




  /* ------------------------------------------------------------ 5분 워밍업 */
  /* ---------- 오늘 훈련 시작 팝업 (바로 Steam 을 켜지 않는다) ---------- */





  /* ------------------------------------------------------------ 1:1 전적 비교 */

  /* v2.0.1 — showCompare 가 v1 삭제 때 함께 빠져 순위표 클릭이 오류로 끝났다 */
  function showCompare(cmp) {
    const mask = $('#compare-mask');
    const box = $('#cmp-result');
    if (!mask || !box) return;
    if (!cmp) { box.innerHTML = '<div class="mnote" style="display:block">비교할 값이 없습니다</div>'; }
    else {
      const rows = cmp.rows || [];
      const head = `<div class="duo-grid">
        <div class="duo-card"><div class="nm">${esc((cmp.me || {}).name)}</div>
          <div class="m">솔로 ${esc((cmp.me || {}).solo_tier || '—')} · 파티 ${esc((cmp.me || {}).party_tier || '—')}</div></div>
        <div class="duo-card"><div class="nm">${esc((cmp.friend || {}).name)}</div>
          <div class="m">솔로 ${esc((cmp.friend || {}).solo_tier || '—')} · 파티 ${esc((cmp.friend || {}).party_tier || '—')}</div></div>
      </div>`;
      const body = rows.map((r) => {
        const up = r.better === 'me';
        const tie = r.better === 'none';
        const a = r.text ? esc(r.me) : `${esc(r.me == null ? '—' : r.me)}${esc(r.unit || '')}`;
        const b = r.text ? esc(r.friend) : `${esc(r.friend == null ? '—' : r.friend)}${esc(r.unit || '')}`;
        return `<div class="duo-pairs"><div class="row">
          <span class="${up ? 'up' : ''}">${a}</span>
          <span class="map">${esc(r.label)}</span>
          <b class="${tie ? '' : up ? 'down' : 'up'}">${b}</b>
        </div></div>`;
      }).join('');
      box.innerHTML = `${head}<div class="duo-verdict ${(cmp.score || {}).me >= (cmp.score || {}).friend ? 'good' : 'mid'}">
          <b>${esc(cmp.verdict || '')}</b><span>${n0((cmp.score || {}).me)} : ${n0((cmp.score || {}).friend)}</span></div>
        ${body}
        <div class="mnote" style="display:block">${esc(cmp.note || '')}</div>`;
    }
    mask.classList.add('show');
  }

  async function compareFriend(name) {
    if (!AL.real) return toast('앱(.exe)에서 1:1 비교를 볼 수 있습니다', false);
    const result = await call('compare_friend', name);
    if (!result || !result.ok) return fail(result, '비교할 전적이 없습니다');
    showCompare(result.compare);
  }


  AL.compareFriend = compareFriend;

  /* ------------------------------------------------------------ 주간 목표 수정 */


  /* ------------------------------------------------------------ 전원 · 게임 모드 */
  function renderPerf(perf) {
    if (!perf) return;
    AL.perf = perf;
    const gmOn = !!(perf.game_mode && perf.game_mode.enabled === true);
    /* v1.3.1 — 스위치는 '지금 적용해 둔 상태'로 판단한다.
       powercfg /duplicatescheme 으로 새 GUID 가 생기면 perf.optimal 이 false 로
       남아, 적용했는데도 스위치가 왼쪽에 머무르던 버그를 없앤다. */
    const optimal = !!(perf.active || perf.applied || perf.optimal);
    UTIL_PERF.on = optimal;
    const sw = $('#perf-switch');
    if (sw) {
      sw.classList.toggle('on', optimal);
      sw.setAttribute('aria-checked', optimal ? 'true' : 'false');
    }
    const detail = $('#perf-detail');
    if (detail) {
      const power = perf.power ? (perf.power.name || perf.power.guid) : (perf.label || '—');
      detail.textContent = `전원 옵션 ${power} · 게임 모드 ${gmOn ? '켜짐' : '꺼짐'}`;
    }
    const chip = $('#perf-chip');
    if (chip) {
      chip.textContent = !perf.supported ? '윈도우 전용' : (optimal ? '적용됨' : '대기');
      chip.className = 'ustate' + (optimal ? ' on' : (perf.supported ? ' warn' : ''));
    }
    const stateEl = $('#perf-state');
    if (stateEl) {
      const gm = perf.game_mode || {};
      const gmTxt = gm.enabled === true ? '켜짐' : (gm.enabled === false ? '꺼짐' : '기본값');
      stateEl.textContent = `게임 모드 ${gmTxt} · ${optimal ? '적용됨' : '적용 전'}`;
      stateEl.className = '';
    }
    const label = $('#perf-label');
    if (label) {
      const power = perf.power ? (perf.power.name || perf.power.guid) : (perf.label || '—');
      label.textContent = `전원 옵션 · ${power}`;
    }
  }

  async function perfRefresh(force) {
    if (!AL.real) return;
    const result = await call('perf_state', !!force);
    if (result && result.ok) renderPerf(result.perf);
  }

  async function togglePerfSwitch() {
    if (!AL.real) {
      UTIL_PERF.on = !UTIL_PERF.on;
      if (typeof renderPerfSwitch === 'function') renderPerfSwitch();
      return toast(UTIL_PERF.on ? '⚡ 고성능 전원 + 게임 모드 적용' : '↩️ 원래 전원 옵션으로 복구', true);
    }
    const on = !(AL.perf && (AL.perf.active || AL.perf.applied || AL.perf.optimal));
    if (on) return perfApply();
    return perfRestore();
  }

  async function perfApply() {
    if (!AL.real) return toast('앱(.exe)에서 적용할 수 있습니다', false);
    busy(true, '전원 옵션 · 게임 모드를 적용하는 중…');
    const result = await call('apply_perf_mode');
    busy(false);
    if (!result) return;
    if (result.perf) renderPerf(result.perf);
    toast(result.message || (result.ok ? '⚡ 적용 완료' : '적용하지 못했습니다'), !!result.ok, 5200);
  }

  async function perfRestore() {
    if (!AL.real) return toast('앱(.exe)에서 복구할 수 있습니다', false);
    busy(true, '원래 전원 옵션으로 복구하는 중…');
    const result = await call('restore_perf_mode');
    busy(false);
    if (!result) return;
    if (result.perf) renderPerf(result.perf);
    toast(result.message || (result.ok ? '원래대로 복구했습니다' : '복구 지점이 없습니다'), !!result.ok, 5200);
  }

  /* ------------------------------------------------------------ 리포트 카드 PNG */
  function rr(g, x, y, w, h, r) {
    const rad = Math.max(0, Math.min(r, h / 2, w / 2));
    g.beginPath();
    g.moveTo(x + rad, y);
    g.arcTo(x + w, y, x + w, y + h, rad);
    g.arcTo(x + w, y + h, x, y + h, rad);
    g.arcTo(x, y + h, x, y, rad);
    g.arcTo(x, y, x + w, y, rad);
    g.closePath();
  }



  /* ------------------------------------------------------------ 트레이로 */
  async function trayHide() {
    if (!AL.real) return toast('앱(.exe)에서 트레이로 숨길 수 있습니다', false);
    const result = await call('tray_hide');
    if (!result || !result.ok) return fail(result, '트레이로 숨기지 못했습니다');
    toast(result.message || '트레이로 숨겼습니다', true, 5200);
  }

  /* 창을 다시 열면: 저장된 state 로 화면을 다시 그린다 */
  let lastAutoFill = 0;

  /* ==================================================================
     이벤트 연결
     ================================================================== */
  let bound = false;

  /* ==================================================================
     v1.4.0 — 스카우팅 · 듀오 시너지 · 맞춤 .plo · 온보딩 · 업데이트 · 연패 알림
     ================================================================== */
  const thCls = (v) => (Number(v) >= 75 ? 'hot' : Number(v) >= 55 ? 'mid' : 'low');



  function rolesApplied(rows) {
    const roles = AL.scoutRoles || {};
    return (rows || []).map((r) => {
      if (!r || !r.weapon) return r;
      const role = roles[r.name] || r.weapon.key;
      return { ...r, weapon: { ...r.weapon, key: role, label: role === 'sniper' ? '스나' : '라플', manual: !!roles[r.name] } };
    });
  }

  /* 화면에서 역할을 바꾸면 팀 스나수/전략을 즉시 다시 계산한다 (서버 규칙과 동일) */
  function localTeam(rows) {
    const players = (rows || []).filter((r) => r && !r.error);
    if (!players.length) return { threat: 0, label: '정보 없음', tips: [], snipers: [], sniper_count: 0, sniper_style: '' };
    const ranked = [...players].sort((a, b) => (b.threat || 0) - (a.threat || 0));
    const avg = players.reduce((a, r) => a + (r.threat || 0), 0) / players.length;
    const threat = Math.round(Math.min(100, avg * 0.65 + (ranked[0].threat || 0) * 0.35));
    const label = threat >= 75 ? '위협' : threat >= 55 ? '경계' : threat >= 35 ? '평범' : '무난';
    const snipers = players.filter((r) => (r.weapon || {}).key === 'sniper')
      .sort((a, b) => ((b.weapon || {}).sniper_kd || 0) - ((a.weapon || {}).sniper_kd || 0));
    const core = snipers.slice(0, 2);
    const count = snipers.length;
    let style = '';
    let styleText = '';
    const teamSize = players.length;
    if (count >= 4 || (count === 3 && teamSize >= 6)) {
      style = `${count}스나 과다`;
      styleText = `상대 스나 ${count}명 — 연막·플래시로 시야를 끊고 숏·중거리 러시로 붙어서 싸우면 유리합니다.`;
    } else if (count === 2) {
      style = teamSize >= 6 ? '정석 2스나' : '정석 1~2스나';
      styleText = '상대가 2스나 조합입니다. A롱·설대 긴 각만 피하고 2스나 각이 겹치는 구간을 먼저 끊으세요.';
    } else if (count === 1) {
      style = teamSize <= 5 ? '1스나 변칙' : '0~1스나 · 라플 위주';
      styleText = `상대 스나가 ${count}명뿐인 라플 위주 팀입니다. 긴 거리에서 견제하며 먼 각에서 먼저 한 명을 따면 유리합니다.`;
    } else {
      style = '스나 없음 · 라플 위주';
      styleText = '상대에 스나가 없습니다. 긴 각에서 먼저 한 명을 따고 천천히 밀어붙이면 유리합니다.';
    }
    const tips = [];
    tips.push(`가장 위협적인 상대는 ${ranked[0].name} (${ranked[0].threat_label} ${ranked[0].threat}) — 솔로 ${ranked[0].solo_tier || '—'} · 최근 폼 K/D ${ranked[0].kd == null ? '—' : ranked[0].kd}%.`);
    if (core.length) {
      tips.push(`핵심 스나수 — ${core.map((c) => `${c.name}(스나 K/D ${(c.weapon || {}).sniper_kd == null ? '—' : (c.weapon || {}).sniper_kd}%)`).join(', ')}. 이 둘의 각을 동시에 주지 말고 연막으로 순서를 나눠 끊으세요.`);
    }
    tips.push(styleText);
    const weak = players.map((r) => (r.weak_maps || [])[0]).find(Boolean);
    if (weak) tips.push(`상대들이 약한 맵 ${weak.map} 를 유도하면 승률이 올라갑니다.`);
    const sniperCount = count;
    const rifleCount = players.length - sniperCount;
    const teamN = players.length;
    const ideal = teamN <= 5 ? (sniperCount === 1 || sniperCount === 2) : sniperCount === 2;
    const balance = ideal ? 100 : sniperCount === 3 ? 74 : sniperCount === 0 ? 52
      : sniperCount === 4 ? 44 : 24;
    const balanceTxt = ideal ? (teamN <= 5 ? '정석 1~2스나' : '정석 2스나')
      : sniperCount === 3 ? '3스나 편중' : sniperCount === 0 ? '스나 없음'
      : sniperCount === 4 ? '4스나 편중' : `${sniperCount}스나 과다`;
    const top2 = ranked.slice(0, 2);
    const ace = Math.round(top2.reduce((a, r) => a + (r.threat || 0), 0) / Math.max(1, top2.length));
    const power = Math.round(players.reduce((a, r) => a + (r.threat || 0), 0) / players.length);
    const synScore = Math.round(balance * 0.45 + ace * 0.35 + power * 0.20);
    const synLabel = synScore >= 80 ? '완성형 조합' : synScore >= 65 ? '균형 잡힌 조합' : synScore >= 50 ? '무난한 조합' : '구멍 있는 조합';
    const synergy = { score: synScore, label: synLabel, balance, balance_txt: balanceTxt, ace,
      sniper_count: sniperCount, rifle_count: rifleCount,
      comment: `${balanceTxt} · 스나 ${sniperCount} / 라플 ${rifleCount} — 에이스 화력 ${ace}점` };
    /* 실전 전력 = 기본 티어력 + 조합 시너지 보정 */
    let adjust = 0;
    const notes = [];
    if (sniperCount === 2) { adjust += 6; notes.push('2스나 + 라플 보완 (+6)'); }
    else if (teamN <= 5 && sniperCount === 1 && rifleCount >= 3) { adjust += 5; notes.push('5:5 1스나 변칙 (+5)'); }
    else if (sniperCount > 2) { adjust -= 8 * (sniperCount - 2); notes.push(`스나 ${sniperCount}명 중복 (${-8 * (sniperCount - 2)})`); }
    else if (sniperCount === 0) { adjust -= 6; notes.push('스나 없음 (-6)'); }
    const styles = players.map((r) => (r.style || {}).key || '');
    const rushN = styles.filter((x) => x === 'rush').length;
    const stableN = styles.filter((x) => x === 'stable').length;
    const supportN = styles.filter((x) => x === 'support').length;
    const aceN = styles.filter((x) => x === 'ace').length;
    if (rushN >= 1 && (stableN >= 1 || supportN >= 1)) { adjust += 4; notes.push('돌격형 + 안정/지원형 상성 (+4)'); }
    if (stableN >= 3) { adjust -= 6; notes.push(`수비형 ${stableN}명 중복 (-6)`); }
    if (rushN >= 3) { adjust -= 4; notes.push(`돌격형 ${rushN}명 · 앵커 부재 (-4)`); }
    if (aceN >= 1) { adjust += 2; notes.push('에이스 캐리형 보유 (+2)'); }
    const byDmg = [...players].filter((r) => r.dmg).sort((a, b) => b.dmg - a.dmg)[0];
    const byWin = [...players].filter((r) => r.win != null).sort((a, b) => b.win - a.win)[0];
    if (byDmg && byWin) {
      if (byDmg.name !== byWin.name) { adjust += 4; notes.push('공격형+안정형 상호보완 (+4)'); }
      else { adjust += 1; notes.push('에이스 단일 의존 (+1)'); }
    }
    const namesSet = new Set(players.map((r) => r.name));
    let pairs = 0;
    let pairBonus = 0;
    players.forEach((r) => {
      Object.entries(r.team_mates || {}).forEach(([mate, stat]) => {
        if (mate === r.name || !namesSet.has(mate)) return;
        const g = Number((stat || {}).games || 0);
        if (g < 2) return;
        pairs += 1;
        const jointWin = Number((stat || {}).wins || 0) / g * 100;
        if (jointWin >= Number(r.win || 0) + 2) pairBonus += 5;
        else if (jointWin <= Number(r.win || 0) - 2) pairBonus -= 5;
      });
    });
    if (pairs) {
      pairBonus = Math.max(-10, Math.min(10, Math.round(pairBonus / Math.max(1, Math.floor(pairs / 2)))));
      adjust += pairBonus;
      notes.push(`합동 전적 케미 ${pairBonus >= 0 ? '+' : ''}${pairBonus}`);
    }
    const basePower = Math.round(avg);
    const force = { base: basePower, adjust, power: Math.max(0, Math.min(100, basePower + adjust)), notes, pairs };
    return { threat, label, tips, snipers: core, sniper_count: count, sniper_style: style, synergy, force };
  }

  function renderScoutSum(rows, team) {
    const sum = $('#scout-sum');
    if (!sum) return;
    if (!rows.length) { sum.hidden = true; sum.innerHTML = ''; return; }
    sum.hidden = false;
    const force = team.force || { base: 0, adjust: 0, power: 0, notes: [] };
    sum.innerHTML = `
      <div class="sc"><span>기본 티어력</span><b>${force.base || 0}</b></div>
      <div class="sc"><span>조합 시너지 보정</span><b style="color:${force.adjust >= 0 ? '#6EE7C0' : '#F7AFAE'}">${force.adjust > 0 ? '+' : ''}${force.adjust || 0}</b></div>
      <div class="sc hl" title="${(force.notes || []).join(' · ')}"><span>실전 전력</span><b>${force.power || 0} <i style="font-style:normal;font-size:9.5px;color:#FBD48F">${team.synergy ? team.synergy.label : ''}</i></b></div>
      <div class="sc" title="${team.synergy ? team.synergy.comment : ''}"><span>스나 구성</span><b style="font-size:12.5px">${team.sniper_style || '—'} <i style="font-style:normal;font-size:9.5px;color:#8FA1B8">(${team.sniper_count == null ? 0 : team.sniper_count}명)</i></b></div>`;
  }

  function scoutMapSummary(rows) {
    /* v1.5.0 (#12) — 이 상대들이 약한 맵 TOP · 강한 맵 TOP (판 수 가중 승률) */
    const table = new Map();
    rows.forEach((r) => {
      (r.maps || []).forEach((m) => {
        const name = String(m.map || '').trim();
        const g = Number(m.games || 0);
        if (!name || g <= 0) return;
        const slot = table.get(name) || { map: name, games: 0, win: 0 };
        slot.games += g;
        slot.win += Number(m.win || 0) * g;
        table.set(name, slot);
      });
    });
    const list = [...table.values()]
      .filter((s) => s.games >= 3)
      .map((s) => ({ map: s.map, games: s.games, win: s.win / s.games }));
    list.sort((a, b) => a.win - b.win);
    return { weak: list.slice(0, 3), strong: [...list].reverse().slice(0, 3) };
  }

  function renderScoutMaps(rows) {
    const box = $('#scout-maps');
    if (!box) return;
    if (!rows.length) { box.hidden = true; box.innerHTML = ''; return; }
    const { weak, strong } = scoutMapSummary(rows);
    const line = (m) => {
      const src = window.mapImg ? window.mapImg(m.map) : '';
      const thumb = src
        ? `<span class="mthumb"><img src="${src}" alt=""></span>`
        : '<span class="mthumb"><svg viewBox="0 0 24 24"><use href="#i-map"/></svg></span>';
      return `<div class="sm-row">${thumb}<span class="nm">${esc(m.map)}</span>
        <span class="pc">${m.win.toFixed(1)}%</span><span class="pc" style="color:#5C6577">${m.games}판</span></div>`;
    };
    box.hidden = false;
    box.innerHTML = `
      <div class="sm-col">
        <div class="sm-hd">이 상대가 약한 맵 <b>TOP 3</b></div>
        ${weak.length ? weak.map(line).join('') : '<div class="sm-empty">표본이 쌓이면 표시됩니다</div>'}
      </div>
      <div class="sm-col">
        <div class="sm-hd">이 상대가 강한 맵 <b>TOP 3</b></div>
        ${strong.length ? strong.map(line).join('') : '<div class="sm-empty">표본이 쌓이면 표시됩니다</div>'}
      </div>`;
  }

  /* v2.0.1 — 티어 이름 약어 (그랜드마스터 II → 그마 II) */
  const SHORT_TIER = {
    '언랭크': '언랭', '브론즈': '브론즈', '실버': '실버', '골드': '골드', '마스터': '마스터',
    '그랜드마스터': '그마', '레전드': '레전', '불레전드': '불레', '별레전드': '별레'
  };
  function shortTier(name) {
    const text = String(name == null ? '' : name).trim();
    if (!text) return '언랭크';
    const parts = text.split(' ');
    const head = SHORT_TIER[parts[0]] || parts[0];
    return parts.length > 1 ? head + ' ' + parts.slice(1).join(' ') : head;
  }

  /* 한 모드(솔로/파티) 랭크 칸 — 티어 · RP · 승률 · K/D */
  function scoutModeCell(stats, fallbackTier, fallbackRp) {
    const st = stats || {};
    const games = Number(st.games || 0);
    const rp = Number(st.rp != null ? st.rp : (fallbackRp || 0)) || 0;
    const tier = st.tier || fallbackTier || (rp > 0 ? '' : '언랭크');
    const win = st.win == null ? null : Number(st.win);
    const kd = st.kd == null ? null : Number(st.kd);
    const line1 = `${esc(tier || '언랭크')}${rp > 0 ? ` · <span style="color:#7E8FA6">${n0(rp)} RP</span>` : ''}`;
    const line2 = games
      ? `${win == null ? '승률 —' : `승률 ${n0(win, 1)}%`}${kd == null ? '' : ` · K/D ${n0(kd, 1)}%`}`
      : '최근 표본 없음';
    const tip = games ? `최근 ${games}판 기준` : '최근 랭크전 표본이 없습니다';
    return `<div class="v mc" title="${escAttr(tip)}"><b>${line1}</b><i>${line2}</i></div>`;
  }

  function renderScout(res) {
    const raw = (res && res.rows) || [];
    AL.scout = res || AL.scout || {};
    if (!AL.scoutRoles) AL.scoutRoles = {};
    const rows = rolesApplied(raw);
    const team = rows.length ? localTeam(rows) : ((res && res.team) || {});
    renderScoutSum(rows, team);
    renderScoutMaps(rows);
    const table = $('#scout-table');
    if (table) {
      if (!rows.length) {
        table.innerHTML = '<div class="empty">상대 닉네임을 입력하고 <b>조회</b>를 누르면 비교표가 나옵니다</div>';
      } else {
        const head = `<div class="scout-row hd"><span class="l">상대</span><span>솔로 랭크</span><span>파티 랭크</span><span>무기</span><span>위협</span></div>`;
        const coreNames = new Set((team.snipers || []).map((s) => s.name));
        const body = rows.map((r) => {
          if (r.error) {
            return `<div class="scout-row" style="border-color:rgba(239,68,68,.28)">
              <div class="nm">${esc(r.name)}<small style="color:#F7AFAE">${esc(r.error)}</small></div>
              <div class="v">—</div><div class="v">—</div><div class="v">—</div><div class="v">—</div></div>`;
          }
          const w = r.weapon || {};
          const core = coreNames.has(r.name);
          const styleDesc = (r.style && r.style.desc) || '';
          return `<div class="scout-row"${core ? ' style="border-color:rgba(56,189,248,.34);background:rgba(56,189,248,.06)"' : ''}>
            <div class="nm">
              <div style="display:flex;align-items:center;gap:4px;min-width:0">
                <b style="font-size:11.5px;font-weight:800;color:#EAF1FB;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.name)}</b>
                ${core ? '<span style="flex:0 0 auto;font-size:7.6px;color:#A9E5F8;font-weight:800;border:1px solid rgba(56,189,248,.32);border-radius:3px;padding:0 3px">핵심 스나</span>' : ''}
                ${r.style ? `<span style="flex:0 0 auto;font-size:7.6px;color:#FCD9A0;font-weight:800;background:rgba(245,158,11,.12);border:1px solid rgba(245,158,11,.24);border-radius:3px;padding:0 3px" title="${escAttr(styleDesc)}">${esc(r.style.emoji || '')} ${esc(r.style.label || '')}</span>` : ''}
              </div>
              <small style="display:block;margin-top:2px">${r.clan && r.clan !== '-' ? esc(r.clan) + ' 클랜 · ' : ''}최근 ${r.form && r.form.games ? n0(r.form.games) : n0(r.games)}판 폼${r.form && r.form.streak >= 2 ? ' · ' + n0(r.form.streak) + '연승' : ''}</small>
            </div>
            ${scoutModeCell(r.solo_stats, r.solo_tier, r.solo)}
            ${scoutModeCell(r.party_stats, r.party_tier, r.party)}
            <div class="v" style="text-align:right">
              <button class="btn-tiny" data-role="${escAttr(r.name)}" title="클릭해서 역할을 스나 ↔ 라플 로 바꿉니다" style="height:18px;font-size:8.5px;padding:0 5px">${esc(w.manual ? w.label : (w.estimate || w.label || '—'))}</button>
              <br><span style="font-size:8px;color:#7E8FA6;white-space:nowrap">스나 K/D ${w.sniper_kd == null ? '—' : n0(w.sniper_kd, 1)}% · 라플 K/D ${w.assault_kd == null ? '—' : n0(w.assault_kd, 1)}%</span>
            </div>
            <div style="text-align:right"><span class="th ${thCls(r.threat)}">${n0(r.threat)} · ${esc(r.threat_label || '')}</span></div>
          </div>`;
        }).join('');
        table.innerHTML = head + body +
          ((team.tips || []).length
            ? `<div class="scout-tips" style="margin-top:5px;padding:8px 10px;border-radius:8px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2)">
                 ${team.tips.map((t) => `<div class="ti">${esc(t)}</div>`).join('')}</div>`
            : '');
      }
    }
    const note = $('#scout-note');
    if (note) {
      note.innerHTML = res && res.calls !== undefined
        ? `이번 조회 API ${n0(res.calls)}건 · 이미 받은 전적은 캐시에서 사용 · <b>역할 버튼을 누르면 팀 스나수가 바로 다시 계산됩니다</b>`
        : '솔로/파티 랭크의 승률·K/D 는 경기 목록(최근 20판) 기준입니다 · <b>역할 버튼을 누르면 팀 스나수가 다시 계산됩니다</b>';
    }
  }

  function openDuo() {
    const mask = $('#duo-mask');
    if (mask) mask.classList.add('show');
    if (AL.real && !AL.duoRankLoaded) { AL.duoRankLoaded = true; runDuoRank(); }
    const input = $('#duo-nick');
    if (input && !input.value) {
      const friend = (MEMBERS || []).find((m) => !m.me && m.name);
      if (friend) input.value = friend.name;
    }
  }

  async function runDuo() {
    const input = $('#duo-nick');
    const names = String(input ? input.value : '').trim();
    if (!names) { toast('함께 뛰는 친구 닉네임을 입력해 주세요', false); return; }
    if (!AL.real) { renderDuo({ available: false, reason: '앱(.exe)에서 내 최근 매치 기록으로 분석합니다', friend: names }); return; }
    busy(true, '최근 매치에서 함께 뛴 판을 찾는 중…');
    const res = await call('duo_synergy', names);
    busy(false);
    AL.duo = res;
    renderDuo(res || {});
  }

  function renderDuo(d) {
    const box = $('#duo-body');
    if (!box) return;
    if (!d || !d.available) {
      box.innerHTML = `<div class="digest-advice" style="background:rgba(245,158,11,.07);border-color:rgba(245,158,11,.24);color:#FDE8BD">
          <span class="ic">🤝</span><span>${esc((d && (d.reason || d.label)) || '함께 뛴 판을 찾지 못했습니다')}</span></div>
        <div class="mnote">병영수첩의 <b>전적 불러오기</b>를 한 번 실행해 두면 최근 100판의 같은 팀 정보로 비교합니다.</div>`;
      return;
    }
    const t = d.together || {};
    const a = d.alone || {};
    const dl = d.delta || {};
    const fmt = (v, u) => (v === null || v === undefined ? '—' : (u === '%' ? Number(v).toFixed(1) + '%' : n0(v)));
    box.innerHTML = `
      <div class="duo-grid">
        <div class="duo-card together"><span>함께 뛴 판</span><b>${fmt(t.win, '%')}</b><i>승률 · ${t.games || 0}판</i></div>
        <div class="duo-card alone"><span>혼자 뛴 판</span><b>${fmt(a.win, '%')}</b><i>승률 · ${a.games || 0}판</i></div>
        <div class="duo-card"><span>차이</span><b style="color:${(dl.win || 0) >= 0 ? '#6EE7C0' : '#F7AFAE'}">${dl.win == null ? '—' : (dl.win > 0 ? '+' : '') + dl.win + '%p'}</b><i>K/D ${dl.kd == null ? '—' : (dl.kd > 0 ? '+' : '') + dl.kd + '%'} · 딜 ${dl.dmg == null ? '—' : (dl.dmg > 0 ? '+' : '') + n0(dl.dmg)}</i></div>
      </div>
      <div class="digest-advice" style="background:rgba(52,211,153,.07);border-color:rgba(52,211,153,.26);color:#CFF6E6">
        <span class="ic">${d.tone === 'warn' ? '⚠️' : '🤝'}</span><span><b>${esc(d.verdict || '')}</b> · ${esc(d.label || '')}</span>
      </div>
      ${d.force ? `<div style="display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:8px;background:rgba(148,163,184,.05);border:1px solid rgba(148,163,184,.12);font-size:10px;font-weight:700;color:#93A4BA">
        <span>기본 전력 <b class="num" style="color:#C7D3E4">${d.force.base}</b></span>
        <span style="color:#4E5C72">+</span>
        <span>케미 보정 <b class="num" style="color:${d.force.adjust >= 0 ? '#6EE7C0' : '#F7AFAE'}">${d.force.adjust > 0 ? '+' : ''}${d.force.adjust}</b></span>
        <span style="color:#4E5C72">=</span>
        <span>실전 전력 <b class="num" style="color:#FDE8BD;font-size:12px">${d.force.power}</b></span>
        <span style="margin-left:auto;font-size:9px;color:#7E8FA6">${(d.force.notes || []).join(' · ')}</span>
      </div>` : ''}
      <div class="mnote">${esc(d.note || '')}</div>`;
  }

  function renderUtilRecent(st) {
    const opp = $('#util-opp');
    const friends = $('#util-friends');
    const meta = $('#util-recent-meta');
    if (!opp || !friends) return;
    const barracks = ((st || AL.state || {}).barracks) || {};
    const matches = (barracks.matches || []).slice();
    /* v1.0.1 (#14) — 최신 판이 맨 앞에 오도록 날짜·시간으로 정렬 (캐시 순서에 기대지 않는다) */
    const stamp = (m) => `${m.date || ''} ${m.time || ''}`;
    matches.sort((a, b) => String(stamp(b)).localeCompare(String(stamp(a))));
    const first = matches[0] || null;
    const me = String(((st || AL.state || {}).nexon || {}).nickname ||
      (((st || AL.state || {}).settings || {}).nickname) || '').trim().toLowerCase();
    /* v1.0.1 (#14) — 플레이어 항목의 2번째 값이 '팀'/'적' 이다. 상대만 고른다(아군이 섞이던 버그) */
    const names = [];
    const detailed = ((first && first.players) || []).filter((p) => String((p && p[1]) || '') === '적');
    const rowsSource = detailed.length ? detailed : ((first && first.players) || []);
    rowsSource.forEach((p) => {
      const nm = String((p && p[0]) || '').trim();
      if (!nm || nm.toLowerCase() === me) return;
      if (detailed.length === 0 && String((p && p[1]) || '') === '팀') return;
      if (!names.includes(nm)) names.push(nm);
    });
    const opponents = names.slice(0, 6);
    if (meta) {
      const when = first ? `${String(first.date || '').replace(/-/g, '.')} ${first.time || ''}`.trim() : '';
      meta.textContent = first ? `${first.map || '최근 매치'} · ${first.result || ''} · ${when}` : '최근 매치 없음';
    }
    opp.innerHTML = opponents.length
      ? opponents.map((n) => `<button class="chip" data-opp="${escAttr(n)}" style="height:24px;font-size:9.5px;cursor:pointer" title="이 닉네임으로 스카우팅">${esc(n)}</button>`).join('')
        + `<button class="ubtn" id="util-opp-run" style="height:24px;padding:0 10px;font-size:9.5px">이 5명 분석</button>`
      : '<span style="font-size:10px;color:#6E7F95;font-weight:600">병영수첩을 불러오면 상대 5명이 자동으로 표시됩니다</span>';
    const table = {};
    matches.slice(0, 30).forEach((m) => {
      (m.players || []).forEach((p) => {
        const nm = String((p && p[0]) || '').trim();
        if (!nm || nm.toLowerCase() === me) return;
        table[nm] = table[nm] || { games: 0, wins: 0 };
        table[nm].games += 1;
        if (String(m.result) === '승') table[nm].wins += 1;
      });
    });
    const top = Object.entries(table).sort((a, b) => b[1].games - a[1].games).slice(0, 4);
    friends.innerHTML = top.length
      ? `<div style="font-size:9px;font-weight:800;color:#71829A;letter-spacing:.04em">최근 함께 뛴 친구</div>` + top.map(([nm, v]) =>
        `<div style="display:flex;align-items:center;gap:6px;font-size:10px;color:#C7D3E4"><span style="font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${nm}</span>
          <span style="margin-left:auto;font-family:var(--num);font-size:9px;color:#8FA1B8">${v.games}판 · ${Math.round(v.wins / v.games * 100)}%</span></div>`).join('')
      : '';
    const run = $('#util-opp-run');
    if (run) run.addEventListener('click', () => { AL.pendingScout = opponents; runScoutAuto(); });
    opp.querySelectorAll('[data-opp]').forEach((b) => b.addEventListener('click', () => {
      const input = $('#scout-nicks');
      if (input) input.value = b.dataset.opp;
      openScout();
    }));
  }

  /* ---------- v1.5.0 · 파티랭크 5인 맵 밴·픽 ---------- */
  function partyRow(row, kind, i) {
    const src = row.image || '';
    const thumb = src
      ? `<span class="mthumb"><img src="${src}" alt=""></span>`
      : `<span class="mthumb"><svg viewBox="0 0 24 24"><use href="#i-map"/></svg></span>`;
    const pct = Number(row.expected || 0);
    const bg = (typeof mapImg === 'function') ? mapImg(row.map) : '';
    return `<div class="party-row ${kind}">
      ${bg ? `<span class="party-bg" style="background-image:url('${bg}')"></span>` : ''}
      <span class="pk">${i + 1}</span>${thumb}
      <span class="nm">${esc(row.map)}</span>
      <span class="gm">${row.games}판</span>
      <span class="ex">${pct.toFixed(1)}%</span>
    </div>`;
  }

  function renderPartyMap(plan) {
    const picks = $('#party-picks'), bans = $('#party-bans'), meta = $('#party-meta');
    if (!picks || !bans) return;
    const p = plan || {};
    picks.innerHTML = (p.picks || []).length
      ? p.picks.slice(0, 3).map((r, i) => partyRow(r, 'pick', i)).join('')
      : '<div class="party-empty">파티원을 불러오면 추천 픽이 나옵니다</div>';
    bans.innerHTML = (p.bans || []).length
      ? p.bans.slice(0, 3).map((r, i) => partyRow(r, 'ban', i)).join('')
      : '<div class="party-empty">밴 추천 대기</div>';
    if (meta) {
      const n = (p.member_count || (p.members || []).length || 0);
      meta.textContent = n ? `파티 ${n}명 · ${p.sample_games || 0}판` : '파티원 0명';
    }
  }

  async function loadPartyMembers() {
    const input = $('#party-names');
    if (!input) return;
    const st = AL.state || {};
    const me = (st.settings && st.settings.nickname) || '';
    const mates = ((st.leaderboard) || []).filter((m) => !m.me).map((m) => m.name);
    const names = [me, ...mates].filter(Boolean).slice(0, 5);
    if (!names.length) { toast('순위표에 멤버가 없습니다 · 닉네임을 직접 입력해 주세요', false); return; }
    input.value = names.join(', ');
    toast(`파티원 ${names.length}명을 채웠습니다 · 분석을 누르면 전적을 모읍니다`, true);
  }

  async function runPartyMap(silent) {
    if (!AL.real) { toast('앱(.exe)에서 파티 전적을 불러옵니다', false); return; }
    const input = $('#party-names');
    const names = String((input && input.value) || '').trim();
    if (!names) { toast('파티원 닉네임을 입력해 주세요', false); return; }
    busy(true, '파티원 맵 전적을 모으는 중…');
    const res = await call('party_map_plan', names || null, false);
    busy(false);
    if (!res || res.ok === false) {
      /* v1.0.1 (#11) — 자동 입력 직후에는 실패 토스트를 띄우지 않는다(오버레이가 이미 결과를 보여 줌) */
      if (!silent) { fail(res, '맵 밴·픽을 계산하지 못했습니다'); renderPartyMap(res || null); }
      return;
    }
    AL.partyMap = res;
    renderPartyMap(res);
    if (!silent) toast(res.message || '파티 맵 밴·픽을 계산했습니다', true, 5000);
  }

  async function runScoutAuto() {
    if (!AL.real) { toast('앱(.exe)에서 방금 끝난 판 상대를 불러옵니다', false); return; }
    busy(true, '방금 끝난 판 상대를 불러오는 중…');
    const res = await call('scout_last_match');
    busy(false);
    if (!res || res.ok === false) { fail(res, '상대를 불러오지 못했습니다'); return; }
    AL.scout = res;
    AL.scoutRoles = {};
    const input = $('#scout-nicks');
    if (input && (res.opponents || []).length) input.value = res.opponents.join(', ');
    const mask = $('#scout-mask');
    if (mask) mask.classList.add('show');
    renderScout(res);
    toast(res.message || '방금 끝난 판 상대를 분석했습니다', true, 5000);
  }

  /* ---------- 내전 팀 자동 짜기 ---------- */
  let balanceShown = false;

  function renderBalanceCandidates() {
    const box = $('#balance-members');
    if (!box) return;
    const members = (AL.balanceCands && AL.balanceCands.members) || [];
    const mates = (AL.balanceCands && AL.balanceCands.coplayers) || [];
    const picked = new Set((AL.balancePicked || []).map((n) => n.toLowerCase()));
    const row = (c) => {
      const role = (AL.balanceRoles || {})[c.name] || (c.weapon === 'rifle' ? 'rifle' : 'sniper');
      const on = picked.has(String(c.name).toLowerCase());
      return `<label style="display:flex;align-items:center;gap:6px;padding:5px 8px;border-radius:7px;background:rgba(255,255,255,.025);border:1px solid ${on ? 'var(--ac22)' : 'var(--line)'};cursor:pointer">
        <input type="checkbox" data-bal-name="${esc(c.name)}" ${on ? 'checked' : ''} style="accent-color:var(--ac)">
        <span style="font-size:11px;font-weight:700;color:#EAF1FB;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(c.name)}${c.me ? ' <small style="color:var(--txt2)">ME</small>' : ''}</span>
        <button type="button" data-bal-role="${esc(c.name)}" title="클릭해서 스나 ↔ 라플" style="height:17px;font-size:8.5px;font-weight:800;padding:0 5px;border-radius:4px;border:1px solid var(--line2);background:${role === 'sniper' ? 'var(--ac10)' : 'rgba(255,255,255,.06)'};color:${role === 'sniper' ? 'var(--ac)' : 'var(--txt2)'}">${role === 'sniper' ? '스나' : '라플'}</button>
        <span style="margin-left:auto;font-family:var(--num);font-size:9px;color:#8FA1B8;white-space:nowrap">${c.source === 'match' ? `동반 ${c.games}판` : (c.solo ? n0(c.solo) + ' RP' : '언랭크')}</span>
        <button type="button" data-bal-remove="${esc(c.name)}" title="목록에서 빼기" style="flex:0 0 auto;width:15px;height:15px;border:0;background:none;color:#5C6577;font-size:10px;cursor:pointer;padding:0">✕</button>
      </label>`;
    };
    const head = (t) => `<div style="grid-column:1/-1;font-size:8.5px;font-weight:800;color:#71829A;letter-spacing:.05em;margin:2px 0 -1px">${t}</div>`;
    box.innerHTML = (members.length ? head('순위표 멤버') + members.map(row).join('') : '')
      + (mates.length ? head('최근 매치 동반 플레이어 (캐시)') + mates.map(row).join('') : '');
    if (!members.length && !mates.length) {
      box.innerHTML = '<div class="mnote">후보가 없습니다 · 병영수첩을 최신화하거나 순위표에 친구를 등록해 주세요</div>';
    }
  }

  function balanceNames() {
    return $$('#balance-members input[data-bal-name]:checked').map((el) => el.dataset.balName);
  }


  function renderBalance(res) {
    const box = $('#balance-result');
    if (!box) return;
    if (!res || res.available === false) {
      box.innerHTML = `<div class="digest-advice" style="background:rgba(245,158,11,.08);border-color:rgba(245,158,11,.28);color:#FDE8BD"><span class="ic">⚖️</span><span>${(res && (res.reason || res.message)) || '멤버를 2명 이상 선택해 주세요'}</span></div>`;
      return;
    }
    const team = (t, label, cls) => `
      <div style="border-radius:10px;padding:9px 11px;background:rgba(7,11,19,.5);border:1px solid ${cls}">
        <div style="display:flex;align-items:center;gap:7px;margin-bottom:6px">
          <b style="font-size:12px;font-weight:900;color:#EAF1FB">${label}</b>
          <span class="num" style="font-size:10px;color:#8FA1B8">평균 ${t.power} · 스나 ${t.snipers}명</span>
        </div>
        ${(t.rows || []).map((r) => `<div style="display:flex;align-items:center;gap:6px;font-size:10px;color:#C7D3E4;padding:2px 0">
          <span style="color:${r.role === 'sniper' ? '#FCD9A0' : '#A9E5F8'};font-weight:800">${r.role === 'sniper' ? '스나' : '라플'}</span>
          <span style="font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.name)}</span>
          <span style="font-size:9px;color:#7E8FA6">${esc(r.style_emoji || '')}${esc(r.style || '')}</span>
          <span class="num" style="margin-left:auto;font-size:9.5px;color:#FDE8BD">${r.power}</span>
        </div>`).join('')}
      </div>`;
    box.innerHTML = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:7px">
        ${team(res.a, 'A팀', 'rgba(245,158,11,.34)')}
        ${team(res.b, 'B팀', 'rgba(56,189,248,.34)')}
      </div>
      <div class="digest-advice" style="background:rgba(52,211,153,.07);border-color:rgba(52,211,153,.26);color:#CFF6E6">
        <span class="ic">⚖️</span><span><b>${esc(res.verdict)}</b> · ${esc(res.note)}</span>
      </div>`;
    const note = $('#balance-note');
    if (note) note.textContent = `${res.size} : ${res.size} 기준 · 후보 ${res.pool}명 — 체크를 바꾸면 다시 계산합니다`;
  }

  async function runDuoRank() {
    if (!AL.real) { toast('앱(.exe)에서 순위표 멤버로 분석합니다', false); return; }
    const box = $('#duo-rank');
    if (box) box.innerHTML = '<div class="mnote">최근 매치에서 함께 뛴 판을 세는 중…</div>';
    const res = await call('duo_rank', []);
    renderDuoRank(res || {});
  }

  function renderDuoRank(res) {
    const box = $('#duo-rank');
    if (!box) return;
    const rows = (res && res.rows) || [];
    if (!rows.length) {
      box.innerHTML = `<div class="mnote">${esc((res && (res.note || res.message)) || '함께 뛴 판이 2판 이상인 친구가 없습니다 — 병영수첩을 최신화해 주세요')}</div>`;
      return;
    }
    const medal = ['🥇', '🥈', '🥉'];
    box.innerHTML = rows.map((r, i) => `
      <div style="display:grid;grid-template-columns:26px minmax(0,1fr) 58px 72px 70px;gap:7px;align-items:center;padding:6px 8px;border-radius:7px;background:rgba(7,11,19,.5);border:1px solid ${i === 0 ? 'rgba(245,158,11,.34)' : 'rgba(148,163,184,.12)'}">
        <div style="font-size:12px;text-align:center">${medal[i] || (i + 1)}</div>
        <div style="min-width:0"><div style="font-size:11px;font-weight:800;color:#EAF1FB;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.friend)}</div>
          <div style="font-size:8.5px;color:#7E8FA6;font-weight:600">함께 ${r.games}판 · 혼자 ${r.alone_games}판</div></div>
        <div class="num" style="font-size:11px;text-align:right;color:${(r.win_delta || 0) >= 0 ? '#6EE7C0' : '#F7AFAE'}">${r.win_delta == null ? '—' : (r.win_delta > 0 ? '+' : '') + r.win_delta + '%p'}</div>
        <div class="num" style="font-size:10px;text-align:right;color:#C7D3E4">K/D ${r.kd_delta == null ? '—' : (r.kd_delta > 0 ? '+' : '') + r.kd_delta}%</div>
        <div style="text-align:right"><span class="num" style="font-size:11px;font-weight:800;color:#FDE8BD">${r.score}</span>
          <div style="font-size:8px;color:#8FA1B8;font-weight:700">${esc(r.verdict || '')}</div></div>
      </div>`).join('');
  }




  /* ---------- 온보딩 ---------- */
  let onbShown = false;
  let onbStep = 0;
  const onbPick = { weapon: 'sniper', game: 'kovak' };

  function maybeShowOnboarding(st) {
    if (!AL.real || onbShown) return;
    const ob = (st && st.onboarding) || {};
    if (!ob.needed) return;
    onbShown = true;
    onbPick.weapon = ob.weapon || (weapon || 'sniper');
    onbPick.game = ob.game || (game || 'kovak');
    onbStep = 0;
    const nick = $('#onb-nick');
    if (nick) nick.value = (st.nexon || {}).nickname || '';
    paintOnb();
    const mask = $('#onb-mask');
    if (mask) mask.classList.add('show');
  }

  function paintOnb() {
    const s0 = $('#onb-step-0');
    const s1 = $('#onb-step-1');
    const s2 = $('#onb-step-2');
    if (s0) s0.hidden = onbStep !== 0;
    if (s1) s1.hidden = onbStep !== 1;
    if (s2) s2.hidden = onbStep !== 2;
    ['0', '1', '2'].forEach((i) => { const d = $('#onb-dot-' + i); if (d) d.classList.toggle('on', onbStep >= Number(i)); });
    const prev = $('#onb-prev');
    if (prev) prev.hidden = onbStep <= 1;
    const next = $('#onb-next');
    if (next) { next.hidden = onbStep === 0; next.textContent = onbStep === 1 ? '다음' : '시작하기'; }
    const skip = $('#onb-skip');
    if (skip) skip.textContent = onbStep === 0 ? '건너뛰고 로컬로 시작' : '건너뛰기';
    $$('#onb-weapons button').forEach((b) => b.classList.toggle('on', b.dataset.onbWeapon === onbPick.weapon));
    $$('#onb-games button').forEach((b) => b.classList.toggle('on', b.dataset.onbGame === onbPick.game));
  }

  async function onbCloud(signup) {
    if (!AL.real) { toast('앱(.exe)에서 로그인합니다', false); return; }
    const email = String(($('#onb-email') || {}).value || '').trim();
    const pw = String(($('#onb-pw') || {}).value || '');
    const note = $('#onb-cloud-note');
    if (!email || !pw) { toast('이메일과 비밀번호를 입력해 주세요', false); return; }
    if (note) note.textContent = signup ? '가입 중…' : '로그인 중…';
    const res = await call(signup ? 'cloud_signup' : 'cloud_signin', email, pw);
    if (!res || res.ok === false) {
      if (note) note.textContent = (res && res.message) || '로그인에 실패했습니다';
      fail(res, '로그인에 실패했습니다');
      return;
    }
    if (signup) {
      if (note) note.textContent = '가입되었습니다 · 이어서 닉네임과 API 키를 설정합니다';
      onbStep = 1;
      paintOnb();
      return;
    }
    busy(true, '클라우드 기록을 불러오는 중…');
    await call('cloud_pull');
    const done = await call('complete_onboarding', { nickname: '', api_key: '', weapon: onbPick.weapon, game: onbPick.game });
    busy(false);
    const mask = $('#onb-mask');
    if (mask) mask.classList.remove('show');
    if (done && done.state) applyState(done.state);
    toast('클라우드 기록을 불러왔습니다 · 환영합니다', true, 4200);
  }

  async function finishOnboarding(skip) {
    const mask = $('#onb-mask');
    if (mask) mask.classList.remove('show');
    if (!AL.real) { toast('앱(.exe)에서 저장합니다', true); return; }
    if (skip) {
      const res = await call('skip_onboarding');
      if (res && res.state) applyState(res.state);
      return;
    }
    const nick = ($('#onb-nick') || {}).value || '';
    const key = ($('#onb-key') || {}).value || '';
    const res = await call('complete_onboarding', {
      nickname: nick.trim(), api_key: key.trim(), weapon: onbPick.weapon, game: onbPick.game,
    });
    if (!res || res.ok === false) { fail(res, '설정을 저장하지 못했습니다'); return; }
    if (res.state) applyState(res.state);
    if (nick.trim()) ME = nick.trim();
    toast(res.message || '준비가 끝났습니다', true);
  }

  /* ---------- 구버전 차단 · 원클릭 업데이트 ---------- */
  let gateShown = false;
  function maybeShowUpdateGate(st) {
    const g = (st && st.update_gate) || {};
    AL.gate = g;
    const mask = $('#update-mask');
    if (!mask) return;
    if (!g.blocked) {
      if (gateShown && !mask.classList.contains('show')) return;
      return;
    }
    if (gateShown) return;
    gateShown = true;
    const title = $('#update-title');
    if (title) title.textContent = '새 버전으로 업데이트해 주세요';
    const sub = $('#update-sub');
    if (sub) sub.textContent = '이 버전은 더 이상 지원되지 않습니다 · 업데이트해야 계속 사용할 수 있습니다';
    const ver = $('#update-version');
    if (ver) ver.textContent = `v${g.current || ''} → ${g.latest ? 'v' + g.latest : 'new'}`;
    const close = $('#update-close');
    if (close) close.hidden = true;
    const later = $('#update-later');
    if (later) later.hidden = true;
    const hint = $('#update-hint');
    if (hint) hint.innerHTML = '내려받기 → 앱 종료 → 파일 교체 → 자동 재시작까지 한 번에 진행됩니다.<br><b style="color:#F7AFAE">구버전은 계속 사용할 수 없습니다.</b>';
    mask.classList.add('show');
  }

  async function runUpdate() {
    if (!AL.real) { toast('앱(.exe)에서 새 버전을 내려받습니다', false); return; }
    const btn = $('#update-go');
    if (btn) { btn.disabled = true; btn.textContent = '내려받는 중…'; }
    const res = await call('install_update');
    if (btn) { btn.disabled = false; btn.textContent = '지금 업데이트'; }
    if (!res || res.ok === false) { fail(res, '업데이트에 실패했습니다'); return; }
    const hint = $('#update-hint');
    if (hint) hint.innerHTML = `<b style="color:#B7F5DA">${res.message || '새 버전을 받았습니다'}</b><br>[프로그램 종료]를 누르면 교체 후 자동으로 다시 시작됩니다.`;
    const close = $('#update-close');
    if (close) close.hidden = false;
    const later = $('#update-later');
    if (later) { later.hidden = false; later.textContent = '프로그램 종료'; }
  }

  /* ---------- 연패 브레이크 알림 ---------- */
  let tiltTimer = null;
  let tiltShown = null;
  function startTiltPoll() {
    if (tiltTimer || !AL.real) return;
    tiltTimer = setInterval(async () => {
      if (!AL.real) return;
      const res = await call('tilt_state');
      if (!res || !res.tilt) return;
      AL.tilt = res.tilt;
      const alerts = res.tilt.alerts || [];
      const last = alerts[alerts.length - 1];
      if (!last || !last.id || tiltShown === last.id) return;
      tiltShown = last.id;
      showTilt(last);
    }, 60000);
  }

  function showTilt(a) {
    const mask = $('#tilt-mask');
    if (!mask) return;
    const danger = a.tone === 'danger';
    const hot = a.tone === 'hot';
    const title = $('#tilt-title');
    if (title) title.textContent = danger ? '🚨 위험 · 5연패 이상' : hot ? '🔥 연승 상승세' : '🧯 연패 브레이크 · 주의';
    const sub = $('#tilt-sub');
    if (sub) sub.textContent = danger ? '지금은 랭크를 멈추고 쉬어야 합니다'
      : hot ? '지금 폼이 좋습니다 · 감도·무기는 그대로' : '잠깐 숨 고르고 다시 들어가세요';
    const box = mask.querySelector('.digest-advice');
    if (box) {
      box.style.background = danger ? 'rgba(239,68,68,.10)' : hot ? 'rgba(52,211,153,.08)' : 'rgba(245,158,11,.08)';
      box.style.borderColor = danger ? 'rgba(239,68,68,.42)' : hot ? 'rgba(52,211,153,.34)' : 'rgba(245,158,11,.30)';
      box.style.color = danger ? '#FFD7D5' : hot ? '#CFF6E6' : '#FDE8BD';
    }
    const text = $('#tilt-text');
    if (text) text.innerHTML = `<b>${a.title || ''}</b><br>${a.message || ''}`;
    mask.classList.add('show');
    toast(`${danger ? '🚨' : hot ? '🔥' : '🧯'} ${a.title || '연패 알림'}`, !(danger), 6000);
    if (AL.real) call('ack_tilt', a.id || '').catch(() => {});
  }

  function openScout() {
    const mask = $('#scout-mask');
    if (mask) mask.classList.add('show');
    const input = $('#scout-nicks');
    if (input && !input.value) {
      const names = (MEMBERS || []).filter((m) => !m.me && m.name).slice(0, 3).map((m) => m.name);
      if (names.length) input.value = names.join(', ');
    }
  }

  async function runScout() {
    const input = $('#scout-nicks');
    const raw = input ? input.value : '';
    const names = raw.split(/[,，\n\t ]+/).map((s) => s.trim()).filter(Boolean).slice(0, 12);
    if (!names.length) { toast('상대 닉네임을 1명 이상 입력해 주세요', false); return; }
    if (!AL.real) { toast('앱(.exe)에서 상대 전적을 조회합니다', false); return; }
    AL.scoutRoles = {};
    busy(true, '상대 전적을 확인하는 중…');
    const res = await call('scout_team', names.slice(0, 6));
    busy(false);
    if (!res || res.ok === false && !(res.rows || []).length) { fail(res, '스카우팅에 실패했습니다'); renderScout(res || {}); return; }
    AL.scout = res;
    renderScout(res);
    if (res.message) toast(res.message, res.ok !== false);
  }

  async function runScoutAuto() {
    if (!AL.real) { toast('앱(.exe)에서 방금 끝난 판 상대를 불러옵니다', false); return; }
    busy(true, '방금 끝난 판 상대를 불러오는 중…');
    const res = await call('scout_last_match');
    busy(false);
    if (!res || res.ok === false) { fail(res, '상대를 불러오지 못했습니다'); return; }
    AL.scout = res;
    AL.scoutRoles = {};
    const input = $('#scout-nicks');
    if (input && (res.opponents || []).length) input.value = res.opponents.join(', ');
    const mask = $('#scout-mask');
    if (mask) mask.classList.add('show');
    renderScout(res);
    toast(res.message || '방금 끝난 판 상대를 분석했습니다', true, 5000);
  }

  async function openBalance() {
    const mask = $('#balance-mask');
    if (mask) mask.classList.add('show');
    if (!AL.real || balanceShown) return;
    balanceShown = true;
    const box = $('#balance-members');
    if (box) box.innerHTML = '<div class="mnote">순위표 · 최근 동반 플레이어를 불러오는 중…</div>';
    const res = await call('balance_candidates');
    AL.balanceCands = res || {};
    renderBalanceCandidates();
  }

  async function runBalance() {
    if (!AL.real) { toast('앱(.exe)에서 팀을 나눕니다', false); return; }
    const names = balanceNames();
    if (!names.length) { toast('내전 인원을 체크해 주세요', false); return; }
    if (names.length < 2) { toast('내전 인원을 2명 이상 체크해 주세요', false); return; }
    const box = $('#balance-result');
    if (box) box.innerHTML = '<div class="mnote">균형을 계산하는 중…</div>';
    const roles = {};
    names.forEach((n) => { if ((AL.balanceRoles || {})[n]) roles[n] = AL.balanceRoles[n]; });
    const res = await call('balance_teams', names, 0, roles);
    AL.balanceResult = res || {};
    renderBalance(res || {});
  }

  function bind() {
    if (bound) return;
    bound = true;

    /* ---------- 창 이동 (v1.3.1 · Win32 커서 추적 + JS 폴백) ----------
       frameless 창에서 pywebview JS API 는 백그라운드 스레드에서 돌기 때문에
       ReleaseCapture()+WM_NCLBUTTONDOWN 방식이 실패할 수 있다. 그래서
       ① 타이틀바에 pywebview-drag-region 클래스를 항상 붙여 두고(공식 경로),
       ② mousedown 시 start_window_drag() 로 Win32 커서 추적 이동을 시작한다.
          (SetWindowPos SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE — 크기·Z순서 불변)
       ③ Win32 이동을 못 쓰는 환경에서는 move_window(dx,dy) 로 직접 따라간다.
       최대화는 없앴다 — 더블클릭해도 아무 일도 하지 않는다 (1200×750 고정). */
    const titlebar = $('#titlebar');
    if (titlebar) {
      titlebar.classList.add('pywebview-drag-region');
      let dragMode = 'both';
      let jsDrag = null;
      titlebar.addEventListener('mousedown', async (e) => {
        if (!AL.real || e.button !== 0) return;
        if (e.target.closest('.winbtns, button, input, a')) return;
        e.preventDefault();
        if (dragMode === 'js') {
          jsDrag = { x: e.screenX, y: e.screenY };
          document.body.classList.add('is-dragging');
          return;
        }
        /* 두 경로를 함께 켠다 — 둘 다 '시작 지점 + 커서 이동량'으로 같은 위치를
           계산하므로 서로 부딪히지 않고, 한쪽이 막혀도 창은 반드시 따라온다.
             ① Win32 커서 추적 루프 (start_window_drag)
             ② JS mousemove → move_window(dx, dy) */
        jsDrag = { x: e.screenX, y: e.screenY };
        document.body.classList.add('is-dragging');
        const result = await call('start_window_drag');
        if (result && result.supported === false) dragMode = 'js';
      });
      window.addEventListener('mousemove', (e) => {
        if (!jsDrag) return;
        call('move_window', e.screenX - jsDrag.x, e.screenY - jsDrag.y);
      });
      window.addEventListener('mouseup', () => {
        if (!jsDrag) return;
        jsDrag = null;
        document.body.classList.remove('is-dragging');
        call('end_window_drag');
      });
    }

    /* ---------- v2.0.0 · 회원 로그인 · 듀오 시너지 ---------- */
    const authTabs = $('#auth-tabs');
    if (authTabs) authTabs.addEventListener('click', (e) => {
      const b = e.target.closest('[data-auth-tab]');
      if (b) setAuthMode(b.dataset.authTab);
    });
    const authSubmit = $('#auth-submit');
    if (authSubmit) authSubmit.addEventListener('click', submitAuth);
    ['#auth-email', '#auth-password'].forEach((sel) => {
      const el = $(sel);
      if (el) el.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submitAuth(); } });
    });
    const cmpRun = $('#cmp-run');
    if (cmpRun) cmpRun.addEventListener('click', runDuoBetween);
    ['#cmp-left', '#cmp-right'].forEach((sel) => {
      const el = $(sel);
      if (el) el.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); runDuoBetween(); } });
    });

    /* ---------- v2.0.1 · 왼쪽 레일 · 설정 · 닫기 팝업 (누락된 리스너 복구) ---------- */
    const railHome = $('#rail-home');
    if (railHome) {
      const goHome = () => {
        if (typeof window.showScreen === 'function') window.showScreen('home');
        else $$('.screen').forEach((s) => { s.hidden = s.id !== 's-home'; });
        if (AL.onScreen) AL.onScreen('home');
      };
      railHome.addEventListener('click', goHome);
      railHome.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goHome(); }
      });
    }
    const settingsBtn = $('#settings-btn');
    if (settingsBtn) {
      settingsBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (typeof window.openSettings === 'function') window.openSettings();
        else AL.openSettings();
      });
    }
    const closeTray = $('#close-to-tray');
    if (closeTray) closeTray.addEventListener('click', async () => {
      const mask = $('#close-mask');
      if (mask) mask.classList.remove('show');
      await AL.hideToTray();
    });
    const closeQuit = $('#close-quit');
    if (closeQuit) closeQuit.addEventListener('click', async () => {
      const mask = $('#close-mask');
      if (mask) mask.classList.remove('show');
      await AL.quitApp();
    });

    /* ---------- 창 제어 ---------- */
    const winMin = $('#win-min');
    if (winMin) winMin.addEventListener('click', (e) => { e.stopPropagation(); if (AL.real) call('minimize_window'); });
    const winClose = $('#win-close');
    if (winClose) winClose.addEventListener('click', (e) => {
      e.stopPropagation();
      /* 22. X 는 항상 '트레이로 / 프로그램 종료' 확인창 (v1.3.0) */
      const mask = $('#close-mask');
      if (mask) mask.classList.add('show');
      else if (AL.real) call('close_window');
    });

    const updateBar = $('#update-banner');
    if (updateBar) {
      updateBar.addEventListener('click', () => {
        const u = AL.update || {};
        if (u.url) call('open_external', u.url);
        else toast(u.message || '새 버전 정보가 없습니다', false);
      });
    }
    const cmpRefresh = $('#cmp-refresh');
    if (cmpRefresh) {
      cmpRefresh.addEventListener('click', () => {
        const mask = $('#compare-mask');
        if (mask) mask.classList.remove('show');
        if (AL.real) fetchLeaderboard();
      });
    }
    const perfApplyBtn = $('#perf-apply');
    if (perfApplyBtn) perfApplyBtn.addEventListener('click', perfApply);
    const perfRestoreBtn = $('#perf-restore');
    if (perfRestoreBtn) perfRestoreBtn.addEventListener('click', perfRestore);

    /* ---------- v1.4.0 · 전술 도구 · 스카우팅 · 듀오 · 맞춤 .plo ---------- */
    ['#util-scout', '#br-scout', '#lb-scout'].forEach((sel) => {
      const b = $(sel);
      if (b) b.addEventListener('click', (e) => { e.preventDefault(); openScout(); });
    });
    ['#util-duo', '#br-duo', '#lb-duo'].forEach((sel) => {
      const b = $(sel);
      if (b) b.addEventListener('click', (e) => { e.preventDefault(); openDuo(); });
    });
    const scoutRun = $('#scout-run');
    if (scoutRun) scoutRun.addEventListener('click', () => runScout());
    const scoutTable = $('#scout-table');
    if (scoutTable) scoutTable.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-role]');
      if (!btn) return;
      e.preventDefault();
      const name = btn.dataset.role;
      const current = (AL.scoutRoles || {})[name]
        || (((AL.scout && AL.scout.rows) || []).find((r) => r.name === name) || {}).weapon?.key
        || 'sniper';
      AL.scoutRoles = { ...(AL.scoutRoles || {}), [name]: current === 'sniper' ? 'rifle' : 'sniper' };
      renderScout(AL.scout || {});
    });
    const scoutInput = $('#scout-nicks');
    if (scoutInput) scoutInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); runScout(); } });
    const scoutCopy = $('#scout-copy');
    if (scoutCopy) scoutCopy.addEventListener('click', () => {
      const team = (AL.scout && AL.scout.team) || {};
      const text = [team.top ? `최고 위협: ${team.top} (${team.threat})` : '', ...(team.tips || [])].filter(Boolean).join('\n');
      if (!text) { toast('먼저 스카우팅을 실행해 주세요', false); return; }
      try {
        navigator.clipboard.writeText(text);
        toast('카운터 전략을 복사했습니다 · 디스코드에 붙여넣으세요', true);
      } catch (err) { toast(text, true, 8000); }
    });
    const scoutAuto = $('#scout-auto');
    if (scoutAuto) scoutAuto.addEventListener('click', () => runScoutAuto());
    const utilScoutAuto = $('#util-scout-auto');
    if (utilScoutAuto) utilScoutAuto.addEventListener('click', () => runScoutAuto());
    const utilDuoPick = $('#util-duo-pick');
    if (utilDuoPick) utilDuoPick.addEventListener('click', () => openDuo());
    const utilBalanceQuick = $('#util-balance-quick');
    if (utilBalanceQuick) utilBalanceQuick.addEventListener('click', () => openBalance());
    /* v2.0.1 — 스카우팅 패널의 오버레이 미리보기 (설정 탭과 같은 동작) */
    [['#pane-ov-scout', 'scout', '상대 분석 미리보기 · 12초'],
     ['#pane-ov-party', 'party', '파티 배지 미리보기 · 15초'],
     ['#pane-ov-toast', 'toast', '토스트 미리보기 · 5초']].forEach(([sel, kind, label]) => {
      const btn = $(sel);
      if (!btn) return;
      btn.addEventListener('click', async () => {
        if (!AL.real) return toast('앱(.exe)에서 실제 오버레이로 보여 줍니다', false);
        const r = await call('overlay_preview', kind, ($('#set-ov-pos') || {}).value || '');
        toast(r && r.ok ? label : ((r && r.message) || '미리보기를 띄우지 못했습니다'), !!(r && r.ok), 4200);
      });
    });
    const paneOvHide = $('#pane-ov-hide');
    if (paneOvHide) paneOvHide.addEventListener('click', async () => {
      if (AL.real) await call('overlay_hide', '');
      toast('오버레이를 모두 껐습니다', true);
    });

    /* ---------- v1.5.0 · 파티랭크 5인 맵 밴·픽 ---------- */
    const partyRun = $('#party-run');
    if (partyRun) partyRun.addEventListener('click', () => runPartyMap());
    const partyInput = $('#party-names');
    if (partyInput) partyInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); runPartyMap(); } });
    $('#util-pane-duo') && renderPartyMap(AL.partyMap || null);

    /* ---------- v1.0.0 · 자유 1:1 비교 (양쪽 아무 닉네임) ---------- */
    const cmpLeft2 = $('#cmp-left'), cmpRight2 = $('#cmp-right');
    const myNick = ((AL.state && AL.state.settings && AL.state.settings.nickname) ||
                    (AL.state && AL.state.nexon && AL.state.nexon.nickname) || '');
    if (cmpLeft2 && !cmpLeft2.value) cmpLeft2.value = myNick;
    const utilBalance = $('#util-balance');
    if (utilBalance) utilBalance.addEventListener('click', () => openBalance());
    const balBox = $('#balance-members');
    if (balBox) balBox.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-bal-role]');
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      const name = btn.dataset.balRole;
      const cands = [...((AL.balanceCands || {}).members || []), ...((AL.balanceCands || {}).coplayers || [])];
      const seed = cands.find((c) => c.name === name) || {};
      const current = (AL.balanceRoles || {})[name] || (seed.weapon === 'rifle' ? 'rifle' : 'sniper');
      AL.balanceRoles = { ...(AL.balanceRoles || {}), [name]: current === 'sniper' ? 'rifle' : 'sniper' };
      renderBalanceCandidates();
      const checked = new Set(balanceNames());
      checked.forEach((n2) => { const el = document.querySelector(`#balance-members input[data-bal-name="${n2}"]`); if (el) el.checked = true; });
    });
    const balanceRun = $('#balance-run');
    if (balanceRun) balanceRun.addEventListener('click', () => runBalance());
    /* v1.5.0 (#14) — 개별 삭제 · 비우기 · 전원 최신화 · 팀 복사 */
    const balBoxEl = $('#balance-members');
    if (balBoxEl) {
      balBoxEl.addEventListener('change', (e) => {
        const cb = e.target.closest('input[data-bal-name]');
        if (!cb) return;
        const set = new Set((AL.balancePicked || []).map((n) => n.toLowerCase()));
        if (cb.checked) set.add(String(cb.dataset.balName).toLowerCase());
        else set.delete(String(cb.dataset.balName).toLowerCase());
        AL.balancePicked = [...set];
        /* 목록을 다시 그리지 않고 행 테두리만 바꾼다 (체크가 풀리지 않게) */
        const row = cb.closest('label');
        if (row) row.style.borderColor = cb.checked ? 'var(--ac22)' : 'var(--line)';
        const live = $$('#balance-members input[data-bal-name]:checked').map((el) => el.dataset.balName);
        const note = $('#balance-note');
        if (note) note.textContent = live.length
          ? `선택 ${live.length}명 · ${live.length % 2 ? '짝수로 맞춰 주세요 (한 명은 제외됩니다)' : `${live.length / 2}:${live.length / 2} 로 나눕니다`}`
          : '입력한 사람만 계산합니다 · 체크한 인원이 0명이면 아무도 넣지 않습니다';
      });
      balBoxEl.addEventListener('click', (e) => {
        const rm = e.target.closest('[data-bal-remove]');
        if (!rm) return;
        e.preventDefault();
        e.stopPropagation();
        const name = String(rm.dataset.balRemove);
        const drop = (list) => (list || []).filter((c) => String(c.name).toLowerCase() !== name.toLowerCase());
        AL.balanceCands = {
          members: drop(AL.balanceCands && AL.balanceCands.members),
          coplayers: drop(AL.balanceCands && AL.balanceCands.coplayers),
        };
        AL.balancePicked = (AL.balancePicked || []).filter((n) => n.toLowerCase() !== name.toLowerCase());
        renderBalanceCandidates();
      });
    }
    const balClear = $('#balance-clear');
    if (balClear) balClear.addEventListener('click', () => {
      AL.balancePicked = [];
      const paste = $('#balance-paste');
      if (paste) paste.value = '';
      renderBalanceCandidates();
      toast('선택을 비웠습니다', true);
    });
    const balRefreshAll = $('#balance-refresh-all');
    if (balRefreshAll) balRefreshAll.addEventListener('click', async () => {
      if (!AL.real) { toast('앱(.exe)에서 전적을 새로 받아옵니다', false); return; }
      const names = (AL.balanceCands && AL.balanceCands.members || []).map((m) => m.name);
      if (!names.length) { toast('순위표에 등록된 멤버가 없습니다', false); return; }
      busy(true, '최근 기록을 새로 받아오는 중…');
      const res = await call('fetch_leaderboard', names, null, 0);
      busy(false);
      if (!res || res.ok === false) return fail(res, '최신화하지 못했습니다');
      const cand = await call('balance_candidates');
      AL.balanceCands = cand || AL.balanceCands;
      renderBalanceCandidates();
      const keep = new Set((AL.balancePicked || []).map((n) => n.toLowerCase()));
      $$('#balance-members input[data-bal-name]').forEach((el) => { el.checked = keep.has(String(el.dataset.balName).toLowerCase()); });
      toast(res.message || '전원 최근 기록을 최신화했습니다', true, 4200);
    });
    const balCopy = $('#balance-copy');
    if (balCopy) balCopy.addEventListener('click', async () => {
      const res = AL.balanceResult;
      if (!res || !(res.teams || res.a || res.b)) { toast('먼저 팀을 짜 주세요', false); return; }
      const teams = res.teams || { a: (res.a && res.a.rows) || [], b: (res.b && res.b.rows) || [] };
      const roles = res.roles || {};
      const label = { sniper: '스나', rifle: '라플' };
      const line = (key, name) => {
        const rows = teams[key] || [];
        if (!rows.length) return `[${name}] —`;
        const parts = rows.map((r) => {
          const nm = typeof r === 'string' ? r : (r.name || '');
          const role = typeof r === 'string' ? (roles[nm] || '') : (r.role || roles[nm] || '');
          return label[role] ? `${nm}(${label[role]})` : nm;
        }).filter(Boolean);
        return `[${name}] ${parts.join(' · ')}`;
      };
      const text = `${line('a', 'A팀')} / ${line('b', 'B팀')}`;
      let okCopy = false;
      window.__LAST_COPY = text;
      try { await navigator.clipboard.writeText(text); okCopy = true; } catch (err) { okCopy = false; }
      if (!okCopy && AL.real) {
        const r = await call('copy_text', text);
        okCopy = !!(r && r.ok);
      }
      toast(okCopy ? '팀을 복사했습니다 · 단톡·디스코드에 붙여넣으세요' : text, true, okCopy ? 4200 : 9000);
    });
    const balanceParse = $('#balance-parse');
    if (balanceParse) balanceParse.addEventListener('click', () => {
      const inp = $('#balance-paste');
      const names = String(inp ? inp.value : '').split(/[,，\n\t ]+/).map((s) => s.trim()).filter(Boolean).slice(0, 16);
      if (!names.length) { toast('닉네임을 붙여넣어 주세요', false); return; }
      const cands = AL.balanceCands || (AL.balanceCands = { members: [], coplayers: [] });
      const known = new Set([...(cands.members || []), ...(cands.coplayers || [])].map((c) => c.name));
      names.filter((n) => !known.has(n)).forEach((n) => { cands.coplayers = cands.coplayers || []; cands.coplayers.unshift({ name: n, games: 0, source: 'match' }); });
      renderBalanceCandidates();
      $$('#balance-members input[data-bal-name]').forEach((el) => { el.checked = names.includes(el.dataset.balName); });
      toast(`${names.length}명을 후보에 반영했습니다`, true);
    });
    const duoRankBtn = $('#duo-rank-btn');
    if (duoRankBtn) duoRankBtn.addEventListener('click', () => runDuoRank());
    const duoRun = $('#duo-run');
    if (duoRun) duoRun.addEventListener('click', () => runDuo());
    const duoInput = $('#duo-nick');
    if (duoInput) duoInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); runDuo(); } });
    /* ---------- v1.4.0 · 온보딩 ---------- */
    const onbLogin = $('#onb-login');
    if (onbLogin) onbLogin.addEventListener('click', () => onbCloud(false));
    const onbSignup = $('#onb-signup');
    if (onbSignup) onbSignup.addEventListener('click', () => onbCloud(true));
    const onbLocal = $('#onb-local');
    if (onbLocal) onbLocal.addEventListener('click', () => { onbStep = 1; paintOnb(); });
    const onbNext = $('#onb-next');
    if (onbNext) onbNext.addEventListener('click', () => {
      if (onbStep === 1) { onbStep = 2; paintOnb(); return; }
      finishOnboarding(false);
    });
    const onbPrev = $('#onb-prev');
    if (onbPrev) onbPrev.addEventListener('click', () => { onbStep = Math.max(0, onbStep - 1); paintOnb(); });
    const onbSkip = $('#onb-skip');
    if (onbSkip) onbSkip.addEventListener('click', () => finishOnboarding(true));
    $$('#onb-weapons button').forEach((b) => b.addEventListener('click', () => { onbPick.weapon = b.dataset.onbWeapon; paintOnb(); }));
    $$('#onb-games button').forEach((b) => b.addEventListener('click', () => { onbPick.game = b.dataset.onbGame; paintOnb(); }));

    /* ---------- v1.4.0 · 업데이트 게이트 · 연패 알림 ---------- */
    const upGo = $('#update-go');
    if (upGo) upGo.addEventListener('click', () => runUpdate());
    const upPage = $('#update-page');
    if (upPage) upPage.addEventListener('click', () => {
      const g = AL.gate || {};
      if (g.url) call('open_external', g.url);
      else toast('릴리스 페이지 주소가 없습니다', false);
    });
    const upLater = $('#update-later');
    if (upLater) upLater.addEventListener('click', () => {
      if ((AL.gate || {}).blocked) { call('close_window'); return; }
      const mask = $('#update-mask');
      if (mask) mask.classList.remove('show');
    });
    const tiltRest = $('#tilt-rest');
    if (tiltRest) tiltRest.addEventListener('click', () => {
      const mask = $('#tilt-mask');
      if (mask) mask.classList.remove('show');
      /* v2.0.1 — v1 훈련(warmup) 화면은 없어졌다. 홈으로 돌아가 기록을 보여 준다. */
      if (typeof window.showScreen === 'function') window.showScreen('home');
      toast('한 판 쉬고 최근 전적을 확인해 보세요', true, 4200);
    });

    /* ---------- 모달 닫기 ---------- */
    $$('[data-close]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const mask = $('#' + btn.dataset.close);
        if (mask) mask.classList.remove('show');
        if (btn.dataset.close === 'nick-mask') AL.nickLater = true;
        if (btn.dataset.close === 'auth-mask') AL.authLater = true;
      });
    });
    /* ---------- v1.0.1 · 닉네임 연결 팝업 ---------- */
    const nickConnect = $('#nick-connect');
    if (nickConnect) nickConnect.addEventListener('click', submitNickLink);
    const nickLater = $('#nick-later');
    if (nickLater) nickLater.addEventListener('click', () => closeNickLink(true));
    const nickInput = $('#nick-input');
    if (nickInput) nickInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submitNickLink(); } });
    $$('.mask').forEach((mask) => {
      mask.addEventListener('mousedown', (e) => {
        if (e.target !== mask) return;
        mask.classList.remove('show');
        if (mask.id === 'auth-mask') AL.authLater = true;
      });
    });
    $$('#cloud-tabs button').forEach((b) => b.addEventListener('click', () => setCloudMode(b.dataset.tab)));
    const cloudSubmit = $('#cloud-submit');
    if (cloudSubmit) cloudSubmit.addEventListener('click', submitCloud);

    /* ---------- 병영수첩 ---------- */
    const brRefresh = $('#br-refresh');
    if (brRefresh) brRefresh.addEventListener('click', () => { fetchBarracks(); });
    const brExport = $('#br-export');
    if (brExport) brExport.addEventListener('click', () => { if (AL.real) backupNow(); });

    /* ---------- 병영수첩 닉네임 검색 ---------- */
    const searchBtn = $('#br-search-btn');
    if (searchBtn) searchBtn.addEventListener('click', searchUser);
    const searchInput = $('#br-search-input');
    if (searchInput) {
      searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); searchUser(); }
      });
    }
    const resultBox = $('#br-search-result');
    if (resultBox) {
      resultBox.addEventListener('click', (e) => {
        const hit = e.target.closest('[data-peek]');
        if (!hit) return;
        openPeek(hit.dataset.nick, hit.dataset.ouid || null);
      });
    }
    const peekBack = $('#br-peek-back');
    if (peekBack) peekBack.addEventListener('click', () => {
      AL.peek = null;
      renderPeek();
      const box = $('#br-search-result');
      if (box) { box.hidden = true; box.innerHTML = ''; }
      toast('내 병영수첩으로 돌아왔습니다', true);
    });
    const peekAdd = $('#br-peek-add');
    if (peekAdd) peekAdd.addEventListener('click', async () => {
      if (!AL.peek) return;
      await AL.addMember(AL.peek.nickname, AL.peek.ouid);
    });

    $$('#br-view-seg button').forEach((b) => {
      b.addEventListener('click', () => {
        $$('#br-view-seg button').forEach((x) => x.classList.toggle('on', x === b));
        AL.view = b.dataset.view;
        const b_ = (AL.peek || (AL.state && AL.state.barracks));
        if (!b_) return;
        if (AL.view === 'matches') renderMatches(b_);
        else renderMaps(b_);
      });
    });

    /* ---------- 순위표 ---------- */
    const lbRefresh = $('#lb-refresh');
    if (lbRefresh) lbRefresh.addEventListener('click', () => { if (AL.real) fetchLeaderboard(); });
    const lbBackup = $('#lb-backup');
    if (lbBackup) lbBackup.addEventListener('click', () => { if (AL.real) backupNow(); });

    /* ---------- 유틸 ---------- */
    AL.utilClick = function (event) {
      if (!AL.real) return;
      const e = event || {};
      const target = e.target || {};
      const closest = (sel) => (target.closest ? target.closest(sel) : null);
      const ap = closest('[data-apply]');
      if (ap) {
        if (ap.dataset.apply === 'alias') toggleAlias();
        else applyNvidia(ap.dataset.apply);
        return;
      }
      if (closest('#nv-restore') || closest('#changes-restore')) return restoreNvidia();
      if (closest('#nv-changes-btn')) return openChanges(LAST_CHANGES || (AL.utils && AL.utils.last_changes));
      if (closest('#accel-switch') || closest('#accel-btn')) return toggleAccel();
      if (closest('#perf-switch')) return togglePerfSwitch();
      if (closest('#bk-save')) return backupNow();
      if (closest('#bk-load')) return restoreBackup();
    };

    /* ---------- 설정 ---------- */
    const settings = $('#settings-mask') || $('#s-settings');
    if (settings) {
      const box = $('#settings-mask') || settings;
      box.addEventListener('click', async (e) => {
        const target = e.target.closest('button, [data-switch]');
        if (!target) return;

        const actBtn = target.closest('[data-act]');
        if (actBtn) {
          const key = actBtn.dataset.act;
          if (key === 'save-nick' || key === 'save-run') {
            if (!AL.real) return toast('앱(.exe)에서 저장할 수 있습니다', false);
            const patch = collectSettings();
            if (key === 'save-nick' && !patch.nickname) return toast('서든어택 닉네임을 입력해 주세요', false);
            const saved = await call('save_settings', patch);
            if (!saved || !saved.ok) return fail(saved, '저장하지 못했습니다');
            if (saved.state) applyState(saved.state);
            if (key === 'save-nick') { toast('✅ 저장했습니다 · 전적을 불러옵니다', true); return AL.refreshBarracks(); }
            return toast('✅ 저장했습니다', true);
          }
          if (key === 'save-goals') {
            if (!AL.real) return toast('앱(.exe)에서 저장할 수 있습니다', false);
            const goals = {};
            ['win', 'kd', 'dmg', 'rp'].forEach((k) => {
              const el = $('#set-goal-' + k);
              if (el && String(el.value).trim() !== '') goals[k] = Number(el.value);
            });
            const result = await call('save_settings', { goals });
            if (result && result.state) applyState(result.state);
            return toast('🎯 목표를 저장했습니다', true);
          }
          if (key === 'ov-save') {
            if (!AL.real) return toast('앱(.exe)에서 저장할 수 있습니다', false);
            const saved = await call('save_settings', collectSettings());
            if (!saved || !saved.ok) return fail(saved, '오버레이 설정을 저장하지 못했습니다');
            if (saved.state) applyState(saved.state);
            const hot = ((saved.state || {}).overlay || {}).hotkeys || {};
            return toast(hot.registered && hot.registered.length
              ? `✅ 오버레이 저장 · 단축키 ${hot.registered.join(', ')}`
              : '✅ 저장했지만 단축키 등록이 안 됐습니다 (Windows · 키 선점 확인)', true, 4600);
          }
          if (key === 'ov-toast-preview' || key === 'ov-party-preview' || key === 'ov-scout-preview') {
            if (!AL.real) return toast('앱(.exe)에서 실제 오버레이로 보여 줍니다', false);
            const kind = key === 'ov-toast-preview' ? 'toast' : key === 'ov-party-preview' ? 'party' : 'scout';
            const r = await call('overlay_preview', kind, ($('#set-ov-pos') || {}).value || '');
            return toast(r && r.ok ? '미리보기를 띄웠습니다 · 잠시 뒤 사라집니다' : (r && r.message) || '미리보기를 띄우지 못했습니다', !!(r && r.ok), 4200);
          }
          if (key === 'ov-hide') { if (AL.real) await call('overlay_hide', ''); return toast('오버레이를 모두 껐습니다', true); }
          if (key === 'preset-save') {
            if (!AL.real) return toast('앱(.exe)에서 저장할 수 있습니다', false);
            const name = ($('#ov-preset-name') || {}).value || '';
            const r = await call('save_party_preset', name, null, true);
            if (!r || !r.ok) return fail(r, '프리셋을 저장하지 못했습니다');
            if (r.state) applyState(r.state);
            return toast(r.message || '프리셋을 저장했습니다', true);
          }
          if (key === 'auth-open') { openAuth('login'); return; }
          if (key === 'auth-out') {
            if (!AL.real) return toast('앱(.exe)에서 로그아웃합니다', false);
            const r = await call('auth_signout');
            if (r && r.state) applyState(r.state);
            return toast((r && r.message) || '로그아웃했습니다', !!(r && r.ok));
          }
          if (key === 'cloud-push') { if (AL.real) cloudAction('push'); else toast('앱(.exe)에서 올립니다', false); return; }
          if (key === 'cloud-pull') { if (AL.real) cloudAction('pull'); else toast('앱(.exe)에서 내려받습니다', false); return; }
          if (key === 'refresh-now') { AL.refreshBarracks(); return; }
          if (key === 'open-folder') {
            const result = await call('open_data_folder');
            return toast(result && result.ok ? '📂 데이터 폴더를 열었습니다' : ((result && result.message) || '폴더를 열지 못했습니다'), !!(result && result.ok));
          }
          if (key === 'backup') { if (AL.real) backupNow(); else toast('앱(.exe)에서 백업합니다', false); return; }
          if (key === 'restore') { if (AL.real) restoreBackup(); else toast('앱(.exe)에서 복원합니다', false); return; }
          return;
        }
        const toggle = target.closest('[data-switch]');
        if (toggle) {
          const key = toggle.dataset.switch;
          const on = toggle.classList.contains('on');
          if (!AL.real) { toggle.classList.toggle('on', !on); return; }
          const result = await call('save_settings', { [key]: !on });
          toggle.classList.toggle('on', !on);
          toggle.setAttribute('aria-checked', (!on) ? 'true' : 'false');
          if (result && result.state) applyState(result.state);
          const labels = {
            auto_launch: '자동 실행', autosave: '점수 자동 저장', auto_sync: '자동 동기화', notify: '알림',
            hotkey_enabled: 'Ctrl+Shift+A 단축키', tilt_watch: '연패 브레이크', auto_cloud: '자동 백업', overlay_enabled: '오버레이', toast_enabled: '결과 토스트', toast_auto: '새 경기 자동 감지', ui_scale_auto: '화면 배율 자동'
          };
          toast(`${labels[key] || key} ${!on ? '켜짐' : '꺼짐'}`, true);
          return;
        }

        if (e.target.closest('#set-api-save') || e.target.closest('#set-api-test')) {
          if (!AL.real) return toast('앱(.exe)에서 키를 저장할 수 있습니다', false);
          const patch = collectSettings();
          if (!patch.nickname) return toast('서든어택 닉네임을 입력해 주세요', false);
          const saved = await call('save_settings', patch);
          if (!saved || !saved.ok) return fail(saved, '저장하지 못했습니다');
          if (saved.state) applyState(saved.state);
          if (e.target.closest('#set-api-test')) return fetchBarracks(true);
          return toast('✅ NEXON 설정을 저장했습니다', true);
        }
        if (e.target.closest('#set-run-save')) {
          if (!AL.real) return toast('앱(.exe)에서 저장할 수 있습니다', false);
          const result = await call('save_settings', collectSettings());
          if (result && result.state) applyState(result.state);
          return toast('✅ 실행 설정을 저장했습니다', true);
        }
        if (e.target.closest('#ov-save')) {
          if (!AL.real) return toast('앱(.exe)에서 저장할 수 있습니다', false);
          const saved = await call('save_settings', collectSettings());
          if (!saved || !saved.ok) return fail(saved, '오버레이 설정을 저장하지 못했습니다');
          if (saved.state) applyState(saved.state);
          const hot = ((saved.state || {}).overlay || {}).hotkeys || {};
          return toast(hot.registered && hot.registered.length
            ? `✅ 오버레이 저장 · 단축키 ${hot.registered.join(', ')}`
            : '✅ 저장했지만 단축키 등록이 안 됐습니다 (Windows · 키 선점 확인)', true, 4600);
        }
        if (e.target.closest('#ov-toast-preview')) {
          if (!AL.real) return toast('앱(.exe)에서 실제 오버레이로 보여 줍니다', false);
          const r = await call('overlay_toast_preview', ($('#set-ov-pos') || {}).value || '');
          return toast(r && r.ok ? '토스트 미리보기 · 5초 뒤 사라집니다' : (r && r.message) || '미리보기를 띄우지 못했습니다', !!(r && r.ok));
        }
        if (e.target.closest('#ov-party-preview')) {
          if (!AL.real) return toast('앱(.exe)에서 실제 오버레이로 보여 줍니다', false);
          const r = await call('overlay_preview', 'party');
          return toast(r && r.ok ? '파티 배지 미리보기 · 10초' : (r && r.message) || '미리보기를 띄우지 못했습니다', !!(r && r.ok));
        }
        if (e.target.closest('#ov-scout-preview')) {
          if (!AL.real) return toast('앱(.exe)에서 실제 오버레이로 보여 줍니다', false);
          const r = await call('overlay_preview', 'scout');
          return toast(r && r.ok ? '상대 분석 미리보기 · 12초' : (r && r.message) || '미리보기를 띄우지 못했습니다', !!(r && r.ok));
        }
        if (e.target.closest('#ov-hide')) {
          if (!AL.real) return;
          await call('overlay_hide', '');
          return toast('오버레이를 모두 껐습니다', true);
        }
        if (e.target.closest('#ov-preset-save')) {
          if (!AL.real) return toast('앱(.exe)에서 저장할 수 있습니다', false);
          const nameEl = $('#ov-preset-name');
          const name = nameEl ? nameEl.value : '';
          const r = await call('save_party_preset', name, null, true);
          if (!r || !r.ok) return fail(r, '프리셋을 저장하지 못했습니다');
          if (r.state) applyState(r.state);
          return toast(r.message || '프리셋을 저장했습니다', true);
        }
        const presetLoad = e.target.closest('[data-preset-load]');
        if (presetLoad) {
          const r = await call('load_party_preset', presetLoad.dataset.presetLoad);
          if (!r || !r.ok) return fail(r, '프리셋을 불러오지 못했습니다');
          const input = $('#party-names');
          if (input) input.value = (r.members || []).join(', ');
          if (r.mapban) { AL.partyMap = r.mapban; renderPartyMap(r.mapban); }
          return toast(`${r.message} · 전술 도구에 채웠습니다`, true);
        }
        const presetDel = e.target.closest('[data-preset-del]');
        if (presetDel) {
          const r = await call('delete_party_preset', presetDel.dataset.presetDel);
          if (r && r.state) applyState(r.state);
          else if (r && r.presets) { const st = Object.assign({}, AL.state || {}); st.party_presets = r.presets; AL.state = st; renderSettings(st); }
          return toast(r && r.message || '삭제했습니다', !!(r && r.ok));
        }
        if (e.target.closest('#set-open-folder')) {
          const result = await call('open_data_folder');
          return toast(result.ok ? '📂 기록 폴더를 열었습니다' : (result.message || '폴더를 열지 못했습니다'), !!result.ok);
        }
        if (e.target.closest('#set-goal-save')) {
          if (!AL.real) return toast('앱(.exe)에서 저장할 수 있습니다', false);
          const goals = {};
          ['win', 'kd', 'dmg', 'rp'].forEach((k) => {
            const el = $('#set-goal-' + k);
            if (el && String(el.value).trim() !== '') goals[k] = Number(el.value);
          });
          const result = await call('save_settings', { goals });
          if (result && result.state) applyState(result.state);
          return toast('🎯 목표를 저장했습니다', true);
        }
        if (e.target.closest('#set-backup')) { if (AL.real) backupNow(); return; }
        const themeBtn = e.target.closest('[data-theme-key]');
        if (themeBtn) {
          const key = themeBtn.dataset.themeKey;
          applyTheme(key);
          $$('#set-theme .theme-btn').forEach((b) => b.classList.toggle('on', b === themeBtn));
          if (AL.real) {
            const r = await call('save_settings', { theme: key });
            if (r && r.state) AL.state = r.state;
          }
          return toast(`테마 · ${themeBtn.textContent.trim()}`, true);
        }
        const scaleBtn = e.target.closest('[data-scale]');
        if (scaleBtn) {
          const px = Number(scaleBtn.dataset.scale) || 100;
          applyUiScale(px);
          $$('#set-scale .scale-btn').forEach((b) => b.classList.toggle('on', b === scaleBtn));
          if (AL.real) {
            const r = await call('save_settings', { ui_scale: px });
            if (r && r.state) AL.state = r.state;
          }
          return toast(`화면 배율 ${px}% · 창을 다시 열면 크기도 맞춰집니다`, true, 4200);
        }
        if (e.target.closest('#set-update-check')) { checkUpdate(true); return; }
        if (e.target.closest('#set-tray-hide')) { trayHide(); return; }
        if (e.target.closest('#set-cloud-login')) { openCloudModal('signin'); return; }
        if (e.target.closest('#set-cloud-out')) { if (AL.real) cloudAction('out'); return; }
        if (e.target.closest('#set-cloud-check')) { if (AL.real) cloudAction('check'); return; }
        if (e.target.closest('#set-cloud-push')) { if (AL.real) cloudAction('push'); return; }
        if (e.target.closest('#set-cloud-pull')) { if (AL.real) cloudAction('pull'); return; }
        if (e.target.closest('#set-save')) {
          if (!AL.real) return toast('앱(.exe)에서 저장할 수 있습니다', false);
          const result = await call('save_settings', collectSettings());
          if (result && result.state) applyState(result.state);
          return toast('✅ 설정을 저장했습니다', true);
        }
      });
    }

    /* ---------- index.html 쪽 훅 ---------- */
    AL.onScreen = function (name) {
      if (!AL.state) return;
      /* v2.0.1 — 레일 메뉴는 6개(scout·duo·system 은 한 화면의 탭) */
      if (name === 'scout' || name === 'duo' || name === 'system') name = 'utils';
      if (name === 'barracks') {
        if (AL.peek) { renderPeek(); return; }
        const b = AL.state.barracks;
        if (AL.view === 'matches' && b) renderMatches(b);
        else if (b) renderMaps(b);
      }
      if (name === 'maps') { renderMaps(AL.peek || AL.state.barracks); }
      if (name === 'leaderboard') { refreshLeaderboardNames(); }
      if (name === 'utils') {
        perfRefresh(false);
        /* v2.0.1 — 탭을 오갈 때마다 API 를 다시 부르지 않는다 (처음 한 번만 최신화) */
        loadRecentOpponents(!AL.recentOpp);
        const left = $('#cmp-left');
        if (left && !left.value) {
          left.value = (AL.state.settings && AL.state.settings.nickname) ||
                       (AL.state.nexon && AL.state.nexon.nickname) || '';
        }
      }
    };
    AL.onSwitch = function () {
      if (!AL.state) return;
      /* v2.0.1 — applySummaryOnly(v1 에임 요약)는 삭제됐다. 화면 값만 다시 그린다. */
      if (typeof drawHome === 'function') drawHome();
      if (AL.peek) renderPeek();
    };

    /* v1.0.1 (#9) — 순위표를 열 때 저장된 ouid 로 현재 닉네임·클랜을 다시 확인한다.
       user/basic 은 30분 캐시라 화면 전환마다 불러도 호출이 거의 늘지 않는다. */
    let lbNameBusy = false;
    async function refreshLeaderboardNames(force) {
      if (!AL.real || lbNameBusy) return;
      const lb = (AL.state && AL.state.leaderboard) || [];
      if (!lb.length) return;
      const stamp = Date.now();
      if (!force && AL.lbNamesAt && stamp - AL.lbNamesAt < 5 * 60 * 1000) return;
      lbNameBusy = true;
      try {
        const res = await call('refresh_leaderboard_names', !!force);
        AL.lbNamesAt = Date.now();
        if (res && res.state) applyState(res.state);
        if (res && (res.renamed || []).length) toast(res.message || '닉네임을 최신화했습니다', true, 4600);
      } catch (err) {
        AL.lbNamesAt = Date.now();
      } finally {
        lbNameBusy = false;
      }
    }
    AL.refreshLeaderboardNames = refreshLeaderboardNames;
    /* v2.0.1 — 스카우팅 패널의 오버레이 단축키 안내 (설정값을 따른다) */
  AL.syncOverlayPane = function (st) {
    const el = $('#pane-ov-sub');
    if (!el) return;
    const ovs = (((st || AL.state || {}).overlay) || {}).settings || {};
    el.textContent = `${ovs.hotkey_lobby || 'Insert'} 대기실 · 맵 밴 / ${ovs.hotkey_scout || 'Home'} 상대 분석`;
  };
  AL.openSettings = function () {
      renderSettings(AL.state || { settings: {}, nexon: {}, cloud: {}, meta: {} });
      renderSettingsTray(AL.tray);
      renderUpdate(AL.update);
      if (typeof compactGrayNotes === 'function') compactGrayNotes();
      const mask = $('#settings-mask');
      if (mask) mask.classList.add('show');
    };
    AL.ackDigest = async function () {
      if (!AL.real) return;
      const result = await call('ack_digest');
      if (result && result.state) applyState(result.state);
    };
    AL.hideToTray = async function () {
      if (!AL.real) return toast('앱(.exe)에서 트레이로 숨길 수 있습니다', false);
      const result = await call('tray_hide');
      toast((result && result.message) || '트레이로 숨겼습니다', true, 5000);
    };
    AL.quitApp = async function () {
      if (!AL.real) { toast('미리보기입니다 · 앱은 종료되지 않습니다', true); return; }
      await call('close_window');
    };
    /* v1.5.0 (#23) — 작업표시줄 ✕ · Alt+F4 도 X 버튼과 같은 확인 팝업 */
    AL.confirmClose = function () {
      const mask = $('#close-mask');
      if (mask) { mask.classList.add('show'); return true; }
      return false;
    };
;
;
    AL.addMember = async function (name, ouid) {
      if (!AL.real) return;
      busy(true, `'${name}' 전적을 불러오는 중…`);
      const result = await call('add_leaderboard_member', name, 0, ouid || null);
      busy(false);
      if (!result || !result.ok) return fail(result, `'${name}' 등록 실패`);
      if (result.state) applyState(result.state);
      toast(result.message || `'${name}' 등록 완료 · 전적 동기화됨`, true);
    };
    AL.openBarracks = async function (ouid, name) {
      const target = ouid || ((AL.state && AL.state.barracks) || {}).ouid || '';
      if (!AL.real) {
        toast('앱(.exe)에서 병영수첩 홈페이지를 엽니다', true);
        return;
      }
      const result = await call('open_barracks_page', target || null, name || null);
      toast(result && result.ok
        ? `${name || '내'} 병영수첩을 브라우저에서 엽니다`
        : ((result && result.message) || '병영수첩을 열지 못했습니다'), !!(result && result.ok), 5200);
    };
    AL.openClan = async function (ouid, name) {
      const target = ouid || ((AL.state && AL.state.barracks) || {}).ouid || '';
      if (!AL.real) {
        toast('앱(.exe)에서 클랜 홈페이지를 엽니다', true);
        return;
      }
      const result = await call('open_clan_page', target || null, name || null);
      toast(result && result.ok
        ? `${name || ''} 클랜 홈페이지를 브라우저에서 엽니다`
        : ((result && result.message) || '클랜 페이지를 열지 못했습니다'), !!(result && result.ok), 5200);
    };
    AL.saveMemo = async function (name, text) {
      if (!AL.real) return toast('앱(.exe)에서 메모를 저장합니다', false);
      const result = await call('save_member_memo', name, text);
      if (!result || !result.ok) return fail(result, '메모를 저장하지 못했습니다');
      if (result.state) applyState(result.state);
      if (result.memos) { MEMOS = result.memos; AL.memos = result.memos; }
      if (typeof drawLeaderboard === 'function') drawLeaderboard();
      const mask = $('#memo-mask');
      if (mask) mask.classList.remove('show');
      toast(result.message || '메모를 저장했습니다', true);
    };
    AL.removeMember = async function (name) {
      if (!AL.real) return;
      const result = await call('remove_leaderboard_member', name);
      if (!result || !result.ok) return fail(result, '삭제하지 못했습니다');
      if (result.state) applyState(result.state);
      toast(result.message || `'${name}' 삭제됨`, true);
    };
  }

  /* state 없이 게임/무기만 바뀐 경우: 저장된 state 로 다시 그린다 */

  /* ==================================================================
     부팅
     ------------------------------------------------------------------
     .exe 에서는 두 가지를 반드시 지킨다.
       1) window.pywebview.api 가 늦게 올라와도(콜드 스타트 5~15초) 충분히 기다린다.
       2) bootstrap() → applyState() 로 실제 데이터가 다 그려진 다음에만
          스플래시 100% → 메인창.  끝내 실패하면 샘플(미리보기) 데이터를
          보여 주지 않고 오류·재시도 화면을 띄운다.
     브라우저 미리보기(--serve)에서만 샘플 데이터를 허용한다.
     ================================================================== */
  const DESKTOP = window.__AL_DESKTOP__ === true
    || (location.protocol === 'file:' && !new URLSearchParams(location.search).has('preview'))
    || new URLSearchParams(location.search).get('desktop') === '1';
  /* 로그 경로: 브리지가 살아 있으면 bootstrap 응답의 meta.log_file 로 갱신되고,
     브리지가 죽어 있으면(그때가 바로 오류 화면이 필요한 순간) 기본 안내 문구를 쓴다. */
  const DEFAULT_LOG_HINT = '%APPDATA%\\SA_Aim_Labs\\logs\\app.log';
  let LOG_HINT = window.__AL_LOG__ || DEFAULT_LOG_HINT;
  function setLogHint(path) {
    if (!path) return;
    LOG_HINT = String(path);
    window.__AL_LOG__ = LOG_HINT;
  }
  const bootQuery = new URLSearchParams(location.search);
  const BOOT_TIMEOUT = Math.max(1000, Number(bootQuery.get('boottimeout')) || 15000);
  const BOOTSTRAP_TIMEOUT = Math.max(1500, Number(bootQuery.get('boottimeout')) || 20000);
  const POLL_MS = 50;
  let phase = 'idle';           /* idle | waiting | booting | ready | failed */
  let watchTimer = null;

  function splashSet(value, label) {
    if (window.SPLASH) window.SPLASH.set(value, label);
  }
  function splashFinish(label) {
    if (!window.SPLASH) return;
    if (label) window.SPLASH.set(100, label);
    window.SPLASH.finish();
  }
  const bridgeUp = () => hasApi('bootstrap');

  /* pywebviewready 가 늦게 오거나 아예 안 와도 50ms 폴링으로 잡아낸다 */
  function watchBridge() {
    if (phase === 'booting' || phase === 'ready') return;
    phase = 'waiting';
    clearTimeout(watchTimer);
    const deadline = Date.now() + BOOT_TIMEOUT;
    const tick = () => {
      if (phase === 'booting' || phase === 'ready') return;
      if (bridgeUp()) { phase = 'idle'; boot(); return; }
      if (Date.now() >= deadline) { onBridgeTimeout(); return; }
      watchTimer = setTimeout(tick, POLL_MS);
    };
    tick();
  }

  function onBridgeTimeout() {
    if (phase === 'ready') return;
    phase = 'failed';
    if (DESKTOP) {
      splashFinish('앱 연결 실패');
      showBridgeError(
        `앱 내부 연결(pywebview 브리지)이 ${Math.round(BOOT_TIMEOUT / 1000)}초 안에 준비되지 않았습니다.`,
        ['Microsoft Edge WebView2 Runtime 이 없다면 아래 주소에서 "Evergreen Bootstrapper"를 설치한 뒤 앱을 다시 실행해 주세요. ' +
         '(https://developer.microsoft.com/microsoft-edge/webview2/)',
         '잠시 뒤 [다시 시도]를 눌러 주세요.', '계속 안 되면 아래 로그 파일을 확인해 주세요.']
      );
    } else {
      splashFinish('미리보기 모드');   /* 브라우저 미리보기에서만 */
    }
  }

  async function boot() {
    if (phase === 'booting' || phase === 'ready') return;
    if (!bridgeUp()) { watchBridge(); return; }
    phase = 'booting';
    AL.real = true;
    hideBridgeError();
    splashSet(18, '앱 데이터를 불러오는 중…');
    busy(true, '앱 데이터를 불러오는 중…');
    let result = null;
    try {
      result = await Promise.race([
        call('bootstrap'),
        new Promise((resolve) => setTimeout(() => resolve({
          ok: false, timeout: true,
          message: `앱 데이터 응답이 없습니다 (${Math.round(BOOTSTRAP_TIMEOUT / 1000)}초)`
        }), BOOTSTRAP_TIMEOUT))
      ]);
    } catch (err) {
      result = { ok: false, message: String((err && err.message) || err) };
    }
    busy(false);
    if (!result || !result.ok) {
      phase = 'failed';
      AL.real = false;
      if (result && result.log_file) setLogHint(result.log_file);
      const message = (result && result.message) || '앱 데이터를 불러오지 못했습니다';
      if (DESKTOP) {
        splashFinish('앱 데이터를 불러오지 못했습니다');
        showBridgeError(message, ['앱은 실행됐지만 데이터를 불러오지 못했습니다.']);
      } else {
        splashFinish('미리보기 모드');
        toast(message, false);
      }
      return;
    }
    try {
      if (result.state) {
        if (result.state.meta && result.state.meta.log_file) setLogHint(result.state.meta.log_file);
        splashSet(58, '훈련 기록 · 티어 계산 중…');
        applyState(result.state);
        AL.ready = true;
      }
    } catch (err) {
      phase = 'failed';
      AL.real = false;
      splashFinish('화면을 그리지 못했습니다');
      showBridgeError('화면을 그리지 못했습니다 · ' + String((err && err.message) || err), []);
      return;
    }
    splashSet(92, '화면을 그리는 중…');
    /* 데이터가 전부 그려진 다음 프레임에 100% → 메인창 */
    requestAnimationFrame(() => requestAnimationFrame(() => {
      splashSet(100, '준비 완료');
      splashFinish();
      phase = 'ready';
      hideBridgeError();
    }));
    /* 새 버전 배너 — 시작할 때 파이썬이 백그라운드로 확인한 결과를 조용히 받아온다.
       아직 확인 중(pending)이면 몇 번 더 조용히 물어본다. */
    const pollUpdate = (left) => {
      if (!AL.real) return;
      Promise.resolve(checkUpdate(false, true)).then((r) => {
        if (left > 0 && (!r || r.pending)) setTimeout(() => pollUpdate(left - 1), 2500);
      }).catch(() => {});
    };
    setTimeout(() => pollUpdate(2), 1500);

    /* 무거운 확인(DRS 스캔 · 마우스 설정)은 메인창이 뜬 뒤 백그라운드로 —
       여기서 기다리면 스플래시가 82% 에 멈춰 "무응답"처럼 보인다 */
    setTimeout(async () => {
      try {
        const nv = await Promise.race([
          call('get_nvidia_state'),
          new Promise((resolve) => setTimeout(() => resolve(null), 8000))
        ]);
        if (nv && nv.utils) {
          AL.utils = nv.utils;
          renderUtilsState(nv.utils);
          if (nv.state) applyState(nv.state);
        }
      } catch (err) { /* 유틸 상태를 못 읽어도 앱은 정상 동작 */ }
    }, 160);
  }

  /* ------------------------------------------------------------ 시작 오류 화면 */

  function showBridgeError(message, hints) {
    let el = document.getElementById('al-bridge-error');
    if (!el) {
      el = document.createElement('div');
      el.id = 'al-bridge-error';
      el.innerHTML = [
        '<div class="abx-card">',
        '  <div class="abx-tag">SA AIM LABS · 시작 오류</div>',
        '  <h1 class="abx-title">앱 데이터에 연결하지 못했습니다</h1>',
        '  <p class="abx-msg"></p>',
        '  <ul class="abx-hints"></ul>',
        '  <div class="abx-log"><span>로그 파일</span><code></code></div>',
        '  <div class="abx-status" hidden></div>',
        '  <div class="abx-actions">',
        '    <button type="button" class="abx-btn primary" data-abx="retry">다시 시도</button>',
        '    <button type="button" class="abx-btn" data-abx="log">로그 폴더 열기</button>',
        '    <button type="button" class="abx-btn ghost" data-abx="close">앱 종료</button>',
        '  </div>',
        '</div>'
      ].join('\n');
      document.body.appendChild(el);
      el.addEventListener('click', async (ev) => {
        const btn = ev.target && ev.target.closest ? ev.target.closest('[data-abx]') : null;
        if (!btn) return;
        const kind = btn.getAttribute('data-abx');
        if (kind === 'retry') {
          setBridgeErrorStatus('다시 연결하는 중… 최대 ' + Math.round(BOOT_TIMEOUT / 1000) + '초 기다립니다');
          phase = 'idle';
          if (bridgeUp()) boot();
          else watchBridge();
        } else if (kind === 'log') {
          const opened = await call('open_data_folder');
          if (!opened || !opened.ok) {
            setBridgeErrorStatus('로그 파일: ' + LOG_HINT);
          }
        } else if (kind === 'close') {
          const closed = await call('close_window');
          if (!closed || !closed.ok) {
            try { window.close(); } catch (err) { /* ignore */ }
          }
        }
      });
    }
    el.querySelector('.abx-msg').textContent = message || '알 수 없는 오류입니다';
    const list = el.querySelector('.abx-hints');
    list.innerHTML = (hints || []).map((h) => `<li>${esc(h)}</li>`).join('');
    list.hidden = !(hints && hints.length);
    el.querySelector('.abx-log code').textContent = LOG_HINT;
    const status = el.querySelector('.abx-status');
    status.hidden = true;
    status.textContent = '';
    el.hidden = false;
    el.style.display = 'flex';
    return el;
  }

  function setBridgeErrorStatus(text) {
    const el = document.getElementById('al-bridge-error');
    if (!el) return;
    const status = el.querySelector('.abx-status');
    if (!status) return;
    status.textContent = text;
    status.hidden = !text;
  }

  function hideBridgeError() {
    const el = document.getElementById('al-bridge-error');
    if (el) { el.hidden = true; el.style.display = 'none'; }
  }

  /* 미리보기( 브라우저 )가 아니라 .exe 인데 브리지가 없으면 샘플 데이터 대신 오류 안내 */
  function notReady() {
    if (DESKTOP) {
      showBridgeError('앱 데이터가 아직 연결되지 않았습니다.', ['[다시 시도]를 눌러 주세요.']);
      return toast('앱 연결이 준비되지 않았습니다 · [다시 시도]를 눌러 주세요', false);
    }
    return toast('미리보기 화면입니다 · .exe 로 실행하면 전적을 불러옵니다', false);
  }

  document.addEventListener('DOMContentLoaded', () => {
    bind();
    if (bridgeUp()) boot();
    else watchBridge();
  });
  window.addEventListener('pywebviewready', () => boot());
  if (document.readyState !== 'loading') {
    bind();
    if (bridgeUp()) boot();
    else watchBridge();
  }
})();
