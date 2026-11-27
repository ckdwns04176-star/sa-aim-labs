"""화면 캡처 · 한국어 OCR · 화면 변화 지문 (Windows 전용 · 표준 라이브러리만).

DLL 인젝션·후킹 없이, PowerShell 로 ``System.Drawing.CopyFromScreen`` 캡처와
Windows 10/11 내장 OCR(``Windows.Media.Ocr``)만 쓴다.  PowerShell 스크립트는
파일로 떨어뜨리지 않고 ``-EncodedCommand`` 로 넘긴다 — PyInstaller onefile
빌드에서도 따로 챙길 리소스가 없다.

v2.0.3 — OCR 인식률·속도
------------------------
* **DPI 인식**: 앱(WebView2)은 DPI-aware 인데 자식 ``powershell.exe`` 는 기본이
  DPI-unaware 라서, 125%/150% 배율·QHD 에서 ``CopyFromScreen`` 이 엉뚱한 곳을
  캡처했다.  스크립트 맨 앞에서 ``SetProcessDpiAwarenessContext(PER_MONITOR_V2)
  → SetProcessDpiAwareness → SetProcessDPIAware`` 순으로 올린다.
* **1회 캡처 + 메모리 크롭**: 슬롯 수만큼 ``CopyFromScreen`` 을 부르던 것을 전체
  영역 1회 캡처 후 크롭으로 바꿨다(GetPixel/SetPixel 2중 루프 제거).
* **LockBits C# 전처리**: 고대비 반전 · 청록/밝은색 닉네임 마스크를 ``LockBits``
  로 처리해 0.5초 안팎.  작은 한글 폰트는 ``HighQualityBicubic`` 이 획을 뭉개므로
  ``InterpolationMode.NearestNeighbor`` 3~4배 확대 경로를 쓴다.
* **줄 좌표 OCR**(:func:`ocr_region`): 10인 카운트다운처럼 한 화면에 여러 줄이
  있는 경우 ``OcrLine.Words`` 의 BoundingRect 를 돌려줘 해상도와 무관하게
  상단 5명 / 하단 5명을 위치로 나눌 수 있게 한다.

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

# ---------------------------------------------------------------- C# 고속 헬퍼
# PowerShell 의 GetPixel/SetPixel 2중 루프는 5~10슬롯에서 몇 초씩 걸린다.
# LockBits 로 한 번에 읽고 쓰는 헬퍼를 같은 프로세스 안에서 컴파일해 쓴다.
_CS_HELPER = r"""
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;

public static class SaDpi {
    [DllImport("user32.dll")] private static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] private static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("shcore.dll")] private static extern int SetProcessDpiAwareness(int value);
    public static string MakeAware() {
        try { if (SetProcessDpiAwarenessContext(new IntPtr(-4))) return "per-monitor-v2"; } catch {}
        try { if (SetProcessDpiAwareness(2) == 0) return "per-monitor"; } catch {}
        try { if (SetProcessDPIAware()) return "system"; } catch {}
        return "none";
    }
}

public static class SaFast {
    public static Bitmap Crop(Bitmap src, int x, int y, int w, int h) {
        if (w < 1) w = 1;
        if (h < 1) h = 1;
        Bitmap dst = new Bitmap(w, h, PixelFormat.Format32bppArgb);
        using (Graphics g = Graphics.FromImage(dst)) {
            g.PixelOffsetMode = PixelOffsetMode.Half;
            g.DrawImage(src, new Rectangle(0, 0, w, h), new Rectangle(x, y, w, h), GraphicsUnit.Pixel);
        }
        return dst;
    }

    public static Bitmap Scale(Bitmap src, int factor) {
        if (factor < 1) factor = 1;
        int w = src.Width * factor;
        int h = src.Height * factor;
        Bitmap dst = new Bitmap(w, h, PixelFormat.Format32bppArgb);
        using (Graphics g = Graphics.FromImage(dst)) {
            g.InterpolationMode = InterpolationMode.NearestNeighbor;
            g.PixelOffsetMode = PixelOffsetMode.Half;
            g.SmoothingMode = SmoothingMode.None;
            g.DrawImage(src, new Rectangle(0, 0, w, h),
                        new Rectangle(0, 0, src.Width, src.Height), GraphicsUnit.Pixel);
        }
        return dst;
    }

    private static byte[] Load(Bitmap src, out int w, out int h) {
        w = src.Width;
        h = src.Height;
        byte[] buf = new byte[w * h * 4];
        BitmapData sd = src.LockBits(new Rectangle(0, 0, w, h), ImageLockMode.ReadOnly,
                                     PixelFormat.Format32bppArgb);
        try {
            byte[] row = new byte[sd.Stride];
            int bytes = w * 4;
            for (int y = 0; y < h; y++) {
                Marshal.Copy(new IntPtr(sd.Scan0.ToInt64() + (long)y * sd.Stride), row, 0, row.Length);
                Buffer.BlockCopy(row, 0, buf, y * bytes, bytes);
            }
        } finally {
            src.UnlockBits(sd);
        }
        return buf;
    }

    private static Bitmap FromGray(int w, int h, byte[] gray) {
        Bitmap dst = new Bitmap(w, h, PixelFormat.Format32bppArgb);
        BitmapData dd = dst.LockBits(new Rectangle(0, 0, w, h), ImageLockMode.WriteOnly,
                                     PixelFormat.Format32bppArgb);
        try {
            byte[] row = new byte[w * 4];
            for (int y = 0; y < h; y++) {
                for (int x = 0; x < w; x++) {
                    byte v = gray[y * w + x];
                    int i = x * 4;
                    row[i] = v; row[i + 1] = v; row[i + 2] = v; row[i + 3] = 255;
                }
                Marshal.Copy(row, 0, new IntPtr(dd.Scan0.ToInt64() + (long)y * dd.Stride), row.Length);
            }
        } finally {
            dst.UnlockBits(dd);
        }
        return dst;
    }

    // 밝은 획은 검정, 어두운 배경은 흰색 — OCR 이 가장 잘 읽는 반전 고대비.
    public static Bitmap Contrast(Bitmap src) {
        int w, h;
        byte[] px = Load(src, out w, out h);
        byte[] gray = new byte[w * h];
        for (int i = 0; i < w * h; i++) {
            int b = px[i * 4], g = px[i * 4 + 1], r = px[i * 4 + 2];
            int lum = (r * 299 + g * 587 + b * 114) / 1000;
            int v = 255 - Math.Min(255, Math.Max(0, (lum - 48) * 215 / 100));
            gray[i] = (byte)v;
        }
        return FromGray(w, h, gray);
    }

    // 청록 닉네임 + 밝은 흰색/금색 닉네임 → 검정 글자 마스크.
    public static Bitmap Mask(Bitmap src, int cyanOn, int brightCut) {
        int w, h;
        byte[] px = Load(src, out w, out h);
        byte[] gray = new byte[w * h];
        for (int i = 0; i < w * h; i++) {
            int b = px[i * 4], g = px[i * 4 + 1], r = px[i * 4 + 2];
            int lum = (r * 299 + g * 587 + b * 114) / 1000;
            bool cyan = cyanOn == 1 && (g - r) >= 18 && (b - r) >= 18
                        && g >= 82 && b >= 82 && Math.Abs(g - b) <= 115;
            bool bright = lum >= brightCut;
            gray[i] = (cyan || bright) ? (byte)0 : (byte)255;
        }
        return FromGray(w, h, gray);
    }

    public static int[] RowCounts(Bitmap mask) {
        int w, h;
        byte[] px = Load(mask, out w, out h);
        int[] rows = new int[h];
        for (int y = 0; y < h; y++) {
            int c = 0;
            for (int x = 0; x < w; x++) if (px[(y * w + x) * 4 + 2] < 128) c++;
            rows[y] = c;
        }
        return rows;
    }

    public static int[] TightBox(Bitmap mask, int top, int bottom) {
        int w, h;
        byte[] px = Load(mask, out w, out h);
        int minX = w, maxX = -1;
        int from = Math.Max(0, top), to = Math.Min(h - 1, bottom);
        for (int y = from; y <= to; y++) {
            for (int x = 0; x < w; x++) {
                if (px[(y * w + x) * 4 + 2] < 128) {
                    if (x < minX) minX = x;
                    if (x > maxX) maxX = x;
                }
            }
        }
        return new int[] { minX, maxX };
    }
}
"""

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

# 두 스크립트가 함께 쓰는 머리말 — DPI 인식 + C# 헬퍼 + OCR 엔진.
_OCR_HEAD = r"""
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}
Add-Type -AssemblyName System.Drawing
$csSource = @'
__CS__
'@
Add-Type -ReferencedAssemblies 'System.Drawing' -TypeDefinition $csSource
$dpiMode = [SaDpi]::MakeAware()
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType=WindowsRuntime]
__AWAIT__
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
$ocr = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language('ko-KR')))
if (-not $ocr) { $ocr = $engine }

function Invoke-Ocr($bitmap) {
  $ms = New-Object System.IO.MemoryStream
  $ras = $null
  $software = $null
  try {
    $bitmap.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $ms.Position = 0
    $ras = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($ms)
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ras)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $software = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    return Await ($ocr.RecognizeAsync($software)) ([Windows.Media.Ocr.OcrResult])
  } finally {
    if ($software) { try { $software.Dispose() } catch {} }
    if ($ras) { try { $ras.Dispose() } catch {} }
    $ms.Dispose()
  }
}

function Add-OcrLines($bitmap, [ref]$target) {
  $result = Invoke-Ocr $bitmap
  if (-not $result) { return }
  foreach ($line in $result.Lines) {
    if ($line.Text) { $target.Value += [string]$line.Text }
  }
}

# 줄 텍스트 + 줄 좌표(BoundingRect) — 해상도와 무관하게 상/하단을 나누기 위한 것.
function Add-OcrLineBoxes($bitmap, [ref]$target, $scale) {
  $result = Invoke-Ocr $bitmap
  if (-not $result) { return }
  foreach ($line in $result.Lines) {
    $text = [string]$line.Text
    if (-not $text) { continue }
    $minX = [double]::MaxValue; $minY = [double]::MaxValue
    $maxX = -1.0; $maxY = -1.0
    foreach ($word in $line.Words) {
      $r = $word.BoundingRect
      if ($r.X -lt $minX) { $minX = $r.X }
      if ($r.Y -lt $minY) { $minY = $r.Y }
      if (($r.X + $r.Width) -gt $maxX) { $maxX = $r.X + $r.Width }
      if (($r.Y + $r.Height) -gt $maxY) { $maxY = $r.Y + $r.Height }
    }
    if ($maxX -lt 0) { continue }
    $s = [double]$scale
    if ($s -le 0) { $s = 1 }
    $target.Value += @{
      text = $text
      x = [int][math]::Round($minX / $s)
      y = [int][math]::Round($minY / $s)
      w = [int][math]::Round(($maxX - $minX) / $s)
      h = [int][math]::Round(($maxY - $minY) / $s)
    }
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

$debugDirB64 = '__DEBUG_DIR_B64__'
$debugDir = $null
if ($debugDirB64) {
  try {
    $debugDir = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($debugDirB64))
    New-Item -ItemType Directory -Force -Path $debugDir | Out-Null
  } catch { $debugDir = $null }
}

# 슬롯이 흩어져 있어도 화면은 **한 번만** 잡는다 — 캡처한 큰 그림에서
# 메모리 크롭으로 각 슬롯을 떼어낸다 (CopyFromScreen 5~10회 → 1회).
function Capture-Union($rects) {
  $minX = [int]::MaxValue; $minY = [int]::MaxValue
  $maxX = 0; $maxY = 0
  foreach ($r in $rects) {
    if ([int]$r.x -lt $minX) { $minX = [int]$r.x }
    if ([int]$r.y -lt $minY) { $minY = [int]$r.y }
    if (([int]$r.x + [int]$r.w) -gt $maxX) { $maxX = [int]$r.x + [int]$r.w }
    if (([int]$r.y + [int]$r.h) -gt $maxY) { $maxY = [int]$r.y + [int]$r.h }
  }
  if ($maxX -le $minX -or $maxY -le $minY) { return $null }
  $bmp = New-Object System.Drawing.Bitmap(($maxX - $minX), ($maxY - $minY))
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  try {
    $g.CopyFromScreen($minX, $minY, 0, 0, (New-Object System.Drawing.Size(($maxX - $minX), ($maxY - $minY))))
  } finally {
    $g.Dispose()
  }
  $script:originX = $minX
  $script:originY = $minY
  return $bmp
}
"""

_SLOTS_SCRIPT = _OCR_HEAD + r"""
$rects = __RECTS__
$out = @()
$slotIndex = 0
$shotFull = $null
try { $shotFull = Capture-Union $rects }
catch { $shotFull = $null }
foreach ($r in $rects) {
  $slotIndex++
  $x = [int]$r.x; $y = [int]$r.y; $w = [int]$r.w; $h = [int]$r.h
  $originalLines = @()
  $enlargedLines = @()
  $contrastLines = @()
  $cyanLines = @()
  $errors = @()
  $resources = @()
  $cyanPixels = 0
  $captureOk = $false
  $nameRect = @{}
  $rawFile = ''; $maskFile = ''; $contrastFile = ''
  $shot = $null

  try {
    if (-not $shotFull) { throw 'screen capture failed' }
    $shot = [SaFast]::Crop($shotFull, ($x - $originX), ($y - $originY), $w, $h)
    $resources += $shot
    $captureOk = $true
    $rawFile = Save-DebugBitmap $shot $debugDir ("slot_{0}_raw.png" -f $slotIndex)
  } catch {
    $errors += ('capture: ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message)
  }

  if ($captureOk -and $shot) {
    try { Add-OcrLines $shot ([ref]$originalLines) }
    catch { $errors += ('original OCR: ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message) }

    # 3배 NearestNeighbor — 작은 한글은 Bicubic 으로 키우면 획이 뭉개진다.
    try {
      $big = [SaFast]::Scale($shot, 3)
      $resources += $big
      Add-OcrLines $big ([ref]$enlargedLines)
    } catch { $errors += ('enlarged OCR: ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message) }

    try {
      $contrast = [SaFast]::Contrast($shot)
      $resources += $contrast
      $contrastFile = Save-DebugBitmap $contrast $debugDir ("slot_{0}_contrast.png" -f $slotIndex)
      $contrastBig = [SaFast]::Scale($contrast, 3)
      $resources += $contrastBig
      Add-OcrLines $contrastBig ([ref]$contrastLines)
    } catch { $errors += ('contrast OCR: ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message) }

    # 청록 + 밝은 흰색/금색(클랜랭크·토너먼트·10인 화면) 닉네임 마스크.
    try {
      $mask = [SaFast]::Mask($shot, 1, 175)
      $resources += $mask
      $maskFile = Save-DebugBitmap $mask $debugDir ("slot_{0}_cyan.png" -f $slotIndex)
      $rowCounts = [SaFast]::RowCounts($mask)
      for ($yy = 0; $yy -lt $rowCounts.Count; $yy++) { $cyanPixels += [int]$rowCounts[$yy] }

      # 닉네임 줄은 화면 종류마다 Y 가 다르다 — 마스크 안에서 픽셀이 연속으로
      # 모인 가장 강한 가로 띠를 스스로 찾는다.
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
          ($bestEnd - $bestStart + 1) -le [math]::Max(42, [int]($h * 0.9))) {
        $box = [SaFast]::TightBox($mask, $bestStart, $bestEnd)
        if ($box[1] -ge $box[0]) {
          $padX = 4; $padY = 3
          $cropX = [math]::Max(0, $box[0] - $padX)
          $cropY = [math]::Max(0, $bestStart - $padY)
          $cropRight = [math]::Min($w, $box[1] + $padX + 1)
          $cropBottom = [math]::Min($h, $bestEnd + $padY + 1)
          $tightWidth = [math]::Max(1, $cropRight - $cropX)
          $tightHeight = [math]::Max(1, $cropBottom - $cropY)
          $tight = [SaFast]::Crop($mask, $cropX, $cropY, $tightWidth, $tightHeight)
          $resources += $tight
          $nameRect = @{ x = $cropX; y = $cropY; w = $tightWidth; h = $tightHeight }
          $cyanPixels = $bestScore
        }
      }

      if ($tight) {
        $tightBig = [SaFast]::Scale($tight, 4)
        $resources += $tightBig
        Add-OcrLines $tightBig ([ref]$cyanLines)
        Save-DebugBitmap $tight $debugDir ("slot_{0}_text.png" -f $slotIndex) | Out-Null
      } else {
        # 글자 띠를 못 찾았으면 마스크 전체를 넣어 본다 (진단용으로 남긴다).
        $nameRect = @{}
        Add-OcrLines $mask ([ref]$cyanLines)
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
$dpiOk = [string]$dpiMode
if ($shotFull) { try { $shotFull.Dispose() } catch {} }
$payload = @{ ok = $true; dpi = $dpiOk; items = @($out) }
ConvertTo-Json -InputObject $payload -Compress -Depth 7
"""

_REGION_SCRIPT = _OCR_HEAD + r"""
$rects = __RECTS__
$scale = __SCALE__
$r = $rects[0]
$x = [int]$r.x; $y = [int]$r.y; $w = [int]$r.w; $h = [int]$r.h
$lines = @()
$errors = @()
$variant = 'raw'
$shot = $null
$big = $null
try {
  $shot = New-Object System.Drawing.Bitmap($w, $h)
  $g = [System.Drawing.Graphics]::FromImage($shot)
  try { $g.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size($w, $h))) } finally { $g.Dispose() }
  $big = [SaFast]::Scale($shot, $scale)
  Add-OcrLineBoxes $big ([ref]$lines) $scale
  if ($lines.Count -lt 8) {
    $contrast = [SaFast]::Contrast($shot)
    $contrastBig = [SaFast]::Scale($contrast, $scale)
    Add-OcrLineBoxes $contrastBig ([ref]$lines) $scale
    $variant = 'raw+contrast'
    $contrast.Dispose(); $contrastBig.Dispose()
  }
  Save-DebugBitmap $shot $debugDir 'region_raw.png' | Out-Null
} catch {
  $errors += ($_.Exception.GetType().FullName + ': ' + $_.Exception.Message)
} finally {
  if ($big) { $big.Dispose() }
  if ($shot) { $shot.Dispose() }
}
ConvertTo-Json -InputObject @{
  ok = ($errors.Count -eq 0)
  dpi = [string]$dpiMode
  variant = $variant
  rect = @{ x = $x; y = $y; w = $w; h = $h }
  scale = $scale
  lines = @($lines)
  errors = @($errors)
} -Compress -Depth 6
"""

_SIGNATURE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$csSource = @'
__CS__
'@
Add-Type -ReferencedAssemblies 'System.Drawing' -TypeDefinition $csSource
$null = [SaDpi]::MakeAware()
$rects = __RECTS__
$out = @()
foreach ($r in $rects) {
  try {
    $bmp = New-Object System.Drawing.Bitmap([int]$r.w, [int]$r.h)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen([int]$r.x, [int]$r.y, 0, 0, (New-Object System.Drawing.Size([int]$r.w, [int]$r.h)))
    $small = New-Object System.Drawing.Bitmap([int]$r.cols, [int]$r.rows)
    $g2 = [System.Drawing.Graphics]::FromImage($small)
    $g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
    $g2.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::Half
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


def _fill(script: str, **values: str) -> str:
    """``__NAME__`` 자리표시자 치환 — C# 소스가 PowerShell 문자열과 섞이지 않게."""
    out = script.replace("__CS__", _CS_HELPER).replace("__AWAIT__", _AWAIT)
    for key, value in values.items():
        out = out.replace(f"__{key}__", value)
    return out


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
        rows.append("@" + "{" + f"x={x};y={y};w={w};h={h}" + "}")
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


def _debug_b64(debug_dir: str | os.PathLike[str] | None) -> str:
    if debug_dir is None:
        return ""
    return base64.b64encode(os.fspath(debug_dir).encode("utf-8")).decode("ascii")


def ocr_regions(rects: Sequence[Sequence[int]], *, timeout: float = 30.0,
                debug_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """여러 영역을 캡처해 원본·3배 확대·고대비·청록 마스크 4경로로 OCR한다.

    debug_dir를 지정하면 슬롯별 원본/전처리 캡처 PNG도 저장한다. 전체 화면이
    아니라 닉네임 줄 주변만 저장하므로 인식 실패 원인을 확인하기 쉽다.
    """
    cleaned = [r for r in rects if len(r) >= 4 and int(r[2]) > 0 and int(r[3]) > 0]
    if not cleaned:
        return {"ok": False, "supported": available(), "message": "읽을 영역이 없습니다"}

    script = _fill(_SLOTS_SCRIPT, RECTS=_rect_list(cleaned),
                   DEBUG_DIR_B64=_debug_b64(debug_dir))
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
        "dpi": str(data.get("dpi") or ""),
    }


def ocr_region(rect: Sequence[int], *, scale: int = 2, timeout: float = 12.0,
               debug_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """한 영역을 1회 캡처 → 확대 → **줄마다 좌표를 붙여** OCR 한다.

    10인 카운트다운처럼 한 화면에 닉네임이 10줄 있는 경우, 고정 비율 슬롯 10개를
    찍는 대신 게임 창 중앙 영역을 통째로 읽고 ``lines[i]["y"]`` 로 상단/하단을
    나눈다.  좌표는 넘긴 rect 기준 **원본 픽셀**로 돌려준다.
    """
    if len(rect) < 4 or int(rect[2]) <= 0 or int(rect[3]) <= 0:
        return {"ok": False, "supported": available(), "message": "읽을 영역이 없습니다",
                "lines": []}
    script = _fill(_REGION_SCRIPT, RECTS=_rect_list([rect]), SCALE=str(max(1, int(scale))),
                   DEBUG_DIR_B64=_debug_b64(debug_dir))
    data = _run(script, timeout=timeout)
    if not data.get("ok") and not data.get("lines"):
        return {**data, "lines": []}

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for item in (data.get("lines") or []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            box = {k: int(item.get(k) or 0) for k in ("x", "y", "w", "h")}
        except (TypeError, ValueError):
            continue
        key = (text, box["x"] // 8, box["y"] // 8)
        if key in seen:
            continue
        seen.add(key)
        out.append({"text": text, **box})
    out.sort(key=lambda row: (row["y"], row["x"]))
    return {
        "ok": bool(out) or bool(data.get("ok")),
        "supported": True,
        "lines": out,
        "count": len(out),
        "variant": str(data.get("variant") or ""),
        "dpi": str(data.get("dpi") or ""),
        "rect": dict(zip(("x", "y", "w", "h"), (int(v) for v in rect[:4]))),
        "errors": _as_text_lines(data.get("errors")),
        "message": f"{len(out)}줄 인식" if out else "OCR 문자를 찾지 못했습니다",
        "debug_dir": os.fspath(debug_dir) if debug_dir is not None else "",
    }


def signature(rect: Sequence[int], *, cols: int = 8, rows: int = 3,
              timeout: float = 6.0) -> list[float] | None:
    """영역의 저해상도 밝기 지문 — 맵 밴 창이 사라졌는지 판별하는 데 쓴다."""
    x, y, w, h = (int(v) for v in rect[:4])
    if w <= 0 or h <= 0:
        return None
    spec = ("@({" + f"x={x};y={y};w={w};h={h};cols={int(cols)};rows={int(rows)}" + "})")
    data = _run(_fill(_SIGNATURE_SCRIPT, RECTS=spec), timeout=timeout)
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
