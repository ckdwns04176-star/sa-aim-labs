# SA AIM LABS

서든어택 에임 훈련 데스크톱 앱 (Windows · PyWebView).
코박스(KovaaK's)·에임랩(Aim Lab) 훈련 기록, 5영역 레이더, 18단계 티어,
NEXON Open API 병영수첩·친구 순위표, NVIDIA Drs 최적화 유틸을 한 화면에 모았습니다.

```
main.py                 엔트리 (창 생성 · 정중앙 배치 · --serve 미리보기 · --drs-apply 상승 헬퍼)
app/api.py              JS ↔ Python 브리지 (window.pywebview.api)
app/nexon.py            서든어택 오픈 API (전적 · 랭크전 100판 집계 · match-detail 디스크 캐시)
app/scoring.py          통합 에임 프로필 · 5영역 평점 · 18단계 티어 · 무기별 추천 · 에임랩 52종
app/drs.py              NVIDIA Drs DB 리더/패처 (계단현상 끄기 = AA compatibility 0x00401241)
app/supabase_sync.py    Supabase Auth + PostgREST (표준 라이브러리만 사용)
app/nvidia.py           NVIDIA Drs 프로필 백업/적용/복구
app/winutil.py          마우스 가속 · 전원 옵션/게임 모드 · steam:// · aimlab:// 실행
app/kovak.py            코박스 stats/*.csv 점수 인식 · 플레이리스트(.json/.plo) 생성
app/updates.py          GitHub Releases 새 버전 확인 (표준 라이브러리만)
app/tray.py             시스템 트레이 아이콘 + Ctrl+Shift+A 전역 단축키 (Win32 ctypes)
ui/index.html           화면 (정적 디자인 + 샘플 데이터 + 첫 로딩 스플래시)
ui/bridge.js            화면 ↔ pywebview.api 연결 (실데이터 반영)
tests/api_test.py       백엔드 테스트 (실응답 캐시 재생 · Drs 패치 · 평점 정규화)
tests/ui_test.py        Playwright UI 테스트 (목 브리지 · 스크린샷)
tests/v110_test.py      v1.1.0 검증 (통합 점수 · 추천 UX · 모달 · 스크롤 · 순위표 · 창 고정)
tests/v120_test.py      v1.2.0 검증 (코박스 인식 · 1:1 비교 · 전후 비교 · 리포트 카드 · 코칭 등 13종)
supabase/schema.sql     user_profiles 테이블 + RLS (SQL Editor 에 1회 실행)
.github/workflows/      windows-latest 에서 .exe 빌드
```

## 소스로 실행

```bash
python -m pip install -r requirements.txt
python main.py                 # 앱 창
python main.py --serve 8710    # 브라우저 미리보기 (샘플 데이터, http://127.0.0.1:8710/index.html)
```

## Windows .exe 빌드

`main` 브랜치에 푸시하거나 Actions 에서 `Build Windows EXE` 를 수동 실행하면
`windows-latest` 에서 아래 명령으로 빌드합니다.

```powershell
pyinstaller --noconfirm --onefile --windowed --icon assets/app.ico --name "SA_Aim_Labs" ^
  --add-data "ui;ui" --add-data "assets;assets" --add-data "config.json;." ^
  --collect-all webview main.py
```

> `--collect-all webview` 는 pywebview 가 플랫폼 백엔드(edgechromium)를 동적으로 import 하기 때문에 필요합니다.

결과물: `dist/SA_Aim_Labs.exe` (Actions 아티팩트 `SA_Aim_Labs-windows`, `latest-build` 릴리즈 첨부)
실행하려면 Windows 10/11 + **Microsoft Edge WebView2 Runtime** 이 필요합니다.

## 설정

1. 앱 → **설정 · API 연동**
   - NEXON Open API 키(`open.api.nexon.com`), 서든어택 닉네임 저장 → **저장 후 전적 확인**
   - 클라우드 저장소 → **로그인 / 회원가입** (서버 주소·공개 키는 앱에 기본 내장, `고급 서버 설정` 안에 있음)
2. Supabase → **SQL Editor** 에 `supabase/schema.sql` 내용을 붙여 1회 실행
   (user_profiles 테이블 + `auth.uid() = user_id` RLS 정책)
3. 이메일 로그인/회원가입 후 **☁️ 올리기 / 내려받기** 로 PC 간 기록 동기화

## v1.1.0 — 통합 점수 · 에임랩 확충 · 추천 UX

- **통합 에임 프로필**: 코박스 · 에임랩 · 스나 · 라플 기록을 하나로 합쳐 종합 점수 · 18단계
  티어 · 5영역 밸런스 · 연속/누적 훈련일 · 일별 추이를 계산합니다(`scoring.profile_values`).
  게임별 점수 단위가 달라 영역마다 0~100 평점으로 환산해 평균합니다.
- **토글의 의미**: 왼쪽 위 `코박스/에임랩` · `스나/라플` 은 점수를 나누지 않고
  **오늘의 추천 5종 · 더 연습하기의 시나리오 필터 + 실행 프로그램**만 고릅니다.
  각 기록에는 `trainer`(game) · `weapon` · `scenarios[].raw_score` 가 남아 스나/라플 유저 판별은 그대로입니다.
- **무기별 추천 철학**: 스나 = 롱 트래킹 제외(플릭 · 마이크로 · 반응/동체 · 스위칭),
  라플 = 트래킹 2칸 · 스위칭 · 헤드라인/마이크로 · 플릭.
- **에임랩 시나리오 52종**(스나 25 · 라플 27, 중복 제외 34종 · 실제 게임 과제명) + 코박스 40종.
- **추천 5종 UX**: 각 행에 오늘 내 점수(미기록이면 `미기록`) · 클릭하면 모달에서
  점수 입력/수정 + 저장 + ▶ 게임에서 재생. 같은 시나리오를 다시 적으면 오늘 기록이 갱신(중복 없음).
- **+ 더 연습하기**: 오늘 목록에 없는 시나리오를 가장 약한 영역 위주로 3종 추가(앱을 다시 켜도 유지).
- **화면 스크롤**: `.screen` 이 창 높이를 넘으면 스크롤(다크 슬림 스크롤바) — 병영수첩 맨 아래
  약점 · 맵별 분석까지 도달. 레이더 상단 라벨이 잘리지 않게 viewBox/좌표 조정.
- **친구 순위표**: 파티 RP 도 솔로와 같은 `엠블럼 + 티어명 + RP` 형태, 같은 정렬 규칙.
- **창 크기 고정**: 서든어택 저해상도 실행 후 바탕화면 복귀 시 DPI·해상도 변화로 창이 커지지 않게
  `winutil.WindowGuard` 가 0.3초 간격으로 실측해 논리 1200×750 으로 되돌리고 화면 밖 이탈을 막습니다.

## v1.2.0 — 13가지 신규 기능

- **📥 코박스 점수 자동 인식**: `stats/*.csv`(Steam 기본 경로 자동 탐색 + 설정에서 폴더 지정)의
  `Score:` 값을 읽어 오늘 추천 5종·더 연습하기와 이름이 겹치는 항목만 채워 저장합니다.
  창을 다시 열 때 자동으로 채우는 옵션(`코박스 점수 자동 채움`)도 있습니다. 추가 API 0건.
- **⚔ 친구 1:1 전적 비교**: 순위표에서 친구 행을 클릭하면 저장된 값만으로
  솔로·파티 RP · 최근 100판 승률 · K/D · 평균 딜량 · 주무기 성향을 나란히 비교합니다(API 0건).
- **🧭 최근 100판 플레이 코칭**: 병영수첩 상단에 승률·K/D·딜량·약한/강한 맵·솔로/파티 격차·
  최근 5판 흐름을 규칙으로 조합한 3~4줄 코칭을 보여 줍니다(캐시 데이터만 사용).
- **📊 훈련일 vs 미훈련일 랭크전 비교**: 대시보드에서 훈련한 날과 안 한 날의
  승률·K/D·평균 딜량을 나란히 비교합니다(표본 5판 미만이면 안내 문구).
- **📸 오늘의 에임 리포트 카드(PNG)**: 종합 점수·티어·5영역·솔로/파티 티어를 담은
  900×1180 다크 카드를 캔버스로 그려 `%APPDATA%\SA_Aim_Labs\reports` 에 저장합니다.
- **⬆ 새 버전 확인**: 시작할 때 GitHub Releases `latest` 를 백그라운드로 확인해
  상단 바에 `새 버전 있음` 배너를 띄웁니다(실패·오프라인은 조용히 무시, 설정 탭에 수동 확인 버튼).
- **⚡ 5분 워밍업**: 지금 트레이너·무기에 맞는 짧은 핵심 3종을 골라 모달에서 바로 실행합니다.
- **⚡ 고성능 전원 + 게임 모드**: `powercfg` 로 고성능/최고 성능 전원 옵션과
  `AutoGameModeEnabled` 를 한 번에 적용하고, 적용 전 GUID·레지스트리 값을 저장해 한 번에 복구합니다.
- **🎯 주간 훈련 목표**: 주 N일 출석 · 주 N판 목표와 이번 주 진행률 게이지(기본 주 5일·25판).
- **📜 플레이리스트 내보내기**: 오늘 추천 5종/워밍업 3종을 코박스 Playlists 폴더에
  `scenario_name`/`play_Count` 스키마 그대로 `.json` + `.plo` 로 저장합니다.
- **🏆 NEW PB**: 시나리오 점수를 저장할 때 내 최고 기록을 넘으면 토스트로 알리고,
  추천 행에 `🏆 PB` 배지를 붙입니다.
- **🟩 훈련 잔디 캘린더**: 최근 14주 일별 훈련량을 4단계 색으로 보여 주는 GitHub 스타일 히트맵.
- **🪟 트레이 + 호출 단축키**: `Shell_NotifyIconW` 트레이 아이콘(좌클릭 토글·우클릭 메뉴)과
  `Ctrl+Shift+A` 전역 단축키로 창을 숨기기/보이기. 설정에서 닫기=트레이 숨기기, 단축키 ON/OFF.

## 동작 메모

- **첫 로딩**: 스플래시(로고 합쳐짐 + 진행률)가 먼저 뜨고, 로컬 데이터 로드 → 티어/시나리오 계산 →
  그래픽 설정 확인(무거운 Drs 스캔은 첫 프레임 뒤로 미룸) 순으로 채워진다.
- **점수 단위**: 시나리오에 나온 실제 점수를 그대로 입력합니다(KovaaK's 152 · Aim Lab 92,500).
  레이더·티어용 0~100 평점은 `scoring.GAME_REF`(kovak 200 · aimlab 100,000)로 환산합니다.
- **계단현상 끄기**: 프로필 팩을 덮어쓰지 않고, 라이브 `nvdrsdb0/1.bin` 의 서든 프로필
  `Antialiasing compatibility(0x00D55F7D)` 값을 `0x00401241` 로 **제자리 패치**합니다(2바이트, 길이·오프셋 불변).
  적용 전 Drs 폴더 전체를 백업하고, 값이 이미 `0x00401241` 이면 그대로 둡니다.
- **NEXON 호출 한도**: `test_` 키는 일일 1,000건. 경기 상세(match-detail)는 디스크에 캐시해
  이미 본 `match_id` 는 다시 부르지 않고, 순위표 갱신은 상세 호출 0건(캐시만 사용)입니다.
  한도를 넘으면 `오늘 넥슨 API 일일 호출 한도(1,000건)를 초과했어요. 자정 이후 초기화됩니다` 토스트가 뜹니다.
- **창 이동/배치**: 프레임 없는 창은 `drag_window()`(ReleaseCapture + WM_NCLBUTTONDOWN)로 OS 가 직접 옮기고,
  시작 위치는 기본 모니터 작업 영역 기준 정중앙입니다.

## 저장 위치 · 보안

- 훈련 기록·설정: `%APPDATA%\SA_Aim_Labs\data.json`
  (실행 파일 옆에 `portable.txt` 를 두면 `./data/` 로 이동 — PC방용)
- `data.json` 에는 **NEXON API 키와 Supabase 로그인 토큰**이 들어갑니다. `.gitignore` 에 포함되어 있으며 절대 커밋하지 않습니다.
- `config.json` 의 `SUPABASE_ANON_KEY` 는 클라이언트 공개용(publishable) 키입니다.
  데이터는 Supabase RLS 로 본인 행만 읽고 쓸 수 있습니다.
- NEXON `test_` 키는 발급자 본인 계정 전용입니다. 배포용으로는 `live_` 키 + 중계 서버가 필요합니다.
