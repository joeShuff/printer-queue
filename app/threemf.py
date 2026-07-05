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
    Extract the correct plate thumbnail PNG from a .gcode.3mf archive.

    OrcaSlicer stores the active plate index in slice_info.config:
      <metadata key="index" value="3"/>

    Thumbnails are at Metadata/plate_N.png. We read the index and load
    that specific plate's thumbnail, falling back to plate_1 if not found.
    """
    path = Path(filepath)
    if ".3mf" not in path.name.lower():
        return None

    try:
        with zipfile.ZipFile(path, "r") as zf:
            names_lower = {n.lower(): n for n in zf.namelist()}

            # Read plate index from slice_info.config
            plate_index = 1
            slice_name = names_lower.get("metadata/slice_info.config")
            if slice_name:
                xml = zf.read(slice_name).decode("utf-8", errors="replace")
                m = re.search(r'key=["\']index["\'][^>]*value=["\'](\d+)["\']', xml)
                if m:
                    plate_index = int(m.group(1))

            # Try the specific plate, then fall back down to plate_1
            candidates = [f"metadata/plate_{plate_index}.png"]
            if plate_index != 1:
                candidates.append("metadata/plate_1.png")
            candidates += ["thumbnails/plate_1.png", "thumbnails/thumbnail.png", "thumbnail.png"]

            for candidate in candidates:
                actual = names_lower.get(candidate)
                if actual:
                    return zf.read(actual)
    except Exception:
        pass

    return None