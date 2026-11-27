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
$debugDirB64 = '__DEBUG_DIR_B64__'
$debugDir = $null
if ($debugDirB64) {
  try {
    $debugDir = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($debugDirB64))
    New-Item -ItemType Directory -Force -Path $debugDir | Out-Null
  } catch { $debugDir = $null }
}
$engine = $null
foreach ($tag in @('ko-KR', 'ko')) {
  try {
    $lang = New-Object Windows.Globalization.Language($tag)
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
  } catch { $engine = $null }
  if ($engine) { break }
}
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $engine) {
  Write-Output '{"ok":false,"message":"Windows OCR 언어팩(한국어)을 찾지 못했습니다"}'
  exit 0
}

# Windows OCR에 PNG 비트맵을 전달한다.
# 중요: AsRandomAccessStream 확장 클래스의 실제 네임스페이스는 System.IO다.
function Add-OcrLines($bitmap, $ocrEngine, [ref]$target) {
  $ms = New-Object System.IO.MemoryStream
  $ras = $null
  $software = $null
  try {
    $bitmap.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $ms.Position = 0
    $ras = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($ms)
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ras)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $software = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($ocrEngine.RecognizeAsync($software)) ([Windows.Media.Ocr.OcrResult])
    foreach ($line in $result.Lines) {
      if ($line.Text) { $target.Value += [string]$line.Text }
    }
  } finally {
    if ($software) { try { $software.Dispose() } catch {} }
    if ($ras) { try { $ras.Dispose() } catch {} }
    $ms.Dispose()
  }
}

function Save-DebugBitmap($bitmap, $folder, $fileName) {
  if (-not $folder -or -not $bitmap) { return '' }
  try {
    $full = Join-Path $folder $fileName
    $bitmap.Save($full, [System.Drawing.Imaging.ImageFormat]::Png)
    return $full
  } catch { return '' }
}

$out = @()
$slotIndex = 0
foreach ($r in $rects) {
  $slotIndex++
  $x = [int]$r.x; $y = [int]$r.y; $w = [int]$r.w; $h = [int]$r.h
  $originalLines = @()
  $enlargedLines = @()
  $contrastLines = @()
  $cyanLines = @()
  $errors = @()
  $resources = @()
  $bmp = $null
  $enlarged = $null
  $contrast = $null
  $mask = $null
  $cyanPixels = 0
  $captureOk = $false
  $nameRect = @{}
  $rawFile = ''
  $maskFile = ''
  $contrastFile = ''

  try {
    $bmp = New-Object System.Drawing.Bitmap($w, $h)
    $resources += $bmp
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $resources += $g
    $g.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size($w, $h)))
    $captureOk = $true
    $rawFile = Save-DebugBitmap $bmp $debugDir ("slot_{0}_raw.png" -f $slotIndex)
  } catch {
    $errors += ('capture: ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message)
  }

  if ($captureOk -and $bmp) {
    # 1) 원본 캡처 OCR
    try { Add-OcrLines $bmp $engine ([ref]$originalLines) }
    catch { $errors += ('original OCR: ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message) }

    # 2) 3배 고품질 확대 OCR
    try {
      $scale = 3
      $enlarged = New-Object System.Drawing.Bitmap(($w * $scale), ($h * $scale))
      $resources += $enlarged
      $g2 = [System.Drawing.Graphics]::FromImage($enlarged)
      $resources += $g2
      $g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
      $g2.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
      $g2.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
      $g2.DrawImage($bmp, 0, 0, $enlarged.Width, $enlarged.Height)
      Add-OcrLines $enlarged $engine ([ref]$enlargedLines)
    } catch { $errors += ('enlarged OCR: ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message) }

    # 3) 고대비 흑백 전처리 OCR. 밝은 닉네임 획을 검정, 어두운 배경을 흰색으로 만든다.
    try {
      $contrast = New-Object System.Drawing.Bitmap($w, $h)
      $resources += $contrast
      for ($yy = 0; $yy -lt $h; $yy++) {
        for ($xx = 0; $xx -lt $w; $xx++) {
          $px = $bmp.GetPixel($xx, $yy)
          $lum = [int][math]::Round(($px.R * 0.299) + ($px.G * 0.587) + ($px.B * 0.114))
          $v = [int][math]::Round(255 - [math]::Min(255, [math]::Max(0, ($lum - 48) * 2.15)))
          if ($v -lt 0) { $v = 0 }; if ($v -gt 255) { $v = 255 }
          $contrast.SetPixel($xx, $yy, [System.Drawing.Color]::FromArgb(255, $v, $v, $v))
        }
      }
      $contrastFile = Save-DebugBitmap $contrast $debugDir ("slot_{0}_contrast.png" -f $slotIndex)
      Add-OcrLines $contrast $engine ([ref]$contrastLines)
    } catch { $errors += ('contrast OCR: ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message) }

    # 4) 청록색 닉네임 마스크 OCR. 회색 클랜명과 카드 아이콘을 최대한 제외한다.
    try {
      $mask = New-Object System.Drawing.Bitmap($w, $h)
      $resources += $mask
      $white = [System.Drawing.Color]::FromArgb(255, 255, 255, 255)
      $black = [System.Drawing.Color]::FromArgb(255, 0, 0, 0)
      $rowCounts = @()
      for ($yy = 0; $yy -lt $h; $yy++) {
        $rowCount = 0
        for ($xx = 0; $xx -lt $w; $xx++) {
          $px = $bmp.GetPixel($xx, $yy)
          $isCyan = (($px.G - $px.R) -ge 18) -and
                    (($px.B - $px.R) -ge 18) -and
                    ($px.G -ge 82) -and ($px.B -ge 82) -and
                    ([math]::Abs($px.G - $px.B) -le 115)
          if ($isCyan) { $mask.SetPixel($xx, $yy, $black); $rowCount++ }
          else { $mask.SetPixel($xx, $yy, $white) }
        }
        $rowCounts += $rowCount
        $cyanPixels += $rowCount
      }
      $maskFile = Save-DebugBitmap $mask $debugDir ("slot_{0}_cyan.png" -f $slotIndex)

      # 화면 종류마다 닉네임의 Y 좌표가 달라서, crop 안에서 청록 픽셀이
      # 연속해서 모여 있는 가장 강한 가로 글자 줄을 자동으로 찾는다.
      $rowThreshold = [math]::Max(2, [int][math]::Ceiling($w * 0.008))
      $bestScore = 0; $bestStart = -1; $bestEnd = -1
      $runScore = 0; $runStart = -1; $lastActive = -100
      for ($yy = 0; $yy -lt $h; $yy++) {
        if ($rowCounts[$yy] -ge $rowThreshold) {
          if ($runStart -lt 0 -or ($yy - $lastActive) -gt 3) {
            if ($runScore -gt $bestScore -and $runStart -ge 0) {
              $bestScore = $runScore; $bestStart = $runStart; $bestEnd = $lastActive
            }
            $runStart = $yy; $runScore = 0
          }
          $runScore += [int]$rowCounts[$yy]
          $lastActive = $yy
        }
      }
      if ($runScore -gt $bestScore -and $runStart -ge 0) {
        $bestScore = $runScore; $bestStart = $runStart; $bestEnd = $lastActive
      }

      $tight = $null
      if ($bestStart -ge 0 -and ($bestEnd - $bestStart + 1) -ge 5 -and
          ($bestEnd - $bestStart + 1) -le 42) {
        $minX = $w; $maxX = -1
        for ($yy = $bestStart; $yy -le $bestEnd; $yy++) {
          for ($xx = 0; $xx -lt $w; $xx++) {
            $px = $bmp.GetPixel($xx, $yy)
            $isCyan = (($px.G - $px.R) -ge 18) -and
                      (($px.B - $px.R) -ge 18) -and
                      ($px.G -ge 82) -and ($px.B -ge 82) -and
                      ([math]::Abs($px.G - $px.B) -le 115)
            if ($isCyan) { if ($xx -lt $minX) { $minX = $xx }; if ($xx -gt $maxX) { $maxX = $xx } }
          }
        }
        if ($maxX -ge $minX) {
          $padX = 4; $padY = 3
          $cropX = [math]::Max(0, $minX - $padX)
          $cropY = [math]::Max(0, $bestStart - $padY)
          $cropRight = [math]::Min($w, $maxX + $padX + 1)
          $cropBottom = [math]::Min($h, $bestEnd + $padY + 1)
          $tightWidth = [math]::Max(1, $cropRight - $cropX)
          $tightHeight = [math]::Max(1, $cropBottom - $cropY)
          $tight = $mask.Clone((New-Object System.Drawing.Rectangle($cropX, $cropY, $tightWidth, $tightHeight)), $mask.PixelFormat)
          $resources += $tight
          $nameRect = @{ x = $cropX; y = $cropY; w = $tightWidth; h = $tightHeight }
          $cyanPixels = $bestScore
        }
      }

      if ($tight) {
        # 실제 글자 바운딩 박스만 원본 크기와 3배 확대 크기로 OCR한다.
        Add-OcrLines $tight $engine ([ref]$cyanLines)
        $maskBig = New-Object System.Drawing.Bitmap(($tight.Width * 3), ($tight.Height * 3))
        $resources += $maskBig
        $gm = [System.Drawing.Graphics]::FromImage($maskBig)
        $resources += $gm
        $gm.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $gm.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $gm.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $gm.DrawImage($tight, 0, 0, $maskBig.Width, $maskBig.Height)
        Add-OcrLines $maskBig $engine ([ref]$cyanLines)
        Save-DebugBitmap $tight $debugDir ("slot_{0}_text.png" -f $slotIndex) | Out-Null
      } else {
        # 색상 글자가 검출되지 않은 경우 전체 마스크를 남겨 진단할 수 있게 한다.
        $nameRect = @{}
        Add-OcrLines $mask $engine ([ref]$cyanLines)
      }
    } catch { $errors += ('cyan-mask OCR: ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message) }
  }

  $allLines = @($originalLines) + @($enlargedLines) + @($contrastLines) + @($cyanLines)
  $variants = @{
    original = @($originalLines)
    enlarged = @($enlargedLines)
    contrast = @($contrastLines)
    cyan_mask = @($cyanLines)
  }
  for ($i = $resources.Count - 1; $i -ge 0; $i--) {
    try { $resources[$i].Dispose() } catch {}
  }
  $out += @{
    slot = $slotIndex
    rect = @{ x = $x; y = $y; w = $w; h = $h }
    capture_ok = $captureOk
    cyan_pixels = $cyanPixels
    name_rect = $nameRect
    lines = @($allLines)
    variants = $variants
    errors = @($errors)
    debug_raw = $rawFile
    debug_cyan = $maskFile
    debug_contrast = $contrastFile
  }
}
$payload = @{ ok = $true; items = @($out) }
ConvertTo-Json -InputObject $payload -Compress -Depth 7
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


def _as_text_lines(value: Any) -> list[str]:
    """PowerShell JSON 배열이 문자열 하나로 축약된 경우까지 안전하게 정규화한다."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)] if str(value).strip() else []


def ocr_regions(rects: Sequence[Sequence[int]], *, timeout: float = 30.0,
                debug_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """여러 영역을 캡처해 원본·3배 확대·고대비·청록 마스크 4경로로 OCR한다.

    debug_dir를 지정하면 슬롯별 원본/전처리 캡처 PNG도 저장한다. 전체 화면이
    아니라 닉네임 줄 주변만 저장하므로 인식 실패 원인을 확인하기 쉽다.
    """
    cleaned = [r for r in rects if len(r) >= 4 and int(r[2]) > 0 and int(r[3]) > 0]
    if not cleaned:
        return {"ok": False, "supported": available(), "message": "읽을 영역이 없습니다"}

    debug_b64 = ""
    if debug_dir is not None:
        import base64 as _base64
        debug_b64 = _base64.b64encode(os.fspath(debug_dir).encode("utf-8")).decode("ascii")

    script = (_OCR_SCRIPT.replace("__AWAIT__", _AWAIT)
              .replace("__RECTS__", _rect_list(cleaned))
              .replace("__DEBUG_DIR_B64__", debug_b64))
    data = _run(script, timeout=timeout)
    if not data.get("ok"):
        return data

    items = data.get("items") or []
    if isinstance(items, dict):
        items = [items]
    lines: list[list[str]] = []
    variant_lines: list[dict[str, list[str]]] = []
    capture_ok: list[bool] = []
    cyan_pixels: list[int] = []
    rect_details: list[dict[str, int]] = []
    name_rects: list[dict[str, int]] = []
    errors: list[str] = []

    for index, item in enumerate(items):
        item = item if isinstance(item, dict) else {}
        all_lines = [line for line in _as_text_lines(item.get("lines"))
                     if not line.strip().startswith("__ERR__")]
        lines.append(all_lines)
        variants_raw = item.get("variants") or {}
        variants: dict[str, list[str]] = {}
        for method in ("original", "enlarged", "contrast", "cyan_mask"):
            variants[method] = [line for line in _as_text_lines(variants_raw.get(method))
                                if not line.strip().startswith("__ERR__")]
        variant_lines.append(variants)
        capture_ok.append(bool(item.get("capture_ok", True)))
        try:
            cyan_pixels.append(int(item.get("cyan_pixels") or 0))
        except (TypeError, ValueError):
            cyan_pixels.append(0)
        rect = item.get("rect") or {}
        if isinstance(rect, dict):
            rect_details.append({k: int(rect.get(k) or 0) for k in ("x", "y", "w", "h")})
        else:
            rect_details.append({})
        name_rect = item.get("name_rect") or {}
        if isinstance(name_rect, dict):
            name_rects.append({k: int(name_rect.get(k) or 0) for k in ("x", "y", "w", "h")})
        else:
            name_rects.append({})
        for error in _as_text_lines(item.get("errors")):
            errors.append(f"영역 {index + 1}: {error[:300]}")

    has_text = any(any(line.strip() for line in row) for row in lines)
    any_capture = any(capture_ok)
    if not any_capture:
        message = "화면 캡처에 실패했습니다"
        if errors:
            message += " · " + errors[0]
    elif not has_text:
        message = "화면 캡처는 됐지만 OCR 문자를 찾지 못했습니다"
    else:
        message = "OCR 캡처 완료"

    return {
        "ok": True,
        "supported": True,
        "lines": lines,
        "variant_lines": variant_lines,
        "capture_ok": capture_ok,
        "cyan_pixels": cyan_pixels,
        "rects": rect_details,
        "name_rects": name_rects,
        "errors": errors,
        "count": len(lines),
        "message": message,
        "debug_dir": os.fspath(debug_dir) if debug_dir is not None else "",
    }


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
