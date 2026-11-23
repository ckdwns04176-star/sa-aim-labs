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
    '.set-status.on{color:#9AE6C9}'
  ].join('\n');
  document.head.appendChild(style);

  /* ------------------------------------------------------------------ 토스트 */
  let toastTimer = null;
  function toast(message, ok, ms) {
    const t = $('#app-toast');
    if (!t) return;
    t.textContent = message;
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
        : `<span class="pulse"></span>로컬 저장 · ${fmtLabel(meta.now)}`;
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
      AREAS.forEach((a) => { VALUES[a.key] = Math.round(Number(summary.values?.[a.key] || 0)); });
      recalc();
      if (typeof summary.score === 'number') {
        score = summary.score;
        tierIdx = 0;
        TIERS.forEach((t, k) => { if (score >= t.cut) tierIdx = k; });
      }

      const recs = (summary.recs || []).map((r) => ({
        name: r.name,
        area: typeof r.area === 'number' ? r.area : areaIdx(r.area_key),
        diff: r.diff || '초급',
        score: r.score === null || r.score === undefined ? null : Math.round(r.score * 10) / 10,
        done: !!r.done,
        delta: typeof r.delta === 'number' ? r.delta : null
      }));
      RECS[`${game}-${weapon}`] = recs;

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
      label: g.label, cur: g.cur, tgt: g.tgt, unit: g.unit || '',
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

  function renderBarracks(barracks, st) {
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
    const weaponLabel = (barracks.recent || {}).sniper >= (barracks.recent || {}).assault ? '스나 유저' : '라플 유저';
    const initial = (barracks.nickname || '?').charAt(0);

    if (banner) {
      banner.innerHTML = `
        <div class="emb-bg"><img src="assets/${band.img}.png" alt=""></div>
        <div class="bn-left">
          <div class="bn-ava">${initial}<i></i></div>
          <div>
            <div class="bn-name"><h2>${barracks.nickname}</h2>
              <span class="tag ${weaponLabel === '스나 유저' ? 'amber' : 'cyan'}" id="bn-user">${weaponLabel}</span>
              <span class="tag dim">${tier.solo.name}</span></div>
            <div class="bn-sub"><span>${basic.clan && basic.clan !== '-' ? basic.clan + ' 클랜' : '클랜 없음'}</span>
              <span class="sep">·</span><span>OUID ${String(barracks.ouid || '').slice(0, 10)}…</span>
              <span class="sep">·</span><span>가입 <b>${basic.created || '—'}</b></span>
              <span class="sep">·</span><span>예의 <b>${basic.manner || '—'}</b></span></div>
          </div>
        </div>
        <div class="bn-stats">
          <div class="bn-stat"><span>최근 100판</span><b>${n0(stats.games)}<i>판</i></b></div>
          <div class="bn-stat"><span>승률</span><b>${n0(stats.win, 1)}<i>%</i></b></div>
          <div class="bn-stat"><span>K/D</span><b>${n0(stats.kd, 1)}<i>%</i></b></div>
          <div class="bn-stat"><span>평균 딜량</span><b>${n0(stats.dmg)}</b></div>
        </div>`;
    }
    if (ranks) ranks.innerHTML = rankCard('solo', tier.solo) + rankCard('party', tier.party);
    if (AL.view === 'matches') renderMatches(barracks);
    else renderMaps(barracks);
  }

  function renderMatches(barracks) {
    const table = $('#map-table');
    if (!table) return;
    const rows = barracks.matches || [];
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
    const table = $('#map-table');
    if (!table) return;
    const rows = (barracks && barracks.maps) || [];
    if (!rows.length) {
      table.innerHTML = '<tbody><tr><td class="l" style="color:#6E7F95">맵 표본이 부족합니다</td></tr></tbody>';
      return;
    }
    table.innerHTML = `
      <thead><tr><th class="l" style="width:18px">#</th><th class="l">맵</th><th>판수</th><th>승률</th><th>K/D</th><th>평균 딜량</th><th class="l">약점 지수</th></tr></thead>
      <tbody>${rows.map((m) => `
        <tr class="${m.rk === 1 ? 'cur' : ''}">
          <td class="rk">${m.rk}</td>
          <td class="l map">${m.map}</td>
          <td>${m.games}판</td>
          <td class="${m.win < 45 ? 'neg' : m.win >= 55 ? 'pos' : ''}">${n0(m.win, 1)}%</td>
          <td class="${m.kd < 48 ? 'neg' : m.kd >= 55 ? 'pos' : ''}">${n0(m.kd, 1)}%</td>
          <td>${n0(m.dmg)}</td>
          <td class="l"><span class="wbar"><i style="width:${Math.min(100, m.weak)}%"></i></span>${n0(m.weak, 1)}</td>
        </tr>`).join('')}</tbody>`;
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

    /* ---------- 카드 1 · NEXON API ---------- */
    const api = $('#set-api');
    if (api) {
      api.innerHTML = `
        <div class="card-hd" style="padding:0">
          <div><div class="card-title"><span class="bar"></span>NEXON Open API</div>
            <div class="card-sub">서든어택 병영수첩 · 친구 순위표</div></div>
          <span class="ustate ${nexon.connected ? 'on' : 'warn'}">${nexon.connected ? '키 등록됨' : '키 필요'}</span>
        </div>
        ${inputRow('set-nick', '서든어택 닉네임', set.nickname || '', { maxlength: 12 })}
        ${inputRow('set-key', 'NEXON Open API 키', '', { type: 'password', hint: '발급: open.api.nexon.com · test_ 키는 본인 계정 전용입니다' })}
        <div class="set-actions">
          <button class="btn btn-primary" id="set-api-save">저장</button>
          <button class="btn btn-ghost" id="set-api-test">저장 후 전적 확인</button>
        </div>`;
    }

    /* ---------- 카드 2 · 실행 · 자동화 ---------- */
    const run = $('#set-run');
    if (run) {
      run.innerHTML = `
        <div class="card-hd" style="padding:0">
          <div><div class="card-title"><span class="bar"></span>실행 · 자동화</div>
            <div class="card-sub">트레이너 실행 경로 · 저장 동작</div></div>
          <span class="chip" style="height:20px;font-size:9.5px">${meta.steam ? 'Steam 감지' : 'Steam 미감지'}</span>
        </div>
        ${inputRow('set-kovak', "KovaaK's 경로", set.kovak_path || '', { hint: '비워 두면 steam://run/824270 으로 실행합니다' })}
        ${inputRow('set-aimlab', 'Aim Lab 경로', set.aimlab_path || '', { hint: '비워 두면 steam://run/714010 으로 실행합니다' })}
        ${toggleRow('auto_launch', '추천 시나리오 자동 실행', '▶ 버튼으로 Steam 원클릭 실행', !!set.auto_launch)}
        ${toggleRow('autosave', '점수 자동 저장', '입력 후 저장 버튼 없이 기록', !!set.autosave)}
        <div class="set-actions">
          <button class="btn btn-primary" id="set-run-save">저장</button>
          <button class="btn btn-ghost" id="set-open-folder">기록 폴더 열기</button>
        </div>`;
    }

    /* ---------- 카드 3 · Supabase ---------- */
    const cl = $('#set-cloud');
    if (cl) {
      const useDefault = !(set.supabase || {}).url || (set.supabase || {}).url === (cloud.url || '');
      cl.innerHTML = `
        <div class="card-hd" style="padding:0">
          <div><div class="card-title"><span class="bar"></span>클라우드 저장소</div>
            <div class="card-sub">이메일 로그인 한 번이면 다른 PC 에서도 기록이 이어집니다</div></div>
          <span class="ustate ${cloud.signed_in ? 'on' : cloud.configured ? 'warn' : ''}">${cloud.signed_in ? '로그인됨' : cloud.configured ? '로그인 전' : '설정 준비됨'}</span>
        </div>
        <div class="set-note" style="margin:2px 0 6px;font-size:10px;line-height:1.6;color:#A9B9CE">
          집 · PC방 어디서나 <b>내 에임 기록과 순위표</b>를 이어서 쓰는 클라우드 저장소입니다.
          이메일로 로그인하면 훈련 기록·순위표가 계정에 저장되고, 다른 PC 에서 <b>내려받기</b>로 그대로 이어갈 수 있습니다.
        </div>
        <div class="set-status ${cloud.signed_in ? 'on' : ''}">${cloud.signed_in ? `☁️ ${cloud.email || ''} · 마지막 동기화 ${fmtLabel(cloud.last_sync)}` : '로그인 전 · 이 PC 에는 기록이 로컬로 저장되고 있습니다'}</div>
        <div class="set-actions">
          ${cloud.signed_in
            ? '<button class="btn btn-ghost" id="set-cloud-out">로그아웃</button>'
            : '<button class="btn btn-primary" id="set-cloud-login">로그인 / 회원가입</button>'}
          <button class="btn btn-cyan" id="set-cloud-push">☁️ 올리기</button>
          <button class="btn btn-ghost" id="set-cloud-pull">내려받기</button>
        </div>
        <details id="set-cloud-adv" style="margin-top:6px">
          <summary style="cursor:pointer;font-size:9.5px;font-weight:700;color:#7E8FA6;list-style:none;outline:none">
            ⚙ 고급 서버 설정 ${useDefault ? '· 기본 서버 사용 중' : '· 직접 입력됨'}
          </summary>
          <div style="display:flex;flex-direction:column;gap:6px;margin-top:6px">
            <div class="set-note" style="font-size:9px;color:#64748B;line-height:1.5">
              앱에 기본 서버 주소와 공개 키가 이미 들어 있어 <b>그대로 두면 됩니다</b>.
              본인 Supabase 프로젝트를 쓸 때만 아래를 바꾸세요.
            </div>
            ${inputRow('set-sb-url', '프로젝트 URL', cloud.url || '')}
            ${inputRow('set-sb-key', '공개 키 (publishable / anon)', '', { type: 'password', hint: 'anon 공개 키입니다 · 데이터는 RLS 로 본인 행만 보호됩니다' })}
            <button class="btn btn-ghost" id="set-cloud-check" style="align-self:flex-start;height:24px;font-size:10px">서버 연결 확인</button>
          </div>
        </details>`;
    }

    /* ---------- 카드 4 · 목표 · 정보 ---------- */
    const more = $('#set-more');
    if (more) {
      more.innerHTML = `
        <div class="card-hd" style="padding:0">
          <div><div class="card-title"><span class="bar"></span>목표 · 앱 정보</div>
            <div class="card-sub">병영수첩 목표 4종</div></div>
          <span class="chip" style="height:20px;font-size:9.5px">v${meta.version || '1.0.0'}</span>
        </div>
        <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px">
          ${inputRow('set-goal-win', '목표 승률 (%)', goals.win ?? 55, { type: 'number' })}
          ${inputRow('set-goal-kd', '목표 K/D (%)', goals.kd ?? 60, { type: 'number' })}
          ${inputRow('set-goal-dmg', '목표 평균 딜량', goals.dmg ?? 1400, { type: 'number' })}
          ${inputRow('set-goal-rp', '목표 솔로 RP', goals.rp ?? 4000, { type: 'number' })}
        </div>
        <div class="set-note">데이터 <b>${meta.data_file || '—'}</b><br>${meta.platform || ''} · ${meta.frozen ? '배포 빌드' : '소스 실행'}${meta.is_admin ? ' · 관리자' : ''}${meta.load_error ? ' · ' + meta.load_error : ''}</div>
        <div class="set-actions">
          <button class="btn btn-primary" id="set-goal-save">목표 저장</button>
          <button class="btn btn-ghost" id="set-backup">백업 파일 저장</button>
        </div>`;
    }
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
    if (!AL.real) {
      return toast('미리보기 화면입니다 · .exe 로 실행하면 전적을 불러옵니다', false);
    }
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
    const b = result.barracks || {};
    const calls = typeof result.calls === 'number' ? result.calls : null;
    toast(
      `🪖 ${b.nickname || '전적'} 최신화 · ${n0((b.stats || {}).games)}판 · 승률 ${n0((b.stats || {}).win, 1)}%`
      + (calls !== null ? ` · API ${calls}회` : ''),
      true
    );
  }

  async function fetchLeaderboard() {
    if (!AL.real) return toast('미리보기 화면입니다 · .exe 로 실행하면 순위표를 갱신합니다', false);
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
    if (!AL.real) return toast('미리보기 화면입니다 · .exe 로 실행하면 검색됩니다', false);
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
    if (!AL.real) return toast('미리보기 화면입니다', false);
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
  async function applyNvidia(mode) {
    busy(true, `${mode === 'rifle' ? '라플' : '스나'} 프로필 적용 중…`);
    let result = await call('apply_nvidia_profile', mode, false);
    busy(false);
    if (result && !result.ok && (result.can_elevate || result.needs_admin)) {
      // 관리자 권한이 필요 → UAC 승인 요청 (사용자 확인 후)
      const go = window.confirm(`${result.message}\n\n지금 관리자 권한으로 다시 적용할까요?`);
      if (!go) return;
      busy(true, '관리자 권한 요청 중… (UAC 창 확인)');
      result = await call('apply_nvidia_profile', mode, true);
      busy(false);
      if (result && result.message) toast(result.message, !!result.ok);
      if (result && result.state) applyState(result.state);
      return;
    }
    if (!result || !result.ok) return fail(result, '프로필을 적용하지 못했습니다');
    if (result.state) applyState(result.state);
    toast(result.message || `${mode === 'rifle' ? '🔫 라플' : '🎯 스나'} 프로필 적용 완료`, true);
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
    const result = await call('restore_nvidia_profile');
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
     이벤트 연결
     ================================================================== */
  let bound = false;

  function bind() {
    if (bound) return;
    bound = true;

    /* ---------- 창 이동 (OS 네이티브 · 지연 없음) ---------- */
    const titlebar = $('#titlebar');
    if (titlebar) {
      let dragMode = 'native';
      titlebar.addEventListener('mousedown', async (e) => {
        if (!AL.real || e.button !== 0) return;
        if (e.target.closest('.winbtns')) return;
        if (dragMode === 'js') return;      // pywebview 기본 드래그가 처리
        const result = await call('drag_window');
        if (result && result.supported === false) {
          /* 네이티브 이동을 못 쓰는 환경: pywebview 공식 드래그 영역으로 폴백 */
          dragMode = 'js';
          titlebar.classList.add('pywebview-drag-region');
          if (window.AL && AL.toast) AL.toast('창 이동: 기본 모드로 전환했습니다', true);
        }
      });
      titlebar.addEventListener('dblclick', (e) => {
        if (!AL.real || e.target.closest('.winbtns')) return;
        call('toggle_maximize');
      });
    }

    /* ---------- 창 제어 ---------- */
    const winMin = $('#win-min');
    if (winMin) winMin.addEventListener('click', (e) => { e.stopPropagation(); if (AL.real) call('minimize_window'); });
    const winMax = $('#win-max');
    if (winMax) winMax.addEventListener('click', (e) => { e.stopPropagation(); if (AL.real) call('toggle_maximize'); });
    const winClose = $('#win-close');
    if (winClose) winClose.addEventListener('click', (e) => { e.stopPropagation(); if (AL.real) call('close_window'); });

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

    /* ---------- 추천 목록 · 실행 ---------- */
    const recList = $('#rec-list');
    if (recList) {
      recList.addEventListener('click', (e) => {
        const runBtn = e.target.closest('[data-run]');
        if (runBtn) {
          const recs = (currentSummary() && currentSummary().recs) || [];
          const rec = recs[Number(runBtn.dataset.run)];
          if (!rec) return;
          if (!AL.real) return;
          e.preventDefault();
          launch(rec.name);
          return;
        }
        if (e.target.closest('.score')) { openScoreModal(); }
      });
    }
    const recGrid = $('#rec-grid');
    if (recGrid) {
      recGrid.addEventListener('click', (e) => {
        const item = e.target.closest('[data-scen]');
        if (!item || !AL.real) return;
        launch(item.dataset.scen);
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
        launchAll();
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
      if (closest('#nv-restore')) return restoreNvidia();
      if (closest('#accel-btn')) return toggleAccel();
      if (closest('#bk-save')) return backupNow();
      if (closest('#bk-load')) return restoreBackup();
    };

    /* ---------- 설정 ---------- */
    const settings = $('#s-settings');
    if (settings) {
      settings.addEventListener('click', async (e) => {
        const target = e.target.closest('button, [data-toggle]');
        if (!target) return;

        const toggle = target.closest('[data-toggle]');
        if (toggle) {
          const key = toggle.dataset.toggle;
          const on = !toggle.classList.contains('off');
          if (!AL.real) { toggle.classList.toggle('off', on); return; }
          const result = await call('save_settings', { [key]: !on });
          toggle.classList.toggle('off', on);
          if (result && result.state) applyState(result.state);
          toast(`${key === 'auto_launch' ? '자동 실행' : '자동 저장'} ${!on ? '켜짐' : '꺼짐'}`, true);
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
      if (name === 'settings') renderSettings(AL.state);
    };
    AL.onSwitch = function () {
      if (!AL.state) return;
      applySummaryOnly();
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
    RECS[`${game}-${weapon}`] = (summary.recs || []).map((r) => ({
      name: r.name,
      area: typeof r.area === 'number' ? r.area : areaIdx(r.area_key),
      diff: r.diff || '초급',
      score: r.score === null || r.score === undefined ? null : Math.round(r.score * 10) / 10,
      done: !!r.done,
      delta: typeof r.delta === 'number' ? r.delta : null
    }));
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
     ================================================================== */
  let booted = false;

  async function boot() {
    if (booted) return;
    if (!hasApi('bootstrap')) return;
    booted = true;
    AL.real = true;
    const splash = window.SPLASH;
    if (splash) splash.set(18, '앱 데이터를 불러오는 중…');
    busy(true, '앱 데이터를 불러오는 중…');
    const result = await call('bootstrap');
    busy(false);
    if (!result || !result.ok) {
      AL.real = false;
      if (splash) splash.set(100, '불러오지 못했습니다');
      setTimeout(() => { if (splash) splash.finish(); }, 500);
      toast((result && result.message) || '앱 데이터를 불러오지 못했습니다', false);
      return;
    }
    if (result.state) {
      if (splash) splash.set(58, '훈련 기록 · 티어 계산 중…');
      applyState(result.state);
      AL.ready = true;
    }
    if (splash) splash.set(74, '화면을 그리는 중…');
    requestAnimationFrame(() => {
      /* 첫 프레임을 먼저 보여 준 뒤 무거운 확인(DRS DB 스캔 · 마우스 설정)을 돌린다 */
      if (splash) splash.set(82, '그래픽 설정 확인 중…');
      setTimeout(async () => {
        const nv = await call('get_nvidia_state');
        if (nv && nv.utils) {
          AL.utils = nv.utils;
          renderUtilsState(nv.utils);
          if (nv.state) applyState(nv.state);
        }
        if (splash) splash.set(96, '마무리 중…');
        splash && splash.finish();
      }, 60);
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    bind();
    if (hasApi('bootstrap')) boot();
    else setTimeout(() => { if (hasApi('bootstrap')) boot(); }, 700);
  });
  window.addEventListener('pywebviewready', boot);
  /* 브리지가 끝내 안 올라오면 스플래시가 멈춰 있지 않게 한다 */
  setTimeout(() => {
    if (!booted && window.SPLASH) {
      window.SPLASH.set(100, '미리보기 모드');
      window.SPLASH.finish();
    }
  }, 2600);
  if (document.readyState !== 'loading') {
    bind();
    boot();
  }
})();
