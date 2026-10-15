"""OCR backends for pctr — Windows (PowerShell) and Linux (rapidocr/tesseract)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .base import OCRWord


# --- Windows PowerShell OCR (preserved from pctr 0.4.0) -------------------------

_OCR_PS = r'''
param([string]$Path)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
  $m = $asTaskGeneric.MakeGenericMethod($type)
  $t = $m.Invoke($null, @($op))
  $t.Wait(-1) | Out-Null
  $t.Result
}
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = $null
$lang = $env:PCTR_OCR_LANG
if ($lang) {
  $null = [Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime]
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language $lang))
  if (-not $engine) { Write-Error ("OCR language not available on this system: " + $lang); exit 2 }
} else {
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  if (-not $engine) {
    $null = [Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime]
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language 'en-US'))
  }
  if (-not $engine) { Write-Error 'no OCR engine available'; exit 2 }
}
$res = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$out = foreach ($line in $res.Lines) { foreach ($w in $line.Words) { [pscustomobject]@{ text = $w.Text; x = [int]$w.BoundingRect.X; y = [int]$w.BoundingRect.Y; w = [int]$w.BoundingRect.Width; h = [int]$w.BoundingRect.Height } } }
ConvertTo-Json -Compress -InputObject @($out)
'''


class WindowsOCR:
    """Windows OCR via PowerShell + Windows.Media.Ocr."""

    def __init__(self):
        import base64
        import json
        import os
        import subprocess

        self._base64 = base64
        self._json = json
        self._os = os
        self._subprocess = subprocess

    def _run(self, image_path: str, lang: str | None = None) -> list[OCRWord]:
        body = _OCR_PS.strip()
        if body.startswith("param"):
            body = body.split("\n", 1)[1]
        ps = "$Path = $env:PCTR_OCR_PATH\n" + body
        enc = self._base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
        env = dict(self._os.environ)
        env["PCTR_OCR_PATH"] = image_path
        if lang:
            env["PCTR_OCR_LANG"] = lang
        else:
            env.pop("PCTR_OCR_LANG", None)
        out = self._subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
            capture_output=True, text=True, env=env,
        )
        txt = (out.stdout or "").strip()
        if not txt:
            raise SystemExit("OCR failed: " + (out.stderr or "").strip()[:400])
        data = self._json.loads(txt, strict=False)
        if isinstance(data, dict):
            data = [data]
        words = []
        for w in data:
            if w and w.get("text"):
                words.append(OCRWord(
                    text=w["text"],
                    x=w["x"], y=w["y"], w=w["w"], h=w["h"],
                ))
        return words

    def ocr(self, image: Any, lang: str | None = None) -> list[OCRWord]:
        # image is a PIL Image on Linux, but WindowsOCR expects a path
        if isinstance(image, str):
            return self._run(image, lang)
        raise SystemExit("WindowsOCR expects a file path, not an image object")


class RapidOCRBackend:
    """Linux OCR via rapidocr-onnxruntime."""

    def __init__(self):
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            self._engine = RapidOCR()
        return self._engine

    def ocr(self, image: Any, lang: str | None = None) -> list[OCRWord]:
        import numpy as np
        engine = self._get_engine()
        img_array = np.array(image.convert("RGB"))
        result, _ = engine(img_array)
        words = []
        if result:
            for box, text, conf in result:
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                x0, y0 = int(min(xs)), int(min(ys))
                x1, y1 = int(max(xs)), int(max(ys))
                words.append(OCRWord(
                    text=text,
                    x=x0, y=y0,
                    w=x1 - x0, h=y1 - y0,
                ))
        return words


class TesseractOCR:
    """Linux OCR fallback via pytesseract + tesseract binary."""

    def __init__(self):
        try:
            import pytesseract
        except ImportError:
            raise SystemExit("TesseractOCR needs pytesseract: pip install pytesseract")
        self._pytesseract = pytesseract

    def ocr(self, image: Any, lang: str | None = None) -> list[OCRWord]:
        import numpy as np
        data = self._pytesseract.image_to_data(
            image, lang=lang or "eng", output_type=self._pytesseract.Output.DICT,
        )
        words = []
        n = len(data.get("text", []))
        for i in range(n):
            txt = data["text"][i]
            if txt and txt.strip():
                words.append(OCRWord(
                    text=txt,
                    x=data["left"][i], y=data["top"][i],
                    w=data["width"][i], h=data["height"][i],
                ))
        return words


class OCRBackend(ABC):
    """Backend interface for OCR dispatch."""

    @abstractmethod
    def ocr(self, image: Any, lang: str | None = None) -> list[OCRWord]: ...
