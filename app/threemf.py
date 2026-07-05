"""
Extract metadata from OrcaSlicer .gcode.3mf files.

Reads Metadata/slice_info.config (XML) for:
  - prediction  → printing_time in seconds
  - layer_ranges → total_layers (last layer index + 1)
"""

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ThreeMFMeta:
    printing_time: int = 0   # seconds
    total_layers: int = 0


def extract_meta(filepath: str | Path) -> ThreeMFMeta:
    path = Path(filepath)
    if ".3mf" not in path.name.lower():
        return ThreeMFMeta()

    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            slice_info_name = next(
                (n for n in names if n.lower() == "metadata/slice_info.config"), None
            )
            if not slice_info_name:
                return ThreeMFMeta()
            xml = zf.read(slice_info_name).decode("utf-8", errors="replace")
            return _parse(xml)
    except Exception:
        return ThreeMFMeta()


def _parse(xml: str) -> ThreeMFMeta:
    meta = ThreeMFMeta()

    # prediction value is print time in seconds
    # <metadata key="prediction" value="468"/>
    m = re.search(r'key=["\']prediction["\'][^>]*value=["\'](\d+)["\']', xml)
    if not m:
        m = re.search(r'value=["\'](\d+)["\'][^>]*key=["\']prediction["\']', xml)
    if m:
        meta.printing_time = int(m.group(1))

    # layer_ranges="0 11" — last number is the last layer index, so total = last + 1
    # Sum across all layer_filament_list entries to find the highest layer index
    ranges = re.findall(r'layer_ranges=["\'](\d+)\s+(\d+)["\']', xml)
    if ranges:
        meta.total_layers = max(int(end) for _, end in ranges) + 1

    return meta


def extract_thumbnail(filepath: str | Path) -> bytes | None:
    """
    Extract the plate thumbnail PNG from a .gcode.3mf archive.
    OrcaSlicer stores it at Thumbnails/plate_1.png (from thumbnail_file key
    in slice_info.config). Falls back to checking common alternative paths.
    Returns raw PNG bytes or None if not found.
    """
    path = Path(filepath)
    if ".3mf" not in path.name.lower():
        return None

    candidates = [
        "Thumbnails/plate_1.png",
        "Thumbnails/thumbnail.png",
        "Metadata/plate_1.png",
        "thumbnail.png",
    ]

    try:
        with zipfile.ZipFile(path, "r") as zf:
            names_lower = {n.lower(): n for n in zf.namelist()}

            # Prefer the path named in slice_info.config
            slice_name = names_lower.get("metadata/slice_info.config")
            if slice_name:
                xml = zf.read(slice_name).decode("utf-8", errors="replace")
                m = re.search(r'key=["\']thumbnail_file["\'][^>]*value=["\']([^"\']+)["\']', xml)
                if m:
                    candidates.insert(0, m.group(1))

            for candidate in candidates:
                actual = names_lower.get(candidate.lower())
                if actual:
                    return zf.read(actual)
    except Exception:
        pass

    return None