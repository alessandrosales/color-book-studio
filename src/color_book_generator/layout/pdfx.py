"""Print-shop export: convert a PDF to PDF/X-3 with Ghostscript.

Ghostscript's pdfwrite produces PDF/X-3 (not X-1a). Colour is converted to DeviceCMYK
for the cover and to DeviceGray for the line-art interior (gray keeps black as pure K
instead of a four-colour rich black). The output intent ICC defaults to Ghostscript's
generic CMYK profile; set ``CBG_PDFX_ICC`` to the press profile the print shop asks for
(for example ISO Coated v2 / FOGRA39, distributed by the ECI).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ColorMode = Literal["cmyk", "gray"]

_GS_ICC_DIRS = (
    Path("/usr/share/color/icc/ghostscript"),
    Path("/usr/local/share/ghostscript/iccprofiles"),
)
_DEFAULT_ICC = {"cmyk": "default_cmyk.icc", "gray": "default_gray.icc"}
TIMEOUT_S = 180


class PdfxError(RuntimeError):
    """Raised when the PDF/X conversion cannot run or fails."""


@dataclass(frozen=True)
class PdfxResult:
    path: Path
    icc: Path
    output_condition: str
    custom_icc: bool


def ghostscript_binary() -> str | None:
    return shutil.which("gs") or shutil.which("gswin64c")


def _find_icc(mode: ColorMode) -> tuple[Path, bool]:
    if mode == "cmyk":
        override = os.getenv("CBG_PDFX_ICC", "").strip()
        if override:
            path = Path(override).expanduser()
            if not path.is_file():
                raise PdfxError(f"perfil ICC configurado em CBG_PDFX_ICC nao existe: {path}")
            return path, True
    for directory in _GS_ICC_DIRS:
        candidate = directory / _DEFAULT_ICC[mode]
        if candidate.is_file():
            return candidate, False
    raise PdfxError("perfil ICC do Ghostscript nao encontrado; defina CBG_PDFX_ICC")


def available() -> bool:
    if ghostscript_binary() is None:
        return False
    try:
        _find_icc("cmyk")
    except PdfxError:
        return False
    return True


def _ps_string(text: str) -> str:
    """Escape text for a PostScript (string) literal."""
    clean = "".join(ch for ch in text if ch.isprintable())
    return clean.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdfx_def(title: str, icc: Path, mode: ColorMode, condition: str) -> str:
    components = 4 if mode == "cmyk" else 1
    return f"""%!
[ /GTS_PDFXVersion (PDF/X-3:2002)
  /Title ({_ps_string(title)})
  /Trapped /False
/DOCINFO pdfmark

[/_objdef {{icc_PDFX}} /type /stream /OBJ pdfmark
[{{icc_PDFX}} << /N {components} >> /PUT pdfmark
[{{icc_PDFX}} ({_ps_string(str(icc))}) (r) file /PUT pdfmark

[/_objdef {{OutputIntent_PDFX}} /type /dict /OBJ pdfmark
[{{OutputIntent_PDFX}} <<
  /Type /OutputIntent
  /S /GTS_PDFX
  /OutputCondition ({_ps_string(condition)})
  /OutputConditionIdentifier (Custom)
  /RegistryName (http://www.color.org)
  /DestOutputProfile {{icc_PDFX}}
>> /PUT pdfmark
[{{Catalog}} << /OutputIntents [ {{OutputIntent_PDFX}} ] >> /PUT pdfmark
"""


def convert_to_pdfx(src: Path, dst: Path, *, mode: ColorMode, title: str) -> PdfxResult:
    """Convert ``src`` to PDF/X-3 at ``dst`` (fonts embedded, colour converted)."""
    gs = ghostscript_binary()
    if gs is None:
        raise PdfxError("Ghostscript (gs) nao esta instalado; instale para exportar PDF/X")
    icc, custom = _find_icc(mode)
    condition = icc.stem if custom else f"Ghostscript {icc.stem}"
    strategy = "CMYK" if mode == "cmyk" else "Gray"
    with tempfile.TemporaryDirectory(prefix="cbg-pdfx-") as tmp:
        definition = Path(tmp) / "PDFX_def.ps"
        definition.write_text(_pdfx_def(title, icc, mode, condition), encoding="latin-1", errors="replace")
        args = [
            gs,
            "-dPDFX",
            "-dBATCH",
            "-dNOPAUSE",
            "-dSAFER",
            # The PDF/X definition only needs to read the ICC profile.
            f"--permit-file-read={icc}",
            "-dQUIET",
            "-sDEVICE=pdfwrite",
            "-dPDFSETTINGS=/prepress",
            "-dEmbedAllFonts=true",
            "-dSubsetFonts=true",
            "-dCompatibilityLevel=1.4",
            f"-sColorConversionStrategy={strategy}",
            f"-sProcessColorModel=Device{strategy}",
            f"-sOutputICCProfile={icc}",
            f"-sOutputFile={dst}",
            str(definition),
            str(src),
        ]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired as exc:
            raise PdfxError("a conversao PDF/X demorou demais") from exc
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        raise PdfxError("falha na conversao PDF/X: " + " ".join(detail))
    return PdfxResult(path=dst, icc=icc, output_condition=condition, custom_icc=custom)
