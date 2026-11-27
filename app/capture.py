"""화면 캡처 · 한국어 OCR · 화면 변화 지문 (Windows 전용 · 표준 라이브러리만).

DLL 인젝션·후킹 없이, PowerShell 로 ``System.Drawing.CopyFromScreen`` 캡처와
Windows 10/11 내장 OCR(``Windows.Media.Ocr``)만 쓴다.  PowerShell 스크립트는
파일로 떨어뜨리지 않고 ``-EncodedCommand`` 로 넘긴다 — PyInstaller onefile
빌드에서도 따로 챙길 리소스가 없다.

반환은 모두 JSON 문자열 → dict/list 로 파싱해서 돌려주고, 실패하면
``{"ok": False, "message": ...}`` 를 준다.  오버레이는 이 실패를 화면에
그대로 보여 주고 게임은 건드리지 않는다.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
from typing import Any, Iterable, Sequence

CREATE_NO_WINDOW = 0x08000000
PS_TIMEOUT = 20.0

# ---------------------------------------------------------------- PowerShell
_AWAIT = r"""
function Await($WinRtTask, $ResultType) {
  $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
  $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
  $netTask = $asTask.Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  $netTask.Result
}
"""

_OCR_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType=WindowsRuntime]
__AWAIT__
$rects = __RECTS__
$cyanOnly = __CYAN_ONLY__
$engine = $null
foreach ($tag in @('ko-KR', 'ko')) {
  try { $lang = New-Object Windows.Globalization.Language($tag)
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang) } catch { $engine = $null }
  if ($engine) { break }
}
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $engine) { Write-Output '{"ok":false,"message":"Windows OCR 언어팩(한국어)을 찾지 못했습니다"}'; exit 0 }

# OCR 엔진에 비트맵을 전달하고 인식된 한 줄을 대상 배열에 추가한다.
function Add-OcrLines($bitmap, $ocrEngine, [ref]$target) {
  $ms = New-Object System.IO.MemoryStream
  try {
    $bitmap.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $ms.Position = 0
    $ras = [System.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($ms)
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ras)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $software = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($ocrEngine.RecognizeAsync($software)) ([Windows.Media.Ocr.OcrResult])
    foreach ($line in $result.Lines) {
      if ($line.Text) { $target.Value += [string]$line.Text }
    }
  } finally {
    $ms.Dispose()
  }
}

# 파티 카드의 청록색 닉네임 글자만 검게 남기고 나머지를 흰색으로 바꾼다.
# 회색 클랜명·카드 아이콘·배경 무늬가 OCR 후보에 들어가지 않게 한다.
function Add-CyanOnlyOcr($bitmap, $ocrEngine, [ref]$target) {
  $mask = $null
  $big = $null
  $scaleGraphics = $null
  try {
    $mask = New-Object System.Drawing.Bitmap($bitmap.Width, $bitmap.Height)
    $black = [System.Drawing.Color]::FromArgb(255, 0, 0, 0)
    $white = [System.Drawing.Color]::FromArgb(255, 255, 255, 255)
    for ($yy = 0; $yy -lt $bitmap.Height; $yy++) {
      for ($xx = 0; $xx -lt $bitmap.Width; $xx++) {
        $px = $bitmap.GetPixel($xx, $yy)
        $isCyan = (($px.G - $px.R) -ge 15) -and
                  (($px.B - $px.R) -ge 15) -and
                  ($px.G -ge 100) -and ($px.B -ge 100) -and
                  ([math]::Abs($px.G - $px.B) -le 100)
        if ($isCyan) { $mask.SetPixel($xx, $yy, $black) }
        else { $mask.SetPixel($xx, $yy, $white) }
      }
    }

    # 원본 마스크와 3배 확대본을 각각 인식해 작은 한글 획의 누락을 줄인다.
    Add-OcrLines $mask $ocrEngine $target
    $scale = 3
    $big = New-Object System.Drawing.Bitmap(($bitmap.Width * $scale), ($bitmap.Height * $scale))
    $scaleGraphics = [System.Drawing.Graphics]::FromImage($big)
    $scaleGraphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $scaleGraphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $scaleGraphics.DrawImage($mask, 0, 0, $big.Width, $big.Height)
    Add-OcrLines $big $ocrEngine $target
  } finally {
    if ($scaleGraphics) { $scaleGraphics.Dispose() }
    if ($big) { $big.Dispose() }
    if ($mask) { $mask.Dispose() }
  }
}

$out = @()
foreach ($r in $rects) {
  $x = [int]$r.x; $y = [int]$r.y; $w = [int]$r.w; $h = [int]$r.h
  $lines = @()
  $errors = @()
  $resources = @()
  $bmp = $null
  try {
    $bmp = New-Object System.Drawing.Bitmap($w, $h)
    $resources += $bmp
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $resources += $g
    $g.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size($w, $h)))
  } catch {
    $errors += ('capture: ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message)
  }

  if ($bmp) {
    if ($cyanOnly) {
      # 파티 대기실: 닉네임 색상만 남겨 클랜명과 배경 문자를 제거한다.
      try {
        Add-CyanOnlyOcr $bmp $engine ([ref]$lines)
      } catch {
        $errors += ('cyan-only OCR: ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message)
      }
    } else {
      # 일반 OCR: 각 전처리를 독립 실행해 어느 한 단계의 실패가 결과를 지우지 않게 한다.
      try {
        Add-OcrLines $bmp $engine ([ref]$lines)
      } catch {
        $errors += ('original OCR: ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message)
      }

      $scale = 2
      $dst = New-Object System.Drawing.Rectangle(0, 0, ($w * $scale), ($h * $scale))

      # 2배 NearestNeighbor
      try {
        $big = New-Object System.Drawing.Bitmap(($w * $scale), ($h * $scale))
        $resources += $big
        $g2 = [System.Drawing.Graphics]::FromImage($big)
        $resources += $g2
        $g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
        $g2.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half
        $g2.DrawImage($bmp, $dst, 0, 0, $w, $h, [System.Drawing.GraphicsUnit]::Pixel)
        Add-OcrLines $big $engine ([ref]$lines)
      } catch {
        $errors += ('nearest-neighbor OCR: ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message)
      }

      # 2배 회색조. 이 단계가 실패해도 앞의 OCR 결과를 버리지 않는다.
      try {
        $gray = New-Object System.Drawing.Bitmap(($w * $scale), ($h * $scale))
        $resources += $gray
        $g3 = [System.Drawing.Graphics]::FromImage($gray)
        $resources += $g3
        $g3.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
        $g3.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half
        $attr = New-Object System.Drawing.Imaging.ImageAttributes
        $resources += $attr
        $cm = New-Object System.Drawing.Imaging.ColorMatrix
        $cm.Matrix00 = 0.30; $cm.Matrix01 = 0.30; $cm.Matrix02 = 0.30
        $cm.Matrix10 = 0.59; $cm.Matrix11 = 0.59; $cm.Matrix12 = 0.59
        $cm.Matrix20 = 0.11; $cm.Matrix21 = 0.11; $cm.Matrix22 = 0.11
        $cm.Matrix33 = 1.0; $cm.Matrix44 = 1.0
        $attr.SetColorMatrix($cm)
        $g3.DrawImage($bmp, $dst, 0, 0, $w, $h, [System.Drawing.GraphicsUnit]::Pixel, $attr)
        Add-OcrLines $gray $engine ([ref]$lines)
      } catch {
        $errors += ('grayscale OCR: ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message)
      }
    }
  }

  for ($i = $resources.Count - 1; $i -ge 0; $i--) {
    try { $resources[$i].Dispose() } catch {}
  }
  # 오류 메시지는 OCR 텍스트(lines)에 절대 섞지 않는다.
  $out += @{ lines = @($lines); errors = @($errors) }
}
$payload = @{ ok = $true; items = $out }
ConvertTo-Json -InputObject $payload -Compress -Depth 5
"""

_SIGNATURE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$rects = __RECTS__
$out = @()
foreach ($r in $rects) {
  try {
    $bmp = New-Object System.Drawing.Bitmap([int]$r.w, [int]$r.h)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen([int]$r.x, [int]$r.y, 0, 0, (New-Object System.Drawing.Size([int]$r.w, [int]$r.h)))
    $small = New-Object System.Drawing.Bitmap([int]$r.cols, [int]$r.rows)
    $g2 = [System.Drawing.Graphics]::FromImage($small)
    $g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g2.DrawImage($bmp, 0, 0, [int]$r.cols, [int]$r.rows)
    $vals = @()
    for ($yy = 0; $yy -lt $r.rows; $yy++) {
      for ($xx = 0; $xx -lt $r.cols; $xx++) {
        $p = $small.GetPixel($xx, $yy)
        $vals += [math]::Round(($p.R * 0.30 + $p.G * 0.59 + $p.B * 0.11), 0)
      }
    }
    $out += @{ ok = $true; values = @($vals) }
    $g.Dispose(); $g2.Dispose(); $bmp.Dispose(); $small.Dispose()
  } catch {
    $out += @{ ok = $false; message = $_.Exception.Message }
  }
}
ConvertTo-Json -InputObject @{ ok = $true; items = $out } -Compress -Depth 5
"""


def available() -> bool:
    return os.name == "nt"


def _powershell() -> str:
    return "powershell.exe" if os.name == "nt" else "pwsh"


def _run(script: str, timeout: float = PS_TIMEOUT) -> dict[str, Any]:
    if not available():
        return {"ok": False, "supported": False,
                "message": "화면 캡처·OCR은 Windows 에서만 동작합니다"}
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            [_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "supported": True, "message": f"PowerShell 실행 실패 · {exc}"}
    raw = proc.stdout.decode("utf-8", "replace").strip()
    if not raw:
        err = proc.stderr.decode("utf-8", "replace").strip()
        return {"ok": False, "supported": True,
                "message": f"캡처 결과가 없습니다 · {err[:200] or 'PowerShell 오류'}"}
    try:
        data = json.loads(raw)
    except ValueError:
        return {"ok": False, "supported": True, "message": f"캡처 응답 해석 실패 · {raw[:160]}"}
    if not isinstance(data, dict):
        return {"ok": False, "supported": True, "message": "캡처 응답 형식 오류"}
    return data


def _rect_list(rects: Sequence[Sequence[int]]) -> str:
    rows = []
    for rect in rects:
        x, y, w, h = (int(v) for v in rect[:4])
        if w <= 0 or h <= 0:
            continue
        rows.append("@{" + f"x={x};y={y};w={w};h={h}" + "}")
    return "@(" + ",".join(rows) + ")"


def ocr_regions(rects: Sequence[Sequence[int]], *, cyan_only: bool = False,
                timeout: float = PS_TIMEOUT) -> dict[str, Any]:
    """화면의 여러 사각형을 캡처해 한국어 OCR 줄 목록을 돌려준다.

    cyan_only=True이면 청록색 글자만 남겨 파티 닉네임과 회색 클랜명을 분리한다.
    """
    cleaned = [r for r in rects if len(r) >= 4]
    if not cleaned:
        return {"ok": False, "supported": available(), "message": "읽을 영역이 없습니다"}
    script = (_OCR_SCRIPT.replace("__AWAIT__", _AWAIT)
              .replace("__RECTS__", _rect_list(cleaned))
              .replace("__CYAN_ONLY__", "$true" if cyan_only else "$false"))
    data = _run(script, timeout=timeout)
    if not data.get("ok"):
        return data
    items = data.get("items") or []
    lines: list[list[str]] = []
    errors: list[str] = []
    for index, item in enumerate(items):
        item = item or {}
        # 구버전 PowerShell이 오류를 lines에 섞어 반환한 경우도 후보에서 제외한다.
        row = [str(v) for v in (item.get("lines") or [])
               if not str(v).strip().startswith("__ERR__")]
        lines.append(row)
        for error in item.get("errors") or []:
            message = str(error).strip()
            if message:
                errors.append(f"영역 {index + 1}: {message[:300]}")
    has_text = any(any(str(line).strip() for line in row) for row in lines)
    if not has_text and errors:
        return {"ok": False, "supported": True, "lines": lines,
                "errors": errors, "count": len(lines),
                "message": "OCR 이미지 처리 실패 · " + errors[0]}
    return {"ok": True, "supported": True, "lines": lines,
            "errors": errors, "count": len(lines)}


def signature(rect: Sequence[int], *, cols: int = 8, rows: int = 3,
              timeout: float = 6.0) -> list[float] | None:
    """영역의 저해상도 밝기 지문 — 맵 밴 창이 사라졌는지 판별하는 데 쓴다."""
    x, y, w, h = (int(v) for v in rect[:4])
    if w <= 0 or h <= 0:
        return None
    spec = ("@({" + f"x={x};y={y};w={w};h={h};cols={int(cols)};rows={int(rows)}" + "})")
    data = _run(_SIGNATURE_SCRIPT.replace("__RECTS__", spec), timeout=timeout)
    if not data.get("ok"):
        return None
    items = data.get("items") or []
    if not items or not (items[0] or {}).get("ok"):
        return None
    try:
        return [float(v) for v in items[0].get("values") or []]
    except (TypeError, ValueError):
        return None


def signature_delta(a: Iterable[float] | None, b: Iterable[float] | None) -> float:
    """두 지문의 평균 절대 차이 (0~255).  화면이 바뀌면 커진다."""
    left = [float(v) for v in (a or [])]
    right = [float(v) for v in (b or [])]
    if not left or len(left) != len(right):
        return 0.0
    return sum(abs(x - y) for x, y in zip(left, right)) / len(left)
