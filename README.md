# SA AIM LABS

서든어택 에임 훈련 데스크톱 앱 (Windows · PyWebView).
코박스(KovaaK's)·에임랩(Aim Lab) 훈련 기록, 5영역 레이더, 18단계 티어,
NEXON Open API 병영수첩·친구 순위표, NVIDIA Drs 최적화 유틸을 한 화면에 모았습니다.

```
main.py                 엔트리 (창 생성 · 정중앙 배치 · --serve 미리보기 · --drs-apply 상승 헬퍼)
app/api.py              JS ↔ Python 브리지 (window.pywebview.api)
app/nexon.py            서든어택 오픈 API (전적 · 랭크전 100판 집계 · match-detail 디스크 캐시)
app/scoring.py          5영역 평점 · 18단계 티어 · 추천 로테이션
app/drs.py              NVIDIA Drs DB 리더/패처 (계단현상 끄기 = AA compatibility 0x00401241)
app/supabase_sync.py    Supabase Auth + PostgREST (표준 라이브러리만 사용)
app/nvidia.py           NVIDIA Drs 프로필 백업/적용/복구
app/winutil.py          마우스 가속 · steam:// · aimlab:// 실행
ui/index.html           화면 (정적 디자인 + 샘플 데이터 + 첫 로딩 스플래시)
ui/bridge.js            화면 ↔ pywebview.api 연결 (실데이터 반영)
tests/api_test.py       백엔드 테스트 (실응답 캐시 재생 · Drs 패치 · 평점 정규화)
tests/ui_test.py        Playwright UI 테스트 (목 브리지 · 스크린샷)
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
