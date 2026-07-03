"""
Parse material/filament metadata from OrcaSlicer .gcode.3mf files.

A .gcode.3mf is just a ZIP archive containing:
  Metadata/project_settings.config  -- flat JSON with all slicer settings
  Metadata/slice_info.config         -- XML with per-plate filament usage
  (plus the actual gcode and thumbnails)

We read filament_colour and filament_type from project_settings.config to
build the AD5XMaterialMapping list the printer API needs.

IMPORTANT CAVEAT
----------------
OrcaSlicer embeds *slicer slot indices* (0-based), not IFS *physical slot
numbers*.  The IFS "slot" shown on the printer screen is 1-based and maps
to whichever spool you have loaded in each slot.  We therefore emit mappings
where slotId = toolId + 1 (i.e. slicer slot 0 → IFS slot 1, slot 1 → IFS
slot 2, etc.).  This is the same assumption OrcaSlicer's own FlashForge
upload makes.  If you have spools in a different physical order, you'll need
to swap them on the IFS unit to match.

Returns an empty list for single-colour files (the caller sends those as a
plain upload with no material-station involvement).
"""

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FilamentSlot:
    tool_id: int       # 0-based slicer index
    slot_id: int       # 1-based IFS physical slot
    material_name: str
    colour: str        # #RRGGBB


def _normalise_colour(raw: str) -> str:
    """Ensure colour is #RRGGBB.  OrcaSlicer often stores as #AARRGGBB."""
    raw = raw.strip()
    if not raw.startswith("#"):
        raw = f"#{raw}"
    # Strip alpha channel if present (#AARRGGBB -> #RRGGBB)
    if len(raw) == 9:
        raw = f"#{raw[3:]}"
    return raw.upper()


def extract_filament_slots(filepath: str | Path) -> list[FilamentSlot]:
    """
    Open a .gcode.3mf and return one FilamentSlot per active slicer tool.
    Returns [] for plain .gcode files or 3MFs with no filament metadata.
    """
    path = Path(filepath)
    if not path.suffix.lower() == ".3mf" and ".gcode.3mf" not in path.name.lower():
        return []

    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()

            # Locate the settings config (case-insensitive search)
            config_name = next(
                (n for n in names if n.lower() == "metadata/project_settings.config"),
                None,
            )
            if config_name is None:
                return []

            raw = zf.read(config_name).decode("utf-8", errors="replace")
            config: dict = json.loads(raw)

    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError):
        return []

    colours_raw: list[str] = config.get("filament_colour", [])
    types_raw: list[str] = config.get("filament_type", [])

    if not colours_raw:
        return []

    # Pad types list if shorter than colours list
    types_padded = list(types_raw) + ["PLA"] * max(0, len(colours_raw) - len(types_raw))

    slots: list[FilamentSlot] = []
    for i, (colour, mat) in enumerate(zip(colours_raw, types_padded)):
        try:
            normalised = _normalise_colour(colour)
        except Exception:
            normalised = "#FFFFFF"

        slots.append(
            FilamentSlot(
                tool_id=i,
                slot_id=i + 1,          # IFS slots are 1-based
                material_name=mat.strip() or "PLA",
                colour=normalised,
            )
        )

    return slots
