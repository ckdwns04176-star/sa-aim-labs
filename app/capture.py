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
PS_TIMEOUT = 12.0

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
$engine = $null
foreach ($tag in @('ko-KR', 'ko')) {
  try { $lang = New-Object Windows.Globalization.Language($tag)
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang) } catch { $engine = $null }
  if ($engine) { break }
}
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $engine) { Write-Output '{"ok":false,"message":"Windows OCR 언어팩(한국어)을 찾지 못했습니다"}'; exit 0 }
$out = @()
foreach ($r in $rects) {
  $x = [int]$r.x; $y = [int]$r.y; $w = [int]$r.w; $h = [int]$r.h
  $lines = @()
  try {
    $bmp = New-Object System.Drawing.Bitmap($w, $h)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size($w, $h)))
    $big = New-Object System.Drawing.Bitmap(($w * 4), ($h * 4))
    $g2 = [System.Drawing.Graphics]::FromImage($big)
    $g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g2.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $attr = New-Object System.Drawing.Imaging.ImageAttributes
    $cm = New-Object System.Drawing.Imaging.ColorMatrix
    $cm.Matrix00 = 0.30; $cm.Matrix01 = 0.30; $cm.Matrix02 = 0.30
    $cm.Matrix10 = 0.59; $cm.Matrix11 = 0.59; $cm.Matrix12 = 0.59
    $cm.Matrix20 = 0.11; $cm.Matrix21 = 0.11; $cm.Matrix22 = 0.11
    $cm.Matrix33 = 1.0; $cm.Matrix44 = 1.0
    $cm.Matrix40 = -0.55; $cm.Matrix41 = -0.55; $cm.Matrix42 = -0.55
    $cm.Matrix00 = -0.30; $cm.Matrix01 = -0.30; $cm.Matrix02 = -0.30
    $cm.Matrix10 = -0.59; $cm.Matrix11 = -0.59; $cm.Matrix12 = -0.59
    $cm.Matrix20 = -0.11; $cm.Matrix21 = -0.11; $cm.Matrix22 = -0.11
    $cm.Matrix40 = 1.55; $cm.Matrix41 = 1.55; $cm.Matrix42 = 1.55
    $cm.Matrix33 = 1.0; $cm.Matrix44 = 1.0
    $attr.SetColorMatrix($cm)
    $dst = New-Object System.Drawing.Rectangle(0, 0, ($w * 4), ($h * 4))
    $g2.DrawImage($bmp, $dst, 0, 0, $w, $h, [System.Drawing.GraphicsUnit]::Pixel, $attr)
    $ms = New-Object System.IO.MemoryStream
    $big.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $ms.Position = 0
    $ras = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($ms)
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ras)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $software = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($software)) ([Windows.Media.Ocr.OcrResult])
    foreach ($line in $result.Lines) {
      if ($line.Text) { $lines += $line.Text }
    }
    $g.Dispose(); $g2.Dispose(); $bmp.Dispose(); $big.Dispose(); $ms.Dispose()
  } catch {
    $lines += ('__ERR__' + $_.Exception.Message)
  }
  $out += @{ lines = @($lines) }
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


def ocr_regions(rects: Sequence[Sequence[int]], *, timeout: float = PS_TIMEOUT) -> dict[str, Any]:
    """화면의 여러 사각형을 한 번에 캡처해 한국어 OCR 줄 목록을 돌려준다."""
    cleaned = [r for r in rects if len(r) >= 4]
    if not cleaned:
        return {"ok": False, "supported": available(), "message": "읽을 영역이 없습니다"}
    script = _OCR_SCRIPT.replace("__AWAIT__", _AWAIT).replace("__RECTS__", _rect_list(cleaned))
    data = _run(script, timeout=timeout)
    if not data.get("ok"):
        return data
    items = data.get("items") or []
    lines: list[list[str]] = []
    for item in items:
        row = [str(v) for v in ((item or {}).get("lines") or [])]
        lines.append(row)
    return {"ok": True, "supported": True, "lines": lines, "count": len(lines)}


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
