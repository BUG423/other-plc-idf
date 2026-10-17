"""Read a screenshot with the Windows built-in WinRT OCR engine.

No GUI automation, device access, Tesseract, or downloaded language models are
used.  OCR text is evidence to interpret, never proof of compiler success by
itself.  The PowerShell script is embedded so installed wheels need no resource
files.  Microsoft API references:
https://learn.microsoft.com/uwp/api/windows.media.ocr.ocrengine
https://learn.microsoft.com/uwp/api/windows.graphics.imaging.bitmapdecoder
"""

from __future__ import annotations

import base64
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import PureWindowsPath
from typing import Sequence

from plc_idf.interop import (
    POWERSHELL_WSL_PATH, InteropError, RetryPolicy, run_command,
)


# Path and language arguments are decoded from data, never evaluated as code.
# All asynchronous WinRT operations are converted to Tasks through the .NET
# Framework projection available in Windows PowerShell 5.1.
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__REQUEST_BASE64__')) | ConvertFrom-Json
$stage = 'winrt-initialization'
$stream = $null
$bitmap = $null
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and
        $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    if ($null -eq $asTask) { throw 'WinRT asynchronous Task projection is unavailable' }
    function Await-WinRT($operation, [Type]$resultType) {
        $method = $asTask.MakeGenericMethod($resultType)
        $task = $method.Invoke($null, @($operation))
        $task.GetAwaiter().GetResult()
    }
    $available = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag })
    $engine = $null
    foreach ($tag in $request.languages) {
        $language = New-Object Windows.Globalization.Language([string]$tag)
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
        if ($null -ne $engine) { break }
    }
    if ($null -eq $engine) {
        [ordered]@{ schema_version = 1; status = 'unsupported'; text = ''; lines = @();
            language = $null; available_languages = $available;
            error = 'No requested Windows OCR language is installed'; stage = 'language-selection'
        } | ConvertTo-Json -Depth 6 -Compress
        exit 0
    }
    $stage = 'read-image'
    $file = Await-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync([string]$request.path)) ([Windows.Storage.StorageFile])
    $stream = Await-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
    $transform.ScaledWidth = [uint32][Math]::Round($decoder.PixelWidth * $request.scale)
    $transform.ScaledHeight = [uint32][Math]::Round($decoder.PixelHeight * $request.scale)
    $transform.InterpolationMode = [Windows.Graphics.Imaging.BitmapInterpolationMode]::Cubic
    $outputWidth = $transform.ScaledWidth
    $outputHeight = $transform.ScaledHeight
    if ($null -ne $request.region) {
        $region = $request.region
        if ($region[0] + $region[2] -gt $decoder.PixelWidth -or $region[1] + $region[3] -gt $decoder.PixelHeight) {
            throw 'OCR region extends outside source image'
        }
        $bounds = New-Object Windows.Graphics.Imaging.BitmapBounds
        $bounds.X = [uint32][Math]::Round($region[0] * $request.scale)
        $bounds.Y = [uint32][Math]::Round($region[1] * $request.scale)
        $bounds.Width = [uint32][Math]::Round($region[2] * $request.scale)
        $bounds.Height = [uint32][Math]::Round($region[3] * $request.scale)
        $transform.Bounds = $bounds
        $outputWidth = $bounds.Width
        $outputHeight = $bounds.Height
    }
    if ($outputWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or
        $outputHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension) {
        throw ('Image exceeds Windows OCR maximum dimension of ' + [Windows.Media.Ocr.OcrEngine]::MaxImageDimension)
    }
    $bitmap = Await-WinRT ($decoder.GetSoftwareBitmapAsync(
        [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied,
        $transform, [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,
        [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage
    )) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $stage = 'recognize'
    $recognized = Await-WinRT ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines = @($recognized.Lines | ForEach-Object { $_.Text })
    [ordered]@{ schema_version = 1; status = 'ok'; text = [string]$recognized.Text;
        lines = $lines; language = $engine.RecognizerLanguage.LanguageTag;
        available_languages = $available; error = $null; stage = $stage
    } | ConvertTo-Json -Depth 6 -Compress
} catch {
    $status = 'error'
    if ($stage -eq 'winrt-initialization') { $status = 'unsupported' }
    [ordered]@{ schema_version = 1; status = $status; text = ''; lines = @();
        language = $null; available_languages = @(); error = $_.Exception.ToString(); stage = $stage
    } | ConvertTo-Json -Depth 6 -Compress
} finally {
    if ($null -ne $bitmap) { $bitmap.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
}
"""


@dataclass
class OcrResult:
    status: str
    text: str = ""
    lines: list[str] = field(default_factory=list)
    language: str | None = None
    available_languages: list[str] = field(default_factory=list)
    error: str | None = None
    stage: str = "invocation"
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    windows_path: str = ""
    scale: float = 1.0
    region: tuple[int, int, int, int] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict:
        return {"schema_version": 1, "engine": "Windows.Media.Ocr",
                "ok": self.ok, **asdict(self)}


def _arguments(windows_path: str, languages: Sequence[str], powershell_exe: str,
               scale: float, region: tuple[int, int, int, int] | None) -> list[str]:
    request = base64.b64encode(json.dumps({"path": windows_path, "languages": list(languages),
                                          "scale": scale, "region": region},
                                         ensure_ascii=False).encode("utf-8")).decode("ascii")
    script = _SCRIPT.replace("__REQUEST_BASE64__", request)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [powershell_exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-STA",
            "-EncodedCommand", encoded]


def read_image_text(windows_path: str, *, powershell_exe: str = POWERSHELL_WSL_PATH,
                    runner=None, timeout_s: float = 30.0,
                    languages: Sequence[str] = ("zh-Hans", "zh-CN", "en-US"),
                    scale: float = 1.0,
                    region: tuple[int, int, int, int] | None = None) -> OcrResult:
    """OCR an existing PNG via one bounded, read-only PowerShell call.

    ``runner`` follows ``interop.run_command``'s subprocess-like injection
    signature: ``runner(argv, timeout=..., cwd=None)``.  Unavailable WinRT/OCR or
    language packs returns ``status='unsupported'``; file/decode/process errors
    return ``status='error'``.  Raw process output is retained for evidence.
    Missing files are not OCR capability failures.  An empty successful OCR
    result is valid and must not be interpreted as successful compilation.
    ``region=(x,y,width,height)`` uses original-image pixels; ``scale`` optionally
    enlarges small text during in-memory decoding. Neither changes the PNG.
    """
    if not isinstance(windows_path, str) or not windows_path or "\x00" in windows_path:
        raise ValueError("windows_path must be a nonempty Windows PNG path without NUL")
    path = PureWindowsPath(windows_path)
    if not path.is_absolute() or path.suffix.lower() != ".png":
        raise ValueError("windows_path must be an absolute drive or UNC path to a PNG")
    if isinstance(languages, (str, bytes)) or not languages or any(
            not isinstance(item, str) or not item or "\x00" in item for item in languages):
        raise ValueError("languages must be a nonempty sequence of language tag strings")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < timeout_s <= 120:
        raise ValueError("timeout_s must be greater than 0 and at most 120 seconds")
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or not 1 <= scale <= 4:
        raise ValueError("scale must be a finite number from 1 to 4")
    if region is not None and (not isinstance(region, (tuple, list)) or len(region) != 4 or any(
            type(value) is not int for value in region) or min(region[:2]) < 0 or min(region[2:]) <= 0):
        raise ValueError("region must be (nonnegative x, nonnegative y, positive width, positive height)")
    argv = _arguments(windows_path, languages, powershell_exe, scale, region)
    try:
        process = run_command(argv, policy=RetryPolicy(timeout_s=timeout_s, retries=0),
                              runner=runner, retryable=False)
    except (InteropError, OSError) as exc:
        return OcrResult("unsupported", error=str(exc), windows_path=windows_path, scale=scale, region=region)
    raw = {"returncode": process.returncode, "stdout": process.stdout,
           "stderr": process.stderr, "timed_out": process.timed_out,
           "windows_path": windows_path, "scale": scale, "region": region}
    if not process.ok:
        return OcrResult("error", error="Windows OCR process timed out" if process.timed_out
                         else "Windows OCR PowerShell process failed", **raw)
    try:
        payload = json.loads(process.stdout.strip().lstrip("\ufeff"))
        if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
            raise ValueError("unexpected Windows OCR protocol version")
        if not isinstance(payload.get("status"), str) or payload["status"] not in {"ok", "unsupported", "error"}:
            raise ValueError("invalid Windows OCR status")
        if not isinstance(payload.get("text"), str) or not isinstance(payload.get("lines"), list) or any(
                not isinstance(line, str) for line in payload["lines"]):
            raise ValueError("invalid Windows OCR text/lines")
        if payload.get("language") is not None and not isinstance(payload["language"], str):
            raise ValueError("invalid Windows OCR language")
        available = payload.get("available_languages", [])
        if not isinstance(available, list) or any(not isinstance(tag, str) for tag in available):
            raise ValueError("invalid Windows OCR available_languages")
        if payload.get("error") is not None and not isinstance(payload["error"], str):
            raise ValueError("invalid Windows OCR error")
        if payload.get("status") == "ok" and not payload.get("language"):
            raise ValueError("successful OCR response requires the recognizer language")
        if payload["status"] == "ok" and payload.get("error"):
            raise ValueError("successful OCR response cannot contain an error")
        if payload["status"] != "ok" and not payload.get("error"):
            raise ValueError("failed/unsupported OCR response requires an error description")
        return OcrResult(status=payload["status"], text=payload["text"], lines=payload["lines"],
                         language=payload.get("language"), available_languages=available,
                         error=payload.get("error"), stage=str(payload.get("stage", "unknown")), **raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return OcrResult("error", error=f"invalid Windows OCR response: {exc}", **raw)
