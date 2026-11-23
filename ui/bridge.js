/* =====================================================================
   SA AIM LABS · ui/bridge.js
   ---------------------------------------------------------------------
   index.html 의 정적 화면을 pywebview.api (app/api.py) 에 연결합니다.

   · 창 제어 (최소화 / 최대화 / 닫기)
   · ▶ 스팀 실행 · ▶ 에임랩 실행  → launch_trainer(steam:// / aimlab://)
   · 추천 5종 점수 입력 + 오늘 기록 저장 → save_training_session(...)
   · 병영수첩 전적 불러오기 / 순위표 일괄 갱신 · 닉네임 등록·삭제
   · 최적화 유틸 (Drs 라플·스나·계단현상, 마우스 가속, 백업/복원)
   · 설정 (NEXON API 키 · 닉네임, Supabase 로그인/회원가입/동기화)
   · 서버가 돌려준 state 로 대시보드·레이더·추이·순위표 실시간 갱신

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

  const hasApi = (name) => !!(window.pywebview && window.pywebview.api && typeof window.pywebview.api[name] === 'function');

  /* ------------------------------------------------------------------ 스타일 */
  const style = document.createElement('style');
  style.textContent = [
    '.field input.inp{width:100%;outline:none;font-family:var(--num);font-size:11px;color:#CBD8E8}',
    '.field input.inp::placeholder{color:transparent}',
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

  function areaIdx(key) {
    const i = AREAS.findIndex((a) => a.key === key);
    return i < 0 ? 0 : i;
  }

  function currentSummary() {
    const st = AL.state;
    if (!st || !st.summaries) return null;
    return st.summaries[`${game}-${weapon}`] || null;
  }

  /* 서버 rec → 화면 rec (오늘 내 점수 포함) */
  function mapRecs(rows) {
    return (rows || []).map((r) => ({
      name: r.name,
      area: typeof r.area === 'number' ? r.area : areaIdx(r.area_key),
      areaKey: r.area_key || (AREAS[typeof r.area === 'number' ? r.area : 0] || AREAS[0]).key,
      areaName: r.area_name || '',
      diff: r.diff || '초급',
      score: r.score === null || r.score === undefined ? null : Math.round(r.score * 10) / 10,
      today_score: r.today_score === null || r.today_score === undefined ? null : Math.round(r.today_score * 10) / 10,
      best: r.best === null || r.best === undefined ? null : Math.round(r.best * 10) / 10,
      avg: r.avg === null || r.avg === undefined ? null : Math.round(r.avg * 10) / 10,
      attempts: typeof r.attempts === 'number' ? r.attempts : null,
      next_target: r.next_target === null || r.next_target === undefined ? null : Math.round(r.next_target * 10) / 10,
      next_tier_cut: r.next_tier_cut === null || r.next_tier_cut === undefined ? null : r.next_tier_cut,
      history: Array.isArray(r.history) ? r.history.map((h) => ({ date: h.date, score: h.score })) : [],
      done: !!r.done,
      extra: !!r.extra,
      delta: typeof r.delta === 'number' ? r.delta : null
    }));
  }

  function applyRecs(summary) {
    const key = `${game}-${weapon}`;
    if (AL.state && AL.state.today_scores) TODAY_SCORES = AL.state.today_scores;
    RECS[key] = mapRecs(summary && summary.recs);
    RECS_MORE[key] = mapRecs(summary && summary.more);
    if (AL.state && AL.state.philosophy) PHILOSOPHY = AL.state.philosophy;
  }

  /* v1.3.0 — 기록 한 건의 종합 점수 (0~3,600) · 저장된 원점수를 게임 기준값으로 환산 */
  const GAME_REF = { kovak: 200, aimlab: 100000 };
  const AREA_KEYS = ['precision', 'wide', 'micro', 'track', 'switch'];
  function recordScore(rec) {
    const scores = (rec && rec.scores) || {};
    const ref = GAME_REF[rec && rec.game] || 200;
    let sum = 0, n = 0;
    AREA_KEYS.forEach((k) => {
      const v = Number(scores[k]);
      if (!isFinite(v) || v <= 0) return;
      sum += Math.min(100, (v / ref) * 100);
      n += 1;
    });
    return n ? Math.round((sum / n) * 36) : 0;
  }
  AL.recordScore = recordScore;

  let digestShown = false;
  function maybeShowDigest() {
    const d = AL.state && AL.state.digest;
    if (!d || !d.show || digestShown) return;
    if (AL.peek) return;
    digestShown = true;
    if (typeof renderDigest === 'function') renderDigest(d);
    const mask = $('#digest-mask');
    if (mask) mask.classList.add('show');
  }

  function applyState(st) {
    if (!st) return;
    AL.state = st;
    const meta = st.meta || {};
    const set = st.settings || {};
    AL.meta = meta;

    /* ---------- 상단 / 사이드바 ---------- */
    const nick = ((st.nexon || {}).nickname || set.nickname || '').trim();
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
    if (pillText) pillText.textContent = (st.nexon || {}).connected ? 'NEXON API 연결됨' : 'API 키 입력 필요';

    const sync = $('#sync-line');
    if (sync) {
      const cloud = st.cloud || {};
      sync.innerHTML = cloud.signed_in
        ? `<span class="pulse"></span>클라우드 로그인 · ${fmtLabel(cloud.last_sync) === '—' ? '동기화 전' : '마지막 ' + fmtLabel(cloud.last_sync)}`
        : `<span class="pulse"></span>기록 자동 저장 · ${fmtLabel(meta.now)}`;
    }

    const barracks = st.barracks || null;
    const tier = (barracks && barracks.tier) || null;
    const basic = (barracks && barracks.basic) || {};
    const clanEl = $('#pf-clan');
    if (clanEl) clanEl.textContent = basic.clan && basic.clan !== '-' ? `${basic.clan} 클랜` : '클랜 없음';

    if (tier) {
      const soloRp = Number(tier.solo.rp || 0);
      const band = bandOf(soloRp);
      const nextBand = SOLO_BANDS.find((b) => b.min > band.min);
      const span = nextBand ? nextBand.min - band.min : 200;
      const pct = nextBand ? Math.max(0, Math.min(100, ((soloRp - band.min) / span) * 100)) : 100;
      const rn = $('#pf-rank-name');
      if (rn) rn.textContent = `솔로 ${tier.solo.name}`;
      const rp = $('#pf-rp');
      if (rp) rp.textContent = `${n0(soloRp)} RP`;
      const bar = $('#pf-bar-i');
      if (bar) bar.style.width = `${pct.toFixed(1)}%`;
      const lo = $('#pf-cap-lo');
      if (lo) lo.textContent = n0(band.min);
      const hi = $('#pf-cap-hi');
      if (hi) hi.textContent = nextBand ? n0(nextBand.min) : 'MAX';
    } else {
      const rn = $('#pf-rank-name');
      if (rn) rn.textContent = '전적 미연동';
      const rp = $('#pf-rp');
      if (rp) rp.textContent = '언랭크';
    }

    /* ---------- 게임 / 무기 요약 ---------- */
    const summary = st.summaries?.[`${game}-${weapon}`] || null;
    if (summary) {
      VALUES = {};
      VALUES_RAW = {};
      AREAS.forEach((a) => {
        VALUES_RAW[a.key] = Number(summary.values?.[a.key] || 0);
        VALUES[a.key] = Math.round(VALUES_RAW[a.key]);
      });
      recalc();
      if (typeof summary.score === 'number') {
        score = summary.score;
        tierIdx = 0;
        TIERS.forEach((t, k) => { if (score >= t.cut) tierIdx = k; });
      }

      applyRecs(summary);
      const daily = summary.daily || [];
      TREND = {
        labels: daily.map((d) => mdLabel(d.date)),
        values: daily.map((d) => (d.score === null ? null : d.score))
      };
      const known = daily.filter((d) => d.score !== null).map((d) => d.score);
      SPARK = known.length ? known.slice(-7) : [0, 0];

      if (summary.style) {
        TRAIN_COUNT = { sniper: summary.style.sniper || 0, rifle: summary.style.rifle || 0 };
      }

      /* KPI 3 · 최근 7일 추세 */
      const delta = summary.week_delta;
      const dEl = $('#kpi-delta');
      if (dEl) {
        dEl.textContent = delta === null || delta === undefined ? '—' : `${delta > 0 ? '+' : ''}${n0(delta)}`;
        dEl.style.color = delta === null || delta === undefined ? '#8FA1B8' : delta >= 0 ? '#6EE7C0' : '#F7AFAE';
      }
      const tp = $('#trend-prev');
      if (tp) tp.innerHTML = `최근 7일 기록 <b>${known.length}일</b> · 훈련 <b>${summary.records || 0}회</b>`;
      const tb = $('#trend-best');
      if (tb) tb.innerHTML = `최고 <b>${known.length ? n0(Math.max(...known)) : '—'}</b>`;
      const trRange = $('#trend-range');
      if (trRange) trRange.textContent = daily.length ? `${mdLabel(daily[0].date)} → ${mdLabel(daily[daily.length - 1].date)}` : '—';
      const tFoot = $('#trend-foot');
      if (tFoot) {
        const avg = known.length ? Math.round(known.reduce((a, b) => a + b, 0) / known.length) : 0;
        tFoot.innerHTML = `일평균 <b>${n0(avg)}</b> · ${known.length}일 기록`;
      }
    }

    /* ---------- 연속 훈련 ---------- */
    const streak = st.streak || {};
    STREAK_STATE = streak;
    WEEK_STATE = st.week || null;
    if (st.condition) HOME_COND = st.condition;
    const chipStreak = $('#chip-streak');
    if (chipStreak) chipStreak.textContent = `${streak.current || 0}일`;
    const kStreak = $('#kpi-streak');
    if (kStreak) kStreak.textContent = streak.current || 0;
    const best = $('#streak-best');
    if (best) best.textContent = `${streak.best || 0}일`;
    const week = $('#streak-week');
    if (week) week.textContent = `${streak.week || 0}/7`;
    const sbar = $('#streak-bar');
    if (sbar) sbar.style.width = `${Math.min(100, ((streak.week || 0) / 7) * 100).toFixed(1)}%`;
    const dots = $('#kpi-dots');
    if (dots && Array.isArray(streak.days7)) {
      dots.innerHTML = streak.days7.map((v, i) => `<span class="${v ? 'f' : i === 6 ? 't' : ''}"></span>`).join('');
    }
    const doneToday = (currentSummary()?.recs || []).filter((r) => r.done).length;
    const chipToday = $('#chip-today');
    if (chipToday) chipToday.textContent = doneToday;
    const dates = new Set((st.records || []).map((r) => r.date));
    const cumul = $('#hd-cumul');
    if (cumul) cumul.textContent = `누적 ${dates.size}일 · 연속 ${streak.current || 0}일`;
    const sbadge = $('#streak-badge');
    if (sbadge) sbadge.innerHTML = `훈련 <b>${(st.record_count || 0)}회</b>`;

    /* ---------- 인게임 지표 (병영수첩) ---------- */
    const stats = (barracks && barracks.stats) || {};
    const kd = $('#kpi-kd');
    if (kd) kd.textContent = stats.games ? n0(stats.kd, 1) : '—';
    const kdBar = $('#kd-bar');
    if (kdBar) kdBar.style.width = `${Math.min(100, Number(stats.kd || 0))}%`;
    const dmg = $('#kpi-dmg');
    if (dmg) dmg.textContent = stats.games ? n0(stats.dmg) : '—';
    const gWin = $('#game-win');
    if (gWin) gWin.innerHTML = stats.games
      ? `승률 <b>${n0(stats.win, 1)}%</b> · ${n0(stats.wins)}승 ${n0((stats.games || 0) - (stats.wins || 0))}패`
      : '전적을 불러오면 표시됩니다';
    const gDiff = $('#game-diff');
    if (gDiff) gDiff.textContent = stats.games ? `최근 ${stats.games}판` : '—';
    const gFoot = $('#game-foot');
    if (gFoot) gFoot.innerHTML = stats.games ? `K/D <b>${n0(stats.kd, 1)}%</b> · 딜량 <b>${n0(stats.dmg)}</b>` : '—';
    const gRange = $('#game-range');
    if (gRange) gRange.textContent = stats.games ? '최근 100판 랭크전' : '전적 미연동';

    /* ---------- 목표 / 맵 ---------- */
    GOALS = (st.goals || []).map((g) => ({
      label: g.label, target: g.target || '', cur: g.cur, tgt: g.tgt, unit: g.unit || '',
      gap: g.gap, gaptxt: g.gaptxt
    }));
    MAPS = (barracks && barracks.maps) || [];

    /* ---------- 순위표 ---------- */
    const members = (st.leaderboard || []).map((m) => ({ ...m }));
    if (!members.length && nick) {
      members.push({ name: nick, me: true, clan: basic.clan || '친구', weapon: 'sniper', solo: 0, party: null, kd: null, dmg: null, win: null });
    }
    MEMBERS = members;

    /* ---------- 인게임 추이 (최근 20판 → 날짜별) ---------- */
    const matches = (barracks && barracks.matches) || [];
    if (matches.length) {
      const byDate = new Map();
      matches.forEach((m) => {
        if (!byDate.has(m.date)) byDate.set(m.date, []);
        byDate.get(m.date).push(m);
      });
      const keys = Array.from(byDate.keys()).sort().slice(-7);
      GAME_TREND = {
        labels: keys.map((k) => String(k).slice(5).replace('-', '.')),
        kd: keys.map((k) => {
          const rows = byDate.get(k);
          return Math.round((rows.reduce((a, r) => a + Number(r.kd || 0), 0) / rows.length) * 10) / 10;
        }),
        dmg: keys.map((k) => {
          const rows = byDate.get(k).filter((r) => r.damage !== null && r.damage !== undefined);
          return rows.length ? Math.round(rows.reduce((a, r) => a + Number(r.damage), 0) / rows.length) : null;
        })
      };
    } else {
      GAME_TREND = { labels: [], kd: [], dmg: [] };
    }

    /* ---------- 유틸 상태 ---------- */
    const utils = st.utils || {};
    const nv = utils.mode;
    UTIL.rifle = nv === 'rifle';
    UTIL.sniper = nv === 'sniper';
    UTIL.alias = !!utils.alias;
    if (typeof utils.accel === 'boolean') UTIL.accel = utils.accel;
    UTIL.backups = (utils.backups || []).length;
    AL.utils = utils;

    const backupCount = $('#util-backup-count');
    if (backupCount) backupCount.textContent = UTIL.backups;
    const backupFoot = $('#util-backup-foot');
    if (backupFoot) backupFoot.textContent = UTIL.backups;
    const bkRecords = $('#bk-records');
    if (bkRecords) bkRecords.textContent = `${n0(st.record_count || 0)}회`;
    const bkMembers = $('#bk-members');
    if (bkMembers) bkMembers.textContent = `${n0((st.leaderboard || []).length)}명`;
    const lbMe = $('#lb-me-name');
    if (lbMe) lbMe.textContent = nick || '—';

    /* ---------- v1.3.0 · 티어 분포 · 병영수첩 변화 · 맵 가이드 · 기록 표 ---------- */
    if (st.tier_distribution) TIER_DIST = st.tier_distribution;
    if (st.pbs) AL.pbs = st.pbs;
    if (st.memos) { MEMOS = st.memos; AL.memos = st.memos; }
    if (Array.isArray(st.tier_details)) TIER_DETAILS = st.tier_details;
    if (Array.isArray(st.map_guides)) {
      MAP_GUIDES = {};
      st.map_guides.forEach((g) => { if (g && g.map) MAP_GUIDES[g.map] = g; });
    }
    if (st.digest) DIGEST = st.digest;
    RECORDS_ROWS = (st.records || []).map((r) => ({
      ...r,
      score: recordScore(r)
    }));

    /* ---------- 다시 그리기 ---------- */
    if (typeof refreshAll === 'function') refreshAll();
    /* recalc() 는 5영역 평균×36 으로 점수를 다시 계산하므로, 서버가 계산한 점수로 덮어쓴다 */
    if (summary && typeof summary.score === 'number') {
      score = summary.score;
      tierIdx = 0;
      TIERS.forEach((t, k) => { if (score >= t.cut) tierIdx = k; });
      drawKpis(); drawRadar(); drawLadder(); drawTierTable();
    }

    const radarWeak = $('#radar-weak');
    if (radarWeak && VALUES) {
      const keys = AREAS.map((a) => a.key);
      const weakest = keys.reduce((a, b) => (VALUES[a] <= VALUES[b] ? a : b), keys[0]);
      const area = AREAS.find((a) => a.key === weakest);
      radarWeak.innerHTML = `가장 약한 영역 · <b>${area.name} ${VALUES[weakest]}</b>`;
    }
    paintSummaryExtras(summary);

    renderUtilsState(utils);
    if (AL.peek) renderPeek();
    else renderBarracks(barracks, st);
    renderSettings(st);
    renderCloudState(st);
    renderInsights(st);
    renderUtilRecent(st);
    if (typeof compactGrayNotes === 'function') compactGrayNotes();
    renderUpdate(st.update);
    renderTray(st.tray);
    maybeShowDigest();
    if (typeof drawHome === 'function') drawHome();
    maybeShowOnboarding(st);
    maybeShowUpdateGate(st);
    startTiltPoll();
  }
  AL.applyState = applyState;

  /* ==================================================================
     대시보드 · 기타 렌더 보조
     ================================================================== */
  function paintSummaryExtras(summary) {
    if (!summary) return;
    const weak = $('#radar-weak');
    if (weak) {
      const keys = AREAS.map((a) => a.key);
      const w = keys.reduce((a, b) => (VALUES[a] <= VALUES[b] ? a : b), keys[0]);
      const area = AREAS.find((a) => a.key === w);
      weak.innerHTML = `가장 약한 영역 · <b>${area.name} ${VALUES[w]}</b>`;
    }
    const chipToday = $('#chip-today');
    if (chipToday) chipToday.textContent = (summary.recs || []).filter((r) => r.done).length;
    const recChip = $('#rec-delta-chip');
    if (recChip) {
      const d = summary.week_delta;
      recChip.innerHTML = d === null || d === undefined ? '최근 7일 <b>—</b>'
        : `최근 7일 <b>${d > 0 ? '+' : ''}${n0(d)}</b>`;
    }
    const recRange = $('#rec-date-range');
    if (recRange && (summary.daily || []).length) {
      const daily = summary.daily;
      recRange.textContent = `${mdLabel(daily[0].date)} ~ ${mdLabel(daily[daily.length - 1].date)}`;
    }
    const delta = summary.week_delta;
    const dEl = $('#kpi-delta');
    if (dEl) {
      dEl.textContent = delta === null || delta === undefined ? '—' : `${delta > 0 ? '+' : ''}${n0(delta)}`;
      dEl.style.color = delta === null || delta === undefined ? '#8FA1B8' : delta >= 0 ? '#6EE7C0' : '#F7AFAE';
    }
    const known = (summary.daily || []).filter((d) => d.score !== null).map((d) => d.score);
    const tp = $('#trend-prev');
    if (tp) tp.innerHTML = `최근 7일 기록 <b>${known.length}일</b> · 훈련 <b>${summary.records || 0}회</b>`;
    const tb = $('#trend-best');
    if (tb) tb.innerHTML = `최고 <b>${known.length ? n0(Math.max(...known)) : '—'}</b>`;
  }

  function renderUtilsState(utils) {
    if (utils && utils.last_changes) LAST_CHANGES = utils.last_changes;
    const points = (utils.points || []);
    const box = $('#nv-points');
    if (box) {
      box.innerHTML = points.length
        ? points.map((p) => `<div class="gi"><b>${String(p.at || '').slice(11, 19) || '—'}</b>${p.label || '설정 변경'}<i>복구 가능</i></div>`).join('')
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
    const name = (tierRow || {}).name || band.n;
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
      <div class="rank-emb"><img src="assets/${band.img}.png" alt="${name}"></div>
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
        <div class="ic">${t.icon || '🧭'}</div>
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
    const band = bandOf(Number(tier.solo.rp || 0));
    /* v1.3.1 — 무기 표기는 '스나' / '라플' 만 (앞의 '주무기 ' 제거) */
    const weaponLabel = Number((barracks.recent || {}).sniper || 0) >= Number((barracks.recent || {}).assault || 0)
      ? '스나' : '라플';
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
        <div class="emb-bg"><img src="assets/${band.img}.png" alt=""></div>
        <div class="bn-left">
          <div class="bn-ava">${initial}<i></i></div>
          <div>
            <div class="bn-name"><h2 class="bn-self" role="button" tabindex="0" data-ouid="${esc(barracks.ouid || '')}"
                title="내 병영수첩 상세 페이지 열기">${barracks.nickname}</h2>
              <span class="tag ${weaponLabel === '스나' ? 'amber' : 'cyan'}" id="bn-user">${weaponLabel}</span>${flowBadge}</div>
            <div class="bn-sub">
              <a class="bn-clan" role="button" tabindex="0" data-clan="${esc(clan)}" data-ouid="${esc(barracks.ouid || '')}"
                title="${esc(clan)} · 클랜 홈페이지 열기">${esc(clan)}</a>
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
          <td class="l map">${m.map}</td>
          <td>${m.type}</td>
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
      <input id="${id}" class="inp" value="${value == null ? '' : String(value).replace(/"/g, '&quot;')}"
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
    const goals = set.goals || {};
    const meta = (st && st.meta) || {};
    const count = (st && st.record_count) || 0;

    /* ---------- 카드 1 · NEXON API · 병영수첩 ---------- */
    const api = $('#set-api');
    if (api) {
      api.innerHTML = `
        <div class="card-hd" style="padding:0">
          <div><div class="card-title"><span class="bar"></span>넥슨 API · 서든 닉네임</div>
            <div class="card-sub">병영수첩 · 친구 순위표 · 목표 4종</div></div>
          <span class="ustate ${nexon.connected ? 'on' : 'warn'}">${nexon.connected ? '키 등록됨' : '키 필요'}</span>
        </div>
        ${inputRow('set-nick', '서든어택 닉네임', set.nickname || '', { maxlength: 12 })}
        <div class="field"><label>NEXON Open API 키</label>
          <input id="set-key" class="inp" type="text" value="${nexon.connected ? esc(set.api_key_masked || '등록됨') : ''}"
            placeholder="발급: open.api.nexon.com">
          
        </div>
        
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px">
          ${inputRow('set-goal-win', '승률 %', goals.win ?? 55, { type: 'number' })}
          ${inputRow('set-goal-kd', 'K/D %', goals.kd ?? 60, { type: 'number' })}
          ${inputRow('set-goal-dmg', '딜량', goals.dmg ?? 1400, { type: 'number' })}
          ${inputRow('set-goal-rp', '솔로 RP', goals.rp ?? 4000, { type: 'number' })}
        </div>
        <div class="set-actions">
          <button class="btn btn-primary" id="set-api-save">저장</button>
          <button class="btn btn-ghost" id="set-api-test">저장 후 전적 확인</button>
          <button class="btn btn-ghost" id="set-goal-save">목표 저장</button>
        </div>`;
    }

    /* ---------- 카드 2 · 실행 · 코박스 폴더 ---------- */
    const run = $('#set-run');
    if (run) {
      run.innerHTML = `
        <div class="card-hd" style="padding:0">
          <div><div class="card-title"><span class="bar"></span>실행 · 코박스 점수</div>
            <div class="card-sub">트레이너 실행 경로 · stats 폴더</div></div>
          <span class="chip" style="height:20px;font-size:9.5px">${meta.steam ? 'Steam 감지' : 'Steam 미감지'}</span>
        </div>
        ${inputRow('set-kovak', "KovaaK's 경로", set.kovak_path || '')}
        ${inputRow('set-aimlab', 'Aim Lab 경로', set.aimlab_path || '')}
        ${inputRow('set-kovak-stats', '코박스 stats 폴더 (선택)', set.kovak_stats_path || '')}
        <div class="set-note">비워 두면 <b>Steam 기본 경로</b>로 실행하고, 코박스 통계는 <b>자동 탐색</b>합니다 · 📥 코박스 점수 인식에 사용</div>
        ${switchRow('auto_launch', '추천 시나리오 자동 실행', '▶ 버튼으로 Steam 원클릭 실행', !!set.auto_launch)}
        ${switchRow('autosave', '점수 자동 저장', '입력 후 저장 버튼 없이 기록', !!set.autosave)}
        ${switchRow('kovak_autofill', '코박스 점수 자동 채움', '앱으로 돌아올 때 오늘 플레이 기록을 자동 저장', !!set.kovak_autofill)}
        ${switchRow('tilt_watch', '연패 브레이크 알림', '서든 실행 중 3연패(주의) · 5연패(위험) 감지', set.tilt_watch !== false)}
        <div class="set-actions">
          <button class="btn btn-primary" id="set-run-save">저장</button>
          <button class="btn btn-ghost" id="set-open-folder">기록 폴더 열기</button>
        </div>`;
    }

    /* ---------- 카드 3 · 클라우드 ---------- */
    const cl = $('#set-cloud');
    if (cl) {
      const useDefault = !(set.supabase || {}).url || (set.supabase || {}).url === (cloud.url || '');
      cl.innerHTML = `
        <div class="card-hd" style="padding:0">
          <div><div class="card-title"><span class="bar"></span>클라우드 저장소</div>
            <div class="card-sub">다른 PC 에서도 기록이 이어집니다</div></div>
          <span class="ustate ${cloud.signed_in ? 'on' : cloud.configured ? 'warn' : ''}">${cloud.signed_in ? '로그인됨' : cloud.configured ? '로그인 전' : '설정 준비됨'}</span>
        </div>
        <div class="set-note" style="margin:0">
          어디서나 <b>내 에임 기록과 순위표</b>를 이어서 쓰는 저장소입니다.
          ${cloud.signed_in ? `☁️ ${cloud.email || ''} · 마지막 동기화 ${fmtLabel(cloud.last_sync)}` : '로그인 전 · 이 PC 에는 기록이 자동 저장되고 있습니다'}
        </div>
        <div class="set-actions">
          ${cloud.signed_in
            ? '<button class="btn btn-ghost" id="set-cloud-out">로그아웃</button>'
            : '<button class="btn btn-primary" id="set-cloud-login">로그인 / 회원가입</button>'}
          <button class="btn btn-cyan" id="set-cloud-push">☁️ 올리기</button>
          <button class="btn btn-ghost" id="set-cloud-pull">내려받기</button>
        </div>
        <details id="set-cloud-adv" style="margin-top:2px">
          <summary style="cursor:pointer;font-size:10px;font-weight:700;color:#7E8FA6;list-style:none;outline:none">
            ⚙ 고급 서버 설정 ${useDefault ? '· 기본 서버 사용 중' : '· 직접 입력됨'}
          </summary>
          <div style="display:flex;flex-direction:column;gap:6px;margin-top:6px">
            ${inputRow('set-sb-url', '프로젝트 URL', cloud.url || '')}
            ${inputRow('set-sb-key', '공개 키 (publishable / anon)', '', { type: 'password' })}
            <button class="btn btn-ghost" id="set-cloud-check" style="align-self:flex-start;height:24px;font-size:10px">서버 연결 확인</button>
          </div>
        </details>`;
    }

    /* ---------- 카드 4 · 트레이 · 업데이트 ---------- */
    const more = $('#set-more');
    if (more) {
      more.innerHTML = `
        <div class="card-hd" style="padding:0">
          <div><div class="card-title"><span class="bar"></span>앱 · 트레이 · 업데이트</div>
            <div class="card-sub">단축키 · 새 버전</div></div>
          <span class="chip" style="height:20px;font-size:9.5px">v${meta.version || '1.3.1'}</span>
        </div>
        ${switchRow('hotkey_enabled', 'Ctrl+Shift+A 창 부르기', '어디서든 단축키로 창을 숨기기/보이기', set.hotkey_enabled !== false)}
        ${switchRow('tilt_watch', '연승·연패 트레이 알림', '3연승(🔥) · 3~4연패(⚠️) · 5연패(🚨) 트레이 풍선 + 앱 알림', set.tilt_watch !== false)}
        <div class="set-note">창 닫기(<b>X</b>)는 항상 <b>트레이로 숨기기 / 프로그램 종료</b> 확인창을 띄웁니다.</div>
        <div class="set-status" id="set-tray-state" style="font-size:9.5px">트레이 상태 확인 중…</div>
        <div class="set-status" id="set-update-info" style="font-size:9.5px">현재 v${meta.version || '1.3.1'} · 업데이트를 확인해 보세요</div>
        <div class="set-actions">
          <button class="btn btn-primary" id="set-update-check">업데이트 확인</button>
          <button class="btn btn-ghost" id="set-tray-hide">트레이로 숨기기</button>
          <button class="btn btn-ghost" id="set-backup">백업 파일 저장</button>
        </div>
        <div class="set-note" title="${meta.data_file || ''}" style="font-size:9px;color:#5E6E85">데이터 <b>${meta.data_file || '—'}</b>${meta.is_admin ? ' · 관리자' : ''}${meta.load_error ? ' · ' + meta.load_error : ''}</div>`;
    }

    /* ---------- 카드 5 · 점수 전체 초기화 (위험 구역) ---------- */
    const data = $('#set-data');
    if (data) {
      data.innerHTML = `
        <div class="card-hd" style="padding:0">
          <div><div class="card-title"><span class="bar" style="background:linear-gradient(180deg,#F87171,#DC2626)"></span>점수 전체 초기화 · <span style="color:#F7AFAE;font-size:10px">되돌릴 수 없음</span></div>
            <div class="card-sub">에임 연습 점수 · 시나리오 PB 만 지웁니다</div></div>
        </div>
        <div class="set-note">
          훈련 기록 <b>${n0(count)}</b>건과 시나리오 PB 가 사라집니다. 훈련 잔디(출석·훈련 일수) · API 키 · 닉네임 · 친구 목록 · 유틸 설정은 <b>그대로 유지</b>됩니다.
        </div>
        <button class="danger-btn" id="set-reset-scores" style="padding:0 16px;flex:0 0 auto">점수 전체 초기화</button>`;
    }

    const metaLine = $('#set-meta-line');
    if (metaLine) {
      metaLine.textContent = `v${meta.version || '1.4.0'} · 훈련 기록 ${n0(count)}회`;
    }
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
        ? `프로젝트 <b>${String(cloud.url || '').replace('https://', '')}</b> · 10명 순위표와 훈련 기록이 계정에 저장되어 다른 PC 에서 이어집니다.`
        : '앱에 기본 클라우드 서버가 이미 들어 있습니다 · 이메일로 로그인하면 다른 PC 와 기록이 이어집니다.';
    }
  }

  function collectSettings() {
    const read = (id) => {
      const el = $('#' + id);
      return el ? String(el.value || '').trim() : '';
    };
    const patch = {};
    const nick = read('set-nick');
    const key = read('set-key');
    const kovak = read('set-kovak');
    const aim = read('set-aimlab');
    if (nick) patch.nickname = nick;
    if (key) patch.api_key = key;
    if (kovak !== '') patch.kovak_path = kovak;
    if (aim !== '') patch.aimlab_path = aim;
    const statsEl = $('#set-kovak-stats');
    if (statsEl) patch.kovak_stats_path = String(statsEl.value || '').trim();
    return patch;
  }

  /* ==================================================================
     점수 입력 모달
     ================================================================== */
  function openScoreModal() {
    const mask = $('#score-mask');
    const body = $('#score-body');
    const summary = currentSummary();
    if (!mask || !body) return;
    const recs = (summary && summary.recs) || [];
    if (!recs.length) {
      toast('추천 시나리오를 먼저 불러와 주세요', false);
      return;
    }
    body.innerHTML = recs.map((r, i) => `
      <div class="score-row" data-row="${i}">
        <div><div class="nm">${i + 1}. ${r.name}</div><div class="ar">${(AREAS[r.area] || AREAS[0]).name} · ${r.diff || '초급'}</div></div>
        <input type="number" min="0" step="any" inputmode="decimal" value=""
          placeholder="${r.score === null || r.score === undefined ? '' : '최근 ' + r.score}"
          data-name="${String(r.name).replace(/"/g, '&quot;')}" data-area="${(AREAS[r.area] || AREAS[0]).key}">
        <div class="unit">점</div>
      </div>`).join('');
    const title = $('#score-title');
    if (title) title.textContent = `${game === 'kovak' ? "KovaaK's" : 'Aim Lab'} · ${weapon === 'sniper' ? '스나' : '라플'} 오늘의 5칸 점수`;
    mask.classList.add('show');
    const first = body.querySelector('input');
    if (first) setTimeout(() => first.focus(), 60);
  }

  async function saveScores() {
    const rows = $$('#score-body .score-row');
    const scenarios = [];
    for (const row of rows) {
      const input = row.querySelector('input');
      if (!input) continue;
      const raw = String(input.value || '').trim();
      if (raw === '') continue;
      const value = Number(raw.replace(/,/g, ''));
      if (Number.isNaN(value) || value < 0) {
        toast(`${input.dataset.name} 점수는 0 이상의 숫자로 입력해 주세요`, false);
        input.focus();
        return;
      }
      scenarios.push({ name: input.dataset.name, area_key: input.dataset.area, score: value });
    }
    if (!scenarios.length) {
      toast('점수를 1개 이상 입력해 주세요', false);
      return;
    }
    const btn = $('#score-save');
    if (btn) { btn.disabled = true; btn.textContent = '저장 중…'; }
    busy(true, '훈련 기록을 저장하는 중…');
    const result = await call('save_training_session', {
      game, weapon,
      date: (AL.meta && AL.meta.today) || null,
      scenarios
    });
    busy(false);
    if (btn) { btn.disabled = false; btn.textContent = '기록 저장'; }
    if (!result || !result.ok) return fail(result, '기록을 저장하지 못했습니다');
    const mask = $('#score-mask');
    if (mask) mask.classList.remove('show');
    if (result.state) applyState(result.state);
    const sm = currentSummary();
    toast(`✅ 기록 저장 · 종합 ${n0(sm ? sm.score : 0)}점 · ${scenarios.length}개 시나리오`, true);
  }

  /* ==================================================================
     시나리오 모달 — 내 점수 입력/수정 + 게임에서 재생 (한 곳에서)
     ================================================================== */
  let SCEN = null;

  function findRec(name) {
    const key = `${game}-${weapon}`;
    return (RECS[key] || []).find((r) => r.name === name)
        || (RECS_MORE[key] || []).find((r) => r.name === name)
        || null;
  }

  function openScenario(name, areaKey) {
    if (!name) return;
    const rec = findRec(name) || AL.findScenario(name);
    SCEN = {
      name,
      areaKey: (rec && rec.areaKey) || areaKey || 'precision',
      trainer: game,
      weapon
    };
    const area = AREAS.find((a) => a.key === SCEN.areaKey) || AREAS[0];
    const title = $('#scen-title');
    if (title) title.textContent = name;
    const areaEl = $('#scen-area');
    if (areaEl) { areaEl.textContent = area.name; areaEl.className = `area ${area.cls}`; }
    const diff = $('#scen-diff');
    if (diff) diff.textContent = (rec && rec.diff) || '초급';
    const trainer = $('#scen-trainer');
    if (trainer) trainer.textContent = `${game === 'kovak' ? "KovaaK's" : 'Aim Lab'} · ${weapon === 'sniper' ? '스나' : '라플'}`;
    const today = rec ? rec.today_score : null;
    const setText = (id, value) => { const el = $('#' + id); if (el) el.textContent = value; };
    setText('scen-today', today === null || today === undefined ? '미기록' : n0(today));
    setText('scen-last', rec && rec.score !== null && rec.score !== undefined ? n0(rec.score) : '—');
    setText('scen-best', rec && rec.best !== null && rec.best !== undefined ? n0(rec.best) : '—');
    if (typeof window.drawScenarioHistory === 'function') {
      window.drawScenarioHistory((rec && rec.history && rec.history.length) ? rec : null);
    }
    const note = $('#scen-note');
    if (note) {
      note.innerHTML = today === null || today === undefined
        ? '<b>통합 점수 · 5영역 · 티어</b>에 저장 즉시 반영됩니다'
        : `오늘 이미 <b>${n0(today)}점</b> · 값을 고쳐 저장하면 오늘 기록이 갱신됩니다`;
    }
    const input = $('#scen-score');
    if (input) {
      input.value = today === null || today === undefined ? '' : String(today);
      input.classList.remove('bad');
    }
    const mask = $('#scen-mask');
    if (mask) mask.classList.add('show');
    if (input) setTimeout(() => { input.focus(); try { input.select(); } catch (e) {} }, 70);
  }
  AL.openScenario = openScenario;

  /* 레코드에 없는(라이브러리에서 직접 누른) 시나리오도 열 수 있게 */
  AL.findScenario = function (name) {
    const st = AL.state;
    const rows = st && st.scenarios && st.scenarios[`${game}-${weapon}`];
    const hit = (rows || []).find((r) => r.name === name);
    if (!hit) return null;
    return { name, areaKey: hit.area_key || hit.area, diff: '초급', today_score: null, score: null, best: null };
  };

  async function saveScenario() {
    if (!SCEN) return;
    const input = $('#scen-score');
    const raw = String((input && input.value) || '').trim().replace(/,/g, '');
    if (raw === '') { if (input) input.focus(); return toast('점수를 입력해 주세요', false); }
    const value = Number(raw);
    if (Number.isNaN(value) || value < 0) {
      if (input) { input.classList.add('bad'); input.focus(); }
      return toast('0 이상의 숫자로 입력해 주세요', false);
    }
    if (!AL.real) return toast('앱(.exe)에서 실행하면 저장됩니다', false);
    const btn = $('#scen-save');
    if (btn) { btn.disabled = true; btn.textContent = '저장 중…'; }
    busy(true, `${SCEN.name} 점수를 저장하는 중…`);
    const result = await call('save_training_session', {
      game: SCEN.trainer,
      weapon: SCEN.weapon,
      date: (AL.meta && AL.meta.today) || null,
      scenarios: [{ name: SCEN.name, area_key: SCEN.areaKey, score: value }]
    });
    busy(false);
    if (btn) { btn.disabled = false; btn.textContent = '점수 저장'; }
    if (!result || !result.ok) return fail(result, '점수를 저장하지 못했습니다');
    if (result.state) applyState(result.state);
    const s = currentSummary();
    toast(`✅ ${SCEN.name} ${n0(value)}점 저장 · 통합 ${n0(s ? s.score : 0)}점`, true);
    const mask = $('#scen-mask');
    if (mask) mask.classList.remove('show');
  }

  async function morePractice() {
    const label = `${game === 'kovak' ? "KovaaK's" : 'Aim Lab'} · ${weapon === 'sniper' ? '스나' : '라플'}`;
    if (!AL.real) return toast('앱(.exe)에서 실행하면 더 연습하기가 열립니다', false);
    busy(true, `${label} 약점 보완 시나리오를 고르는 중…`);
    const result = await call('recommend_more', game, weapon, 3);
    busy(false);
    if (!result || !result.ok) return fail(result, '더 연습할 시나리오를 가져오지 못했습니다');
    if (result.state) applyState(result.state);
    const count = (result.recs || []).length;
    if (!count) return toast(result.message || '더 추천할 시나리오가 없습니다', false);
    /* 추가된 시나리오가 바로 보이게 목록을 살짝 내린다 */
    const list = $('#rec-list');
    const sep = list && list.querySelector('.rec-sep');
    if (list && sep) list.scrollTop = Math.max(0, sep.offsetTop - list.offsetTop - 2);
    toast(`➕ ${label} 약점 보완 ${count}종 추가 · 클릭해서 점수 기록`, true);
  }

  /* ==================================================================
     실행 (Steam · Aim Lab)
     ================================================================== */
  function scenarioUrl(name) {
    if (game === 'aimlab') return name ? `aimlab://scenario/${encodeURIComponent(name)}` : 'steam://run/714010';
    return name ? `steam://run/824270/?action=jump-to-scenario;name=${name}` : 'steam://run/824270';
  }

  async function launch(name, opts) {
    const url = scenarioUrl(name);
    const result = await call('launch_trainer', url);
    if (!result || !result.ok) return fail(result, '실행 앱을 찾지 못했습니다 · Steam 설치를 확인해 주세요');
    if (!(opts && opts.quiet)) toast(`▶ ${name ? name : (game === 'kovak' ? "KovaaK's" : 'Aim Lab')} 실행`, true);
    return result;
  }

  async function launchAll() {
    const recs = (currentSummary() && currentSummary().recs) || [];
    if (!recs.length) return toast('추천 시나리오가 없습니다', false);
    const ordered = recs.filter((r) => !r.done).concat(recs.filter((r) => r.done));
    toast(`▶ ${ordered.length}종 연속 실행 · Steam 이 순서대로 열립니다`, true);
    for (const r of ordered) {
      await launch(r.name, { quiet: true });
      await new Promise((res) => setTimeout(res, 1200));
    }
  }

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
        if (typeof showScreen === 'function') showScreen('settings');
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
    toast(result.message || `🔄 ${names.length}명 전적 일괄 갱신 완료`, true);
  }

  /* ==================================================================
     병영수첩 · 닉네임 검색 (임시 조회)
     ================================================================== */
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  function bandImg(rp) {
    try { return (bandOf(Number(rp) || 0) || { img: 'solo_100' }).img || 'solo_100'; }
    catch (e) { return 'solo_100'; }
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
        <div class="emb"><img src="assets/${bandImg(u.solo)}.png" alt="${esc(u.solo_tier || '')}"></div>
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
    busy(true, '원래 설정으로 복구 중…');
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
    toast(result.message || '↩️ 원래 설정으로 복구했습니다', true);
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
     v1.2.0 — 인사이트(잔디·주간목표·훈련 전후) · 업데이트 · 트레이 · 코박스 인식
     · 5분 워밍업 · 리포트 카드 · 1:1 비교 · 전원/게임 모드 · 플레이리스트
     ================================================================== */
  function renderInsights(st) {
    try {
      if (st.heatmap && typeof window.drawHeatmap === 'function') window.drawHeatmap(st.heatmap);
      else if (typeof window.drawHeatmap === 'function' && !AL.real) window.drawHeatmap(sampleHeatmap());
      if (st.week && typeof window.drawWeek === 'function') window.drawWeek(st.week);
      if (st.impact && typeof window.drawImpact === 'function') window.drawImpact(st.impact);
    } catch (err) { /* 인사이트는 화면 장식 — 실패해도 앱은 정상 동작 */ }
  }

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

  /* ------------------------------------------------------------ 코박스 점수 인식 */
  function fillScoreFromKovak(matched) {
    let filled = 0;
    (matched || []).forEach((m) => {
      const input = Array.from(document.querySelectorAll('#score-body .score-row input'))
        .find((el) => el.dataset.name === m.name);
      if (input) { input.value = String(m.score); filled += 1; }
    });
    return filled;
  }

  async function saveKovakMatches(matched, auto) {
    const scenarios = (matched || []).map((m) => {
      const rec = findRec(m.name) || AL.findScenario(m.name) || {};
      return { name: m.name, area_key: rec.areaKey || 'precision', score: m.score };
    });
    if (!scenarios.length) return;
    busy(true, '코박스 점수를 저장하는 중…');
    const result = await call('save_training_session', {
      game, weapon, date: (AL.meta && AL.meta.today) || null, scenarios,
      note: auto ? '코박스 stats 자동 인식' : '코박스 stats 불러오기'
    });
    busy(false);
    if (!result || !result.ok) return fail(result, '코박스 점수를 저장하지 못했습니다');
    if (result.state) applyState(result.state);
    const base = `📥 코박스 점수 ${scenarios.length}종 저장 완료`;
    if (result.pbs && result.pbs.length) toastPbs(result.pbs, base);
    else toast(base, true);
  }

  async function importKovak(auto) {
    if (!AL.real) return toast('앱(.exe)에서 코박스 점수를 읽을 수 있습니다', false);
    const summary = currentSummary() || {};
    const names = []
      .concat((summary.recs || []).map((r) => r.name))
      .concat((summary.more || []).map((r) => r.name));
    if (!names.length) return toast('오늘 추천 5종을 먼저 불러와 주세요', false);
    const result = await call('import_kovak_scores', names, 1);
    if (!result || !result.ok) {
      if (!auto) fail(result, '코박스 점수를 읽지 못했습니다');
      return;
    }
    const matched = result.matched || [];
    if (!matched.length) {
      if (!auto) toast(result.message || '오늘 추천 과제와 겹치는 코박스 기록이 없습니다', false, 5200);
      return;
    }
    const mask = $('#score-mask');
    if (mask && mask.classList.contains('show')) {
      const filled = fillScoreFromKovak(matched);
      return toast(`📥 코박스 점수 ${filled}칸을 채웠습니다 · 저장을 눌러 주세요`, true);
    }
    await saveKovakMatches(matched, !!auto);
  }

  function toastPbs(pbs, base) {
    const list = pbs || [];
    if (!list.length) { if (base) toast(base, true); return; }
    const first = list[0];
    const prev = (first.prev === null || first.prev === undefined) ? '첫 기록' : n0(first.prev);
    const more = list.length > 1 ? ` 외 ${list.length - 1}종` : '';
    toast(`🏆 NEW PB! ${first.name} ${n0(first.score)}점 (이전 ${prev})${more}`, true, 5000);
  }

  /* ------------------------------------------------------------ 5분 워밍업 */
  /* ---------- 오늘 훈련 시작 팝업 (바로 Steam 을 켜지 않는다) ---------- */
  function openToday() {
    const mask = $('#today-mask');
    const list = $('#today-list');
    const rows = (typeof RECS !== 'undefined' && RECS[`${game}-${weapon}`]) || [];
    if (list) {
      if (!rows.length) {
        list.innerHTML = '<div class="empty">추천을 계산하려면 훈련 기록이 필요합니다 · 시나리오에서 첫 점수를 남겨 주세요</div>';
      } else {
        list.innerHTML = rows.map((r, i) => {
          const area = (typeof AREAS !== 'undefined' ? AREAS[r.area] : null) || { name: '', cls: '' };
          const today = recTodayScore(r);
          return `<div style="display:grid;grid-template-columns:20px minmax(0,1fr) 74px 54px 54px;gap:8px;align-items:center;padding:7px 9px;border-radius:8px;background:${r.done ? 'rgba(52,211,153,.07)' : 'rgba(7,11,19,.5)'};border:1px solid ${r.done ? 'rgba(52,211,153,.26)' : 'rgba(148,163,184,.12)'}">
            <div class="num" style="color:${r.done ? '#34D399' : '#F59E0B'};font-weight:800;font-size:11px;text-align:center">${r.done ? '✓' : i + 1}</div>
            <div style="min-width:0"><div style="font-size:11.5px;font-weight:700;color:#EAF1FB;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${r.name}</div>
              <div style="font-size:8.5px;color:#8496AE;font-weight:600">${area.name} · ${r.diff || '초급'}</div></div>
            <div style="font-family:var(--num);font-size:10.5px;text-align:right;color:${today == null ? '#5E6E85' : '#FDE8BD'}">${today == null ? '미기록' : Number(today).toLocaleString('ko-KR')}</div>
            <button class="btn-tiny" data-today-score="${esc(r.name)}" title="이 시나리오 점수 입력">점수</button>
            <button class="btn-tiny" data-today-run="${esc(r.name)}" style="color:#BFE9F7;border-color:rgba(56,189,248,.3);background:rgba(56,189,248,.12)">▶ 실행</button>
          </div>`;
        }).join('');
      }
    }
    const sub = $('#today-sub');
    if (sub) sub.textContent = `${game === 'kovak' ? "KovaaK's" : 'Aim Lab'} · ${weapon === 'sniper' ? '스나' : '라플'} · 추천 ${rows.length}종 · 개별 실행 또는 연속 실행`;
    if (mask) mask.classList.add('show');
  }

  function openTodayScore(name) {
    const mask = $('#today-mask');
    if (mask) mask.classList.remove('show');
    if (name && typeof openScenario === 'function') { openScenario(name); return; }
    openScoreModal();
  }

  async function openWarmup() {
    if (!AL.real) return toast('앱(.exe)에서 실행하면 워밍업이 열립니다', false);
    busy(true, '워밍업 3종을 고르는 중…');
    const result = await call('warmup_plan', game, weapon);
    busy(false);
    if (!result || !result.ok) return fail(result, '워밍업을 준비하지 못했습니다');
    AL.warmup = result.recs || [];
    const list = $('#warmup-list');
    if (list) {
      list.innerHTML = AL.warmup.map((r, i) => `
        <div class="warmup-row">
          <span class="ix">${i + 1}</span>
          <span class="nm">${esc(r.name)}</span>
          <span class="ar">${(AREAS[r.area] || AREAS[0]).name}${r.done ? ' · 오늘 완료' : ''}</span>
          <button class="go" data-warm-run="${i}">▶ 실행</button>
        </div>`).join('');
    }
    const sub = $('#warmup-sub');
    if (sub) sub.textContent = `${game === 'kovak' ? "KovaaK's" : 'Aim Lab'} · ${weapon === 'sniper' ? '스나' : '라플'} · 짧은 핵심 3종`;
    const note = $('#warmup-note');
    if (note) note.textContent = result.message || '감각을 깨우고 랭크전으로 들어가세요';
    const mask = $('#warmup-mask');
    if (mask) mask.classList.add('show');
  }

  async function runWarmupAll() {
    const rows = AL.warmup || [];
    if (!rows.length) return toast('워밍업 시나리오가 없습니다', false);
    toast(`⚡ 워밍업 ${rows.length}종 연속 실행 · Steam 이 순서대로 열립니다`, true);
    for (const r of rows) {
      await launch(r.name, { quiet: true });
      await new Promise((res) => setTimeout(res, 1200));
    }
  }

  async function exportPlaylist(kind) {
    if (!AL.real) return toast('앱(.exe)에서 플레이리스트를 저장합니다', false);
    const result = await call('export_playlist', kind || 'recs', game, weapon);
    if (!result || !result.ok) return fail(result, '플레이리스트를 저장하지 못했습니다');
    toast(result.message || '📜 플레이리스트 저장 완료', true, 5600);
  }

  /* ------------------------------------------------------------ 1:1 전적 비교 */
  function compareRowHtml(r) {
    const unit = r.unit || '';
    const fmt = (v) => {
      if (v === null || v === undefined || v === '') return '—';
      if (r.text) return String(v);
      return unit === '%' ? Number(v).toFixed(1) + '%' : Number(v).toLocaleString() + (unit === 'RP' ? ' RP' : '');
    };
    const a = Number(r.me), b = Number(r.friend);
    let wa = 50;
    if (!r.text && isFinite(a) && isFinite(b) && (a + b) > 0) wa = Math.max(2, Math.min(98, a / (a + b) * 100));
    return `<div class="cmp-row">
      <span class="lb">${esc(r.label)}</span>
      <span class="cmp-val ${r.better === 'me' ? 'win' : ''}">${fmt(r.me)}</span>
      <div class="cmp-bar" style="${r.text ? 'opacity:.35' : ''}"><i class="a" style="width:${r.text ? 50 : wa}%"></i><i class="b" style="width:${r.text ? 50 : 100 - wa}%"></i></div>
      <span class="cmp-val left ${r.better === 'friend' ? 'win' : ''}">${fmt(r.friend)}</span>
    </div>`;
  }

  async function compareFriend(name) {
    if (!AL.real) return toast('앱(.exe)에서 1:1 비교를 볼 수 있습니다', false);
    const result = await call('compare_friend', name);
    if (!result || !result.ok) return fail(result, '비교할 전적이 없습니다');
    const cmp = result.compare;
    const body = $('#cmp-body');
    const title = $('#cmp-title');
    if (title) title.textContent = `${cmp.me.name} vs ${cmp.friend.name}`;
    if (body) {
      body.innerHTML = `
        <div class="cmp-hero">
          <div class="cmp-card me">
            <div class="nm">${esc(cmp.me.name)}</div>
            <div class="cl">${esc(cmp.me.clan)}</div>
            <div class="rp">${esc(cmp.me.solo_tier)}</div>
            <div class="rp">${esc(cmp.me.party_tier)}</div>
          </div>
          <div class="cmp-vs">VS<br><span style="font-size:9px;color:#7E8FA6;font-family:Pretendard">${cmp.score.me} : ${cmp.score.friend}</span></div>
          <div class="cmp-card">
            <div class="nm">${esc(cmp.friend.name)}</div>
            <div class="cl">${esc(cmp.friend.clan)}</div>
            <div class="rp">${esc(cmp.friend.solo_tier)}</div>
            <div class="rp">${esc(cmp.friend.party_tier)}</div>
          </div>
        </div>
        <div class="cmp-rows">${(cmp.rows || []).map(compareRowHtml).join('')}</div>
        <div class="mnote">${esc(cmp.verdict)} · ${esc(cmp.note)}</div>`;
    }
    const mask = $('#compare-mask');
    if (mask) mask.classList.add('show');
  }
  AL.compareFriend = compareFriend;

  /* ------------------------------------------------------------ 주간 목표 수정 */
  function openWeekGoals() {
    const week = (AL.state && AL.state.week) || {};
    const daysEl = $('#week-days');
    const gamesEl = $('#week-games');
    if (daysEl) daysEl.value = week.days || 5;
    if (gamesEl) gamesEl.value = week.games || 25;
    const mask = $('#week-mask');
    if (mask) mask.classList.add('show');
    if (daysEl) setTimeout(() => { daysEl.focus(); daysEl.select(); }, 60);
  }

  async function saveWeekGoals() {
    const days = Math.round(Number(($('#week-days') || {}).value || 0));
    const games = Math.round(Number(($('#week-games') || {}).value || 0));
    if (!days || !games) return toast('주 N일 · N판을 숫자로 입력해 주세요', false);
    if (!AL.real) return toast('앱(.exe)에서 저장할 수 있습니다', false);
    const result = await call('save_settings', { week_goals: { days, games } });
    if (!result || !result.ok) return fail(result, '목표를 저장하지 못했습니다');
    if (result.state) applyState(result.state);
    const mask = $('#week-mask');
    if (mask) mask.classList.remove('show');
    toast(`🎯 주간 목표 저장 · 주 ${days}일 · ${games}판`, true);
  }

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

  function reportCardCanvas() {
    const summary = currentSummary() || {};
    const st = AL.state || {};
    const values = summary.values || VALUES || {};
    const tier = summary.tier || (typeof TIERS !== 'undefined' ? TIERS[tierIdx] : null) || { n: '기록 대기', c: '#F59E0B' };
    const tierColor = tier.c || '#F59E0B';
    const scoreVal = typeof summary.score === 'number' ? summary.score : (typeof score === 'number' ? score : 0);
    const barracks = st.barracks || null;
    const soloTier = ((barracks || {}).tier || {}).solo || null;
    const partyTier = ((barracks || {}).tier || {}).party || null;
    const meta = st.meta || {};
    const streak = st.streak || {};
    const nextTier = summary.next_tier || null;
    const left = nextTier ? Math.max(0, (nextTier.cut || 0) - scoreVal) : 0;
    const now = new Date();
    const dateLabel = `${now.getFullYear()}.${String(now.getMonth() + 1).padStart(2, '0')}.${String(now.getDate()).padStart(2, '0')} (${'일월화수목금토'[now.getDay()]})`;
    const F = '"Malgun Gothic", "Apple SD Gothic Neo", Pretendard, sans-serif';
    const FN = '"Chakra Petch", "Malgun Gothic", Pretendard, sans-serif';

    const W = 900, H = 1180, P = 62;
    const c = document.createElement('canvas');
    c.width = W; c.height = H;
    const g = c.getContext('2d');

    const bg = g.createLinearGradient(0, 0, W, H);
    bg.addColorStop(0, '#0D1524'); bg.addColorStop(0.55, '#0A101C'); bg.addColorStop(1, '#070B13');
    g.fillStyle = bg; g.fillRect(0, 0, W, H);
    const glow = g.createRadialGradient(W / 2, 110, 20, W / 2, 110, 620);
    glow.addColorStop(0, 'rgba(245,158,11,0.20)'); glow.addColorStop(1, 'rgba(245,158,11,0)');
    g.fillStyle = glow; g.fillRect(0, 0, W, 720);
    g.strokeStyle = 'rgba(148,163,184,0.16)'; g.lineWidth = 2; g.strokeRect(1, 1, W - 2, H - 2);

    g.fillStyle = '#F59E0B'; g.fillRect(P, 56, 44, 5);
    g.textAlign = 'left';
    g.fillStyle = '#F8FAFC'; g.font = `800 30px ${FN}`;
    g.fillText('SA AIM LABS', P, 112);
    g.fillStyle = '#7E8FA6'; g.font = `600 18px ${F}`;
    g.fillText('오늘의 에임 리포트', P + 236, 112);
    g.textAlign = 'right';
    g.fillStyle = '#C7D3E4'; g.font = `700 21px ${FN}`;
    g.fillText(dateLabel, W - P, 84);
    g.fillStyle = '#5F7088'; g.font = `600 15px ${FN}`;
    g.fillText(`v${meta.version || '1.2.0'}`, W - P, 112);
    g.textAlign = 'left';

    /* 점수 */
    g.fillStyle = '#7E8FA6'; g.font = `700 16px ${F}`;
    g.fillText('종합 에임 점수', P, 196);
    g.fillStyle = '#F8FAFC'; g.font = `800 100px ${FN}`;
    g.fillText(scoreVal.toLocaleString('ko-KR'), P, 300);
    const tierName = tier.n || '기록 대기';
    g.fillStyle = tierColor; g.font = `800 32px ${F}`;
    g.fillText(tierName, P, 352);
    const tw = g.measureText(tierName).width;
    g.fillStyle = '#7E8FA6'; g.font = `600 16px ${F}`;
    g.fillText(`/ 3,600${nextTier ? ` · ${nextTier.n}까지 ${left}점` : ' · 최고 단계'}`, P + tw + 14, 352);

    /* 티어 메달 */
    const cx = W - P - 92, cy = 258, R = 92;
    const ring = g.createLinearGradient(cx - R, cy - R, cx + R, cy + R);
    ring.addColorStop(0, tierColor); ring.addColorStop(1, 'rgba(255,255,255,0.35)');
    g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2);
    g.fillStyle = 'rgba(7,11,19,0.9)'; g.fill();
    g.lineWidth = 7; g.strokeStyle = ring; g.stroke();
    g.beginPath(); g.arc(cx, cy, R - 17, 0, Math.PI * 2);
    g.lineWidth = 2; g.strokeStyle = 'rgba(148,163,184,0.28)'; g.stroke();
    g.textAlign = 'center';
    g.fillStyle = tierColor; g.font = `800 44px ${FN}`;
    g.fillText(String((summary.tier_index || 0) + 1), cx, cy + 6);
    g.fillStyle = '#7E8FA6'; g.font = `700 14px ${F}`;
    g.fillText('/ 18', cx, cy + 30);
    g.textAlign = 'left';

    /* 5영역 */
    g.strokeStyle = 'rgba(148,163,184,0.14)'; g.lineWidth = 1;
    g.beginPath(); g.moveTo(P, 400); g.lineTo(W - P, 400); g.stroke();
    g.fillStyle = '#C7D3E4'; g.font = `800 19px ${F}`;
    g.fillText('5영역 에임 밸런스', P, 438);
    g.fillStyle = '#5F7088'; g.font = `600 14px ${F}`;
    g.textAlign = 'right'; g.fillText('최근 기록 평균 · 0~100', W - P, 438); g.textAlign = 'left';
    AREAS.forEach((a, i) => {
      const y = 484 + i * 52;
      const v = Math.max(0, Math.min(100, Number(values[a.key] || 0)));
      g.fillStyle = '#C7D3E4'; g.font = `700 18px ${F}`;
      g.fillText(a.name, P, y + 6);
      const bx = P + 196, bw = 468;
      g.fillStyle = 'rgba(148,163,184,0.16)'; rr(g, bx, y - 8, bw, 15, 7); g.fill();
      const grad = g.createLinearGradient(bx, 0, bx + bw, 0);
      grad.addColorStop(0, a.color); grad.addColorStop(1, a.color);
      g.fillStyle = grad; rr(g, bx, y - 8, Math.max(10, bw * v / 100), 15, 7); g.fill();
      g.textAlign = 'right'; g.fillStyle = '#E4ECF8'; g.font = `800 20px ${FN}`;
      g.fillText(Math.round(v), W - P, y + 7); g.textAlign = 'left';
    });

    /* 인게임 랭크 */
    const ry = 760;
    g.beginPath(); g.moveTo(P, ry - 20); g.lineTo(W - P, ry - 20); g.stroke();
    g.fillStyle = '#C7D3E4'; g.font = `800 19px ${F}`;
    g.fillText('인게임 랭크전', P, ry + 18);
    const brStats = (barracks || {}).stats || {};
    const brName = (barracks || {}).nickname || ((st.nexon || {}).nickname || '');
    if (brName) {
      g.textAlign = 'right';
      g.fillStyle = '#FDE8BD'; g.font = `800 18px ${F}`;
      g.fillText(brName, W - P, ry + 18);
      g.fillStyle = '#7E8FA6'; g.font = `600 14px ${F}`;
      g.fillText(brStats.games ? `최근 ${brStats.games}판 · 승률 ${Number(brStats.win || 0).toFixed(1)}% · K/D ${Number(brStats.kd || 0).toFixed(1)}% · 딜량 ${Number(brStats.dmg || 0).toLocaleString('ko-KR')}` : '전적 미연동', W - P, ry + 40);
      g.textAlign = 'left';
    }
    const boxes = [
      { label: '솔로', tier: soloTier },
      { label: '파티', tier: partyTier }
    ];
    const bw2 = (W - P * 2 - 18) / 2;
    boxes.forEach((b, i) => {
      const bx = P + i * (bw2 + 18), by = ry + 40;
      g.fillStyle = 'rgba(7,11,19,0.6)'; rr(g, bx, by, bw2, 104, 14); g.fill();
      g.strokeStyle = 'rgba(148,163,184,0.18)'; g.lineWidth = 1; g.stroke();
      g.fillStyle = '#7E8FA6'; g.font = `700 15px ${F}`;
      g.fillText(b.label, bx + 22, by + 32);
      g.fillStyle = '#E4ECF8'; g.font = `800 26px ${F}`;
      g.fillText((b.tier || {}).name || '언랭크', bx + 22, by + 68);
      g.fillStyle = '#A9E5F8'; g.font = `700 19px ${FN}`;
      g.fillText(b.tier && b.tier.rp ? `${Number(b.tier.rp).toLocaleString('ko-KR')} RP` : '—', bx + 22, by + 92);
    });

    /* 오늘 · 연속 · 누적 */
    const sy = 968;
    const stats = [
      { k: '오늘 시나리오', v: `${(summary.today_scenarios !== undefined ? summary.today_scenarios : ((summary.recs || []).filter((r) => r.done).length))}종` },
      { k: '연속 훈련', v: `${streak.current || 0}일` },
      { k: '누적 훈련', v: `${summary.days || (st.profile || {}).days || 0}일` }
    ];
    const bw3 = (W - P * 2 - 24) / 3;
    stats.forEach((s, i) => {
      const bx = P + i * (bw3 + 12);
      g.fillStyle = 'rgba(7,11,19,0.55)'; rr(g, bx, sy, bw3, 92, 14); g.fill();
      g.strokeStyle = 'rgba(148,163,184,0.14)'; g.stroke();
      g.fillStyle = '#7E8FA6'; g.font = `700 14px ${F}`;
      g.textAlign = 'center'; g.fillText(s.k, bx + bw3 / 2, sy + 34);
      g.fillStyle = '#F8FAFC'; g.font = `800 30px ${FN}`;
      g.fillText(s.v, bx + bw3 / 2, sy + 72);
      g.textAlign = 'left';
    });

    /* 워터마크 */
    g.save();
    g.globalAlpha = 0.055;
    g.fillStyle = '#F8FAFC'; g.font = `800 64px ${FN}`;
    g.textAlign = 'center';
    g.fillText('SA AIM LABS', W / 2, 1084);
    g.restore();
    g.textAlign = 'left';

    /* 푸터 */
    g.beginPath(); g.moveTo(P, 1112); g.lineTo(W - P, 1112); g.stroke();
    g.fillStyle = '#5F7088'; g.font = `600 14px ${F}`;
    g.fillText('SA AIM LABS · 코박스·에임랩 통합 트레이너', P, 1144);
    g.textAlign = 'right';
    g.fillText(`${now.getHours()}:${String(now.getMinutes()).padStart(2, '0')} 생성`, W - P, 1144);
    g.textAlign = 'left';
    return c;
  }

  async function saveReportCard() {
    let canvas;
    try {
      canvas = reportCardCanvas();
    } catch (err) {
      return toast('리포트 카드를 그리지 못했습니다 · ' + String((err && err.message) || err), false);
    }
    let dataUrl = '';
    try {
      dataUrl = canvas.toDataURL('image/png');
    } catch (err) {
      return toast('이미지로 변환하지 못했습니다', false);
    }
    if (!AL.real) {
      // 브라우저 미리보기: 다운로드로 저장
      const a = document.createElement('a');
      a.href = dataUrl;
      a.download = 'sa_aim_report.png';
      document.body.appendChild(a);
      a.click();
      a.remove();
      return toast('📸 리포트 카드를 내려받았습니다', true);
    }
    busy(true, '리포트 카드를 저장하는 중…');
    const result = await call('save_report_card', dataUrl, (AL.state && AL.state.meta && AL.state.meta.today) || '');
    busy(false);
    if (!result || !result.ok) return fail(result, '리포트 카드를 저장하지 못했습니다');
    /* v1.3.1 — 저장 토스트에 '폴더 열기' 버튼 (자동 사라짐 시간은 그대로) */
    toast(result.message || '📸 리포트 카드 저장 완료', true, 5600, {
      label: '폴더 열기',
      onClick: async () => {
        const opened = await call('open_reports_folder');
        if (opened && opened.ok) toast('📂 리포트 폴더를 열었습니다', true);
        else toast((opened && opened.message) || (result.folder ? '폴더: ' + result.folder : '폴더를 열지 못했습니다'), false, 6000);
      }
    });
  }

  /* ------------------------------------------------------------ 트레이로 숨기기 */
  async function trayHide() {
    if (!AL.real) return toast('앱(.exe)에서 트레이로 숨길 수 있습니다', false);
    const result = await call('tray_hide');
    if (!result || !result.ok) return fail(result, '트레이로 숨기지 못했습니다');
    toast(result.message || '트레이로 숨겼습니다', true, 5200);
  }

  /* 창을 다시 열면: 설정이 켜져 있으면 코박스 점수를 조용히 확인한다 */
  let lastAutoFill = 0;
  function maybeAutoFill() {
    if (!AL.real || !AL.state) return;
    const set = (AL.state.settings || {});
    if (!set.kovak_autofill) return;
    if (Date.now() - lastAutoFill < 60000) return;
    if (document.querySelector('.mask.show')) return;
    lastAutoFill = Date.now();
    importKovak(true);
  }

  /* ==================================================================
     이벤트 연결
     ================================================================== */
  let bound = false;

  /* ==================================================================
     v1.4.0 — 스카우팅 · 듀오 시너지 · 맞춤 .plo · 온보딩 · 업데이트 · 연패 알림
     ================================================================== */
  const thCls = (v) => (Number(v) >= 75 ? 'hot' : Number(v) >= 55 ? 'mid' : 'low');

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

  function renderScout(res) {
    const raw = (res && res.rows) || [];
    AL.scout = res || AL.scout || {};
    if (!AL.scoutRoles) AL.scoutRoles = {};
    const rows = rolesApplied(raw);
    const team = rows.length ? localTeam(rows) : ((res && res.team) || {});
    renderScoutSum(rows, team);
    const table = $('#scout-table');
    if (table) {
      if (!rows.length) {
        table.innerHTML = '<div class="empty">상대 닉네임을 입력하고 <b>조회</b>를 누르면 비교표가 나옵니다</div>';
      } else {
        const head = `<div class="scout-row hd"><span class="l">상대</span><span>솔로</span><span>파티</span><span>K/D</span><span>딜량</span><span>무기 K/D</span><span>위협</span></div>`;
        const coreNames = new Set((team.snipers || []).map((s) => s.name));
        const body = rows.map((r) => {
          if (r.error) {
            return `<div class="scout-row" style="border-color:rgba(239,68,68,.28)"><div class="nm">${r.name}<small style="color:#F7AFAE">${r.error}</small></div><div class="v">—</div><div class="v">—</div><div class="v">—</div><div class="v">—</div><div class="v">—</div><div class="v">—</div></div>`;
          }
          const w = r.weapon || {};
          const core = coreNames.has(r.name);
          return `<div class="scout-row"${core ? ' style="border-color:rgba(56,189,248,.34);background:rgba(56,189,248,.06)"' : ''}>
            <div class="nm">
              <div style="display:flex;align-items:center;gap:4px;min-width:0">
                <b style="font-size:11.5px;font-weight:800;color:#EAF1FB;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${r.name}</b>
                ${core ? '<span style="flex:0 0 auto;font-size:7.6px;color:#A9E5F8;font-weight:800;border:1px solid rgba(56,189,248,.32);border-radius:3px;padding:0 3px">핵심 스나</span>' : ''}
                ${r.style ? `<span style="flex:0 0 auto;font-size:7.6px;color:#FCD9A0;font-weight:800;background:rgba(245,158,11,.12);border:1px solid rgba(245,158,11,.24);border-radius:3px;padding:0 3px" title="${r.style.desc || ''}">${r.style.emoji || ''} ${r.style.label}</span>` : ''}
              </div>
              <small style="display:block;margin-top:2px">${r.clan && r.clan !== '-' ? r.clan + ' 클랜 · ' : ''}최근 ${r.form && r.form.games ? r.form.games : (r.games || 0)}판 폼${r.form && r.form.streak >= 2 ? ' · ' + r.form.streak + '연승' : ''}</small>
            </div>
            <div class="v">${r.solo_tier || '—'}<br><span style="font-size:8.5px;color:#7E8FA6">${n0(r.solo)} RP</span></div>
            <div class="v">${r.party ? (r.party_tier || '—') : '언랭크'}<br><span style="font-size:8.5px;color:#7E8FA6">${r.party ? n0(r.party) + ' RP' : '—'}</span></div>
            <div class="v">${r.kd == null ? '—' : n0(r.kd, 1) + '%'}</div>
            <div class="v">${r.dmg == null ? '—' : n0(r.dmg)}</div>
            <div class="v" style="text-align:right">
              <button class="btn-tiny" data-role="${esc(r.name)}" title="클릭해서 역할을 스나 ↔ 라플 로 바꿉니다" style="height:18px;font-size:8.5px;padding:0 5px">${w.manual ? w.label : (w.estimate || w.label || '—')}</button>
              <br><span style="font-size:8px;color:#7E8FA6;white-space:nowrap">스나 K/D ${w.sniper_kd == null ? '—' : w.sniper_kd}% · 라플 K/D ${w.assault_kd == null ? '—' : w.assault_kd}%</span>
            </div>
            <div style="text-align:right"><span class="th ${thCls(r.threat)}">${r.threat} · ${r.threat_label}</span></div>
          </div>`;
        }).join('');
        table.innerHTML = head + body +
          ((team.tips || []).length
            ? `<div class="scout-tips" style="margin-top:5px;padding:8px 10px;border-radius:8px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.2)">
                 ${team.tips.map((t) => `<div class="ti">${t}</div>`).join('')}</div>`
            : '');
      }
    }
    const note = $('#scout-note');
    if (note) {
      note.innerHTML = res && res.calls !== undefined
        ? `이번 조회 API ${n0(res.calls)}건 · 이미 받은 전적은 캐시에서 사용 · <b>역할 버튼을 누르면 팀 스나수가 바로 다시 계산됩니다</b>`
        : '스나/라플 수치는 사용 비중이 아니라 <b>무기별 K/D</b> 입니다 (합이 100%를 넘을 수 있습니다).';
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
          <span class="ic">🤝</span><span>${(d && (d.reason || d.label)) || '함께 뛴 판을 찾지 못했습니다'}</span></div>
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
        <span class="ic">${d.tone === 'warn' ? '⚠️' : '🤝'}</span><span><b>${d.verdict || ''}</b> · ${d.label || ''}</span>
      </div>
      ${d.force ? `<div style="display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:8px;background:rgba(148,163,184,.05);border:1px solid rgba(148,163,184,.12);font-size:10px;font-weight:700;color:#93A4BA">
        <span>기본 전력 <b class="num" style="color:#C7D3E4">${d.force.base}</b></span>
        <span style="color:#4E5C72">+</span>
        <span>케미 보정 <b class="num" style="color:${d.force.adjust >= 0 ? '#6EE7C0' : '#F7AFAE'}">${d.force.adjust > 0 ? '+' : ''}${d.force.adjust}</b></span>
        <span style="color:#4E5C72">=</span>
        <span>실전 전력 <b class="num" style="color:#FDE8BD;font-size:12px">${d.force.power}</b></span>
        <span style="margin-left:auto;font-size:9px;color:#7E8FA6">${(d.force.notes || []).join(' · ')}</span>
      </div>` : ''}
      <div class="mnote">${d.note || ''}</div>`;
  }

  function renderUtilRecent(st) {
    const opp = $('#util-opp');
    const friends = $('#util-friends');
    const meta = $('#util-recent-meta');
    if (!opp || !friends) return;
    const matches = ((st || AL.state || {}).barracks || {}).matches || [];
    const first = matches[0] || null;
    const me = String(((st || AL.state || {}).nexon || {}).nickname || '').trim().toLowerCase();
    const names = [];
    ((first && first.players) || []).forEach((p) => {
      const nm = String((p && p[0]) || '').trim();
      if (nm && nm.toLowerCase() !== me && !names.includes(nm)) names.push(nm);
    });
    const opponents = names.slice(0, 6);
    if (meta) meta.textContent = first ? `${first.map || '최근 매치'} · ${(first.date || '').slice(5)}` : '최근 매치 없음';
    opp.innerHTML = opponents.length
      ? opponents.map((n) => `<button class="chip" data-opp="${esc(n)}" style="height:24px;font-size:9.5px;cursor:pointer" title="이 닉네임으로 스카우팅">${n}</button>`).join('')
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

  function renderBalanceCandidates() {
    const box = $('#balance-members');
    if (!box) return;
    const members = (AL.balanceCands && AL.balanceCands.members) || [];
    const mates = (AL.balanceCands && AL.balanceCands.coplayers) || [];
    const row = (c) => {
      const role = (AL.balanceRoles || {})[c.name] || (c.weapon === 'rifle' ? 'rifle' : 'sniper');
      return `<label style="display:flex;align-items:center;gap:6px;padding:5px 8px;border-radius:7px;background:rgba(7,11,19,.5);border:1px solid rgba(148,163,184,.12);cursor:pointer">
        <input type="checkbox" data-bal-name="${esc(c.name)}" style="accent-color:#F59E0B">
        <span style="font-size:11px;font-weight:700;color:#EAF1FB;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${c.name}${c.me ? ' <small style="color:#FBD48F">ME</small>' : ''}</span>
        <button type="button" data-bal-role="${esc(c.name)}" title="클릭해서 스나 ↔ 라플" style="height:17px;font-size:8.5px;font-weight:800;padding:0 5px;border-radius:4px;border:1px solid ${role === 'sniper' ? 'rgba(245,158,11,.4)' : 'rgba(56,189,248,.4)'};background:${role === 'sniper' ? 'rgba(245,158,11,.15)' : 'rgba(56,189,248,.13)'};color:${role === 'sniper' ? '#FCD9A0' : '#A9E5F8'}">${role === 'sniper' ? '스나' : '라플'}</button>
        <span style="margin-left:auto;font-family:var(--num);font-size:9px;color:#8FA1B8;white-space:nowrap">${c.source === 'match' ? `동반 ${c.games}판` : (c.solo ? n0(c.solo) + ' RP' : '언랭크')}</span>
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

  async function runBalance() {
    if (!AL.real) { toast('앱(.exe)에서 팀을 나눕니다', false); return; }
    const names = balanceNames();
    if (names.length && names.length < 6) { toast('내전은 3:3(6명) 이상 선택해 주세요', false); return; }
    const box = $('#balance-result');
    if (box) box.innerHTML = '<div class="mnote">균형을 계산하는 중…</div>';
    const roles = {};
    names.forEach((n) => { if ((AL.balanceRoles || {})[n]) roles[n] = AL.balanceRoles[n]; });
    const res = await call('balance_teams', names, 0, roles);
    renderBalance(res || {});
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
          <span style="font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${r.name}</span>
          <span style="font-size:9px;color:#7E8FA6">${r.style_emoji || ''}${r.style || ''}</span>
          <span class="num" style="margin-left:auto;font-size:9.5px;color:#FDE8BD">${r.power}</span>
        </div>`).join('')}
      </div>`;
    box.innerHTML = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:7px">
        ${team(res.a, 'A팀', 'rgba(245,158,11,.34)')}
        ${team(res.b, 'B팀', 'rgba(56,189,248,.34)')}
      </div>
      <div class="digest-advice" style="background:rgba(52,211,153,.07);border-color:rgba(52,211,153,.26);color:#CFF6E6">
        <span class="ic">⚖️</span><span><b>${res.verdict}</b> · ${res.note}</span>
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
      box.innerHTML = `<div class="mnote">${(res && (res.note || res.message)) || '함께 뛴 판이 2판 이상인 친구가 없습니다 — 병영수첩을 최신화해 주세요'}</div>`;
      return;
    }
    const medal = ['🥇', '🥈', '🥉'];
    box.innerHTML = rows.map((r, i) => `
      <div style="display:grid;grid-template-columns:26px minmax(0,1fr) 58px 72px 70px;gap:7px;align-items:center;padding:6px 8px;border-radius:7px;background:rgba(7,11,19,.5);border:1px solid ${i === 0 ? 'rgba(245,158,11,.34)' : 'rgba(148,163,184,.12)'}">
        <div style="font-size:12px;text-align:center">${medal[i] || (i + 1)}</div>
        <div style="min-width:0"><div style="font-size:11px;font-weight:800;color:#EAF1FB;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${r.friend}</div>
          <div style="font-size:8.5px;color:#7E8FA6;font-weight:600">함께 ${r.games}판 · 혼자 ${r.alone_games}판</div></div>
        <div class="num" style="font-size:11px;text-align:right;color:${(r.win_delta || 0) >= 0 ? '#6EE7C0' : '#F7AFAE'}">${r.win_delta == null ? '—' : (r.win_delta > 0 ? '+' : '') + r.win_delta + '%p'}</div>
        <div class="num" style="font-size:10px;text-align:right;color:#C7D3E4">K/D ${r.kd_delta == null ? '—' : (r.kd_delta > 0 ? '+' : '') + r.kd_delta}%</div>
        <div style="text-align:right"><span class="num" style="font-size:11px;font-weight:800;color:#FDE8BD">${r.score}</span>
          <div style="font-size:8px;color:#8FA1B8;font-weight:700">${r.verdict || ''}</div></div>
      </div>`).join('');
  }

  async function openPersonalPlo() {
    const mask = $('#plo-mask');
    if (mask) mask.classList.add('show');
    if (!AL.real) { toast('앱(.exe)에서 약점·무기 기준으로 만듭니다', false); return; }
    busy(true, '약점 + 주무기로 플레이리스트를 고르는 중…');
    const res = await call('personal_playlist', game, weapon, false);
    busy(false);
    if (!res || res.ok === false) { fail(res, '플레이리스트를 만들지 못했습니다'); return; }
    AL.plan = res;
    renderPlan(res);
  }

  function renderPlan(plan) {
    const coach = $('#plo-coaching');
    if (coach) {
      coach.innerHTML = (plan.coaching || []).map((t) => `<div class="coach-row"><div class="ic">🤖</div><div class="tx"><div class="d" style="font-size:9.5px">${t}</div></div></div>`).join('');
    }
    const list = $('#plo-list');
    if (list) {
      list.innerHTML = (plan.reasons || []).map((r, i) => `
        <div style="display:grid;grid-template-columns:20px minmax(0,1fr) auto;gap:8px;align-items:center;padding:6px 9px;border-radius:8px;background:rgba(7,11,19,.5);border:1px solid rgba(148,163,184,.12)">
          <div class="num" style="color:#F59E0B;font-weight:800;font-size:11px">${i + 1}</div>
          <div style="min-width:0"><div style="font-size:11px;font-weight:700;color:#EAF1FB;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${r.name}</div>
            <div style="font-size:8.8px;color:#8496AE;font-weight:600;margin-top:1px">${r.why || ''}</div></div>
          <span class="area" style="height:18px;font-size:8.5px">${r.area || ''}</span>
        </div>`).join('');
    }
    const sub = $('#plo-sub');
    if (sub) sub.textContent = `약점 ${(plan.focus || []).join(' · ')} 우선 · ${plan.count}종 · ${plan.philosophy || ''}`;
  }

  async function savePersonalPlo() {
    if (!AL.real) { toast('앱(.exe)에서 플레이리스트 파일을 저장합니다', false); return; }
    const res = await call('personal_playlist', game, weapon, true);
    if (!res || res.ok === false) { fail(res, '저장에 실패했습니다'); return; }
    toast(res.message || '맞춤 플레이리스트를 저장했습니다', true, 5200);
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

    /* ---------- 창 제어 ---------- */
    const winMin = $('#win-min');
    if (winMin) winMin.addEventListener('click', (e) => { e.stopPropagation(); if (AL.real) call('minimize_window'); });
    const winClose = $('#win-close');
    if (winClose) winClose.addEventListener('click', (e) => {
      e.stopPropagation();
      /* 22. X 는 항상 '트레이로 숨기기 / 프로그램 종료' 확인창 (v1.3.0) */
      const mask = $('#close-mask');
      if (mask) mask.classList.add('show');
      else if (AL.real) call('close_window');
    });

    /* ---------- v1.2.0 빠른 실행 · 워밍업 · 리포트 카드 ---------- */
    ['#warmup-btn', '#warmup-btn-2'].forEach((sel) => {
      const b = $(sel);
      if (b) b.addEventListener('click', (e) => { e.preventDefault(); openWarmup(); });
    });
    ['#kovak-import-btn', '#kovak-import-2', '#kovak-import-head', '#score-kovak'].forEach((sel) => {
      const b = $(sel);
      if (b) b.addEventListener('click', (e) => { e.preventDefault(); importKovak(false); });
    });
    const reportBtnBr = $('#br-report');
    if (reportBtnBr) reportBtnBr.addEventListener('click', (e) => { e.preventDefault(); saveReportCard(); });
    const reportBtn = $('#report-btn');
    if (reportBtn) reportBtn.addEventListener('click', (e) => { e.preventDefault(); saveReportCard(); });
    const ploBtn = $('#plo-btn');
    if (ploBtn) ploBtn.addEventListener('click', (e) => { e.preventDefault(); exportPlaylist('recs'); });
    const wPlo = $('#warmup-plo');
    if (wPlo) wPlo.addEventListener('click', () => exportPlaylist('warmup'));
    const wRun = $('#warmup-run');
    if (wRun) wRun.addEventListener('click', () => runWarmupAll());
    const wList = $('#warmup-list');
    if (wList) {
      wList.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-warm-run]');
        if (!btn) return;
        const row = (AL.warmup || [])[Number(btn.dataset.warmRun)];
        if (row && AL.real) launch(row.name);
        else toast('앱(.exe)에서 실행하면 Steam 이 열립니다', false);
      });
    }
    const weekEdit = $('#week-goal-edit');
    if (weekEdit) weekEdit.addEventListener('click', openWeekGoals);
    const weekSave = $('#week-save');
    if (weekSave) weekSave.addEventListener('click', (e) => { e.preventDefault(); saveWeekGoals(); });
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
    AL.openToday = openToday;
    const todayList = $('#today-list');
    if (todayList) todayList.addEventListener('click', (e) => {
      const run = e.target.closest('[data-today-run]');
      if (run) { e.preventDefault(); const mask = $('#today-mask'); if (mask) mask.classList.remove('show'); launch(run.dataset.todayRun); return; }
      const sc = e.target.closest('[data-today-score]');
      if (sc) { e.preventDefault(); openTodayScore(sc.dataset.todayScore); }
    });
    const todayRunAll = $('#today-runall');
    if (todayRunAll) todayRunAll.addEventListener('click', () => { const mask = $('#today-mask'); if (mask) mask.classList.remove('show'); launchAll(); });
    const todayScore = $('#today-score');
    if (todayScore) todayScore.addEventListener('click', () => { const mask = $('#today-mask'); if (mask) mask.classList.remove('show'); openScoreModal(); });
    const duoRankBtn = $('#duo-rank-btn');
    if (duoRankBtn) duoRankBtn.addEventListener('click', () => runDuoRank());
    const duoRun = $('#duo-run');
    if (duoRun) duoRun.addEventListener('click', () => runDuo());
    const duoInput = $('#duo-nick');
    if (duoInput) duoInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); runDuo(); } });
    const ploBtn2 = $('#personal-plo-btn');
    if (ploBtn2) ploBtn2.addEventListener('click', (e) => { e.preventDefault(); openPersonalPlo(); });
    const ploSave = $('#plo-save');
    if (ploSave) ploSave.addEventListener('click', () => savePersonalPlo());

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
      if (typeof openWarmup === 'function') openWarmup();
    });

    /* ---------- 모달 닫기 ---------- */
    $$('[data-close]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const mask = $('#' + btn.dataset.close);
        if (mask) mask.classList.remove('show');
      });
    });
    $$('.mask').forEach((mask) => {
      mask.addEventListener('mousedown', (e) => { if (e.target === mask) mask.classList.remove('show'); });
    });
    $$('#cloud-tabs button').forEach((b) => b.addEventListener('click', () => setCloudMode(b.dataset.tab)));
    const cloudSubmit = $('#cloud-submit');
    if (cloudSubmit) cloudSubmit.addEventListener('click', submitCloud);

    /* ---------- 점수 저장 ---------- */
    const scoreSave = $('#score-save');
    if (scoreSave) scoreSave.addEventListener('click', (e) => { e.preventDefault(); saveScores(); });
    const scoreBtn = $('#score-input-btn');
    if (scoreBtn) scoreBtn.addEventListener('click', openScoreModal);
    const recSave = $('#rec-save');
    if (recSave) recSave.addEventListener('click', openScoreModal);

    /* ---------- 시나리오 모달 (점수 + 재생) ---------- */
    const scenSave = $('#scen-save');
    if (scenSave) scenSave.addEventListener('click', (e) => { e.preventDefault(); saveScenario(); });
    const scenScore = $('#scen-score');
    if (scenScore) scenScore.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); saveScenario(); }
    });
    const scenPlay = $('#scen-play');
    if (scenPlay) scenPlay.addEventListener('click', () => {
      if (!SCEN) return;
      if (!AL.real) return toast('앱(.exe)에서 실행하면 Steam 이 열립니다', false);
      launch(SCEN.name);
    });

    /* ---------- + 더 연습하기 ---------- */
    ['#rec-more', '#rec-more-2'].forEach((sel) => {
      const btn = $(sel);
      if (btn) btn.addEventListener('click', (e) => { e.preventDefault(); morePractice(); });
    });

    /* ---------- 추천 목록 · 실행 ---------- */
    const recList = $('#rec-list');
    if (recList) {
      recList.addEventListener('click', (e) => {
        const row = e.target.closest('[data-rec]');
        if (!row) return;
        const name = row.dataset.name;
        if (e.target.closest('[data-run]')) {
          if (!AL.real) return toast('앱(.exe)에서 실행하면 Steam 이 열립니다', false);
          e.preventDefault();
          launch(name);
          return;
        }
        openScenario(name, row.dataset.areakey);
      });
    }
    const recGrid = $('#rec-grid');
    if (recGrid) {
      recGrid.addEventListener('click', (e) => {
        const item = e.target.closest('[data-scen]');
        if (!item) return;
        openScenario(item.dataset.scen, item.dataset.scenArea);
      });
    }
    const startBtn = $('#start-train-btn');
    if (startBtn) {
      startBtn.addEventListener('click', () => {
        if (!AL.real) return toast('앱(.exe)에서 실행하면 Steam 이 열립니다', false);
        const recs = (currentSummary() && currentSummary().recs) || [];
        const next = recs.find((r) => !r.done) || recs[0];
        if (next) launch(next.name);
        else toast('추천 시나리오가 없습니다', false);
      });
    }
    const runAll = $('#run-all-btn');
    if (runAll) {
      runAll.addEventListener('click', () => {
        if (!AL.real) return toast('앱(.exe)에서 실행하면 Steam 이 열립니다', false);
        /* v1.3.1 — 5종을 순서대로 실행하면서, 바로 점수를 적을 수 있게 5칸 입력창도 함께 연다 */
        launchAll();
        setTimeout(() => { try { openScoreModal(); } catch (err) { /* 점수 창은 보조 */ } }, 700);
      });
    }

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
            hotkey_enabled: 'Ctrl+Shift+A 단축키', kovak_autofill: '코박스 점수 자동 채움'
          };
          toast(`${labels[key] || key} ${!on ? '켜짐' : '꺼짐'}`, true);
          return;
        }
        if (e.target.closest('#set-reset-scores')) {
          if (typeof resetFlow === 'function') resetFlow();
          else openResetModal();
          return;
        }

        if (e.target.closest('#set-api-save') || e.target.closest('#set-api-test')) {
          if (!AL.real) return toast('앱(.exe)에서 키를 저장할 수 있습니다', false);
          const patch = collectSettings();
          if (!patch.nickname && !patch.api_key) return toast('닉네임 또는 API 키를 입력해 주세요', false);
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
      if (name === 'barracks') {
        if (AL.peek) { renderPeek(); return; }
        const b = AL.state.barracks;
        if (AL.view === 'matches' && b) renderMatches(b);
        else if (b) renderMaps(b);
      }
      if (name === 'maps') { renderMaps(AL.peek || AL.state.barracks); }
      if (name === 'records') { if (typeof drawRecords === 'function') drawRecords(); }
      if (name === 'utils') perfRefresh(false);
    };
    AL.onSwitch = function () {
      if (!AL.state) return;
      applySummaryOnly();
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
    AL.resetScores = async function () {
      if (!AL.real) return toast('앱(.exe)에서 초기화할 수 있습니다', false);
      busy(true, '점수 기록을 초기화하는 중…');
      const result = await call('reset_scores', '초기화');
      busy(false);
      if (!result || !result.ok) return fail(result, '초기화하지 못했습니다');
      if (result.state) applyState(result.state);
      const mask = $('#reset-mask');
      if (mask) mask.classList.remove('show');
      toast(result.message || '점수 기록을 초기화했습니다', true, 5200);
    };
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
      const result = await call('open_barracks_page', target || null);
      toast(result && result.ok
        ? `🪖 ${name || '내'} 병영수첩을 브라우저에서 엽니다`
        : ((result && result.message) || '병영수첩을 열지 못했습니다'), !!(result && result.ok), 5200);
    };
    AL.openClan = async function (ouid, name) {
      const target = ouid || ((AL.state && AL.state.barracks) || {}).ouid || '';
      if (!AL.real) {
        toast('앱(.exe)에서 클랜 홈페이지를 엽니다', true);
        return;
      }
      const result = await call('open_clan_page', target || null);
      toast(result && result.ok
        ? `🛡 ${name || ''} 클랜 홈페이지를 브라우저에서 엽니다`
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
  function applySummaryOnly() {
    const st = AL.state;
    const summary = st && st.summaries && st.summaries[`${game}-${weapon}`];
    if (!summary) return;
    VALUES = {};
    AREAS.forEach((a) => { VALUES[a.key] = Math.round(Number(summary.values?.[a.key] || 0)); });
    recalc();
    if (typeof summary.score === 'number') {
      score = summary.score;
      tierIdx = 0;
      TIERS.forEach((t, k) => { if (score >= t.cut) tierIdx = k; });
    }
    applyRecs(summary);
    const daily = summary.daily || [];
    TREND = { labels: daily.map((d) => mdLabel(d.date)), values: daily.map((d) => (d.score === null ? null : d.score)) };
    const known = daily.filter((d) => d.score !== null).map((d) => d.score);
    SPARK = known.length ? known.slice(-7) : [0, 0];
    refreshAll();
    if (typeof summary.score === 'number') {
      score = summary.score;
      tierIdx = 0;
      TIERS.forEach((t, k) => { if (score >= t.cut) tierIdx = k; });
      drawKpis(); drawRadar(); drawLadder(); drawTierTable();
    }
    paintSummaryExtras(summary);
  }

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

  window.addEventListener('focus', () => setTimeout(maybeAutoFill, 900));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) setTimeout(maybeAutoFill, 900); });

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
