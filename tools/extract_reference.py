#!/usr/bin/env python3
"""Extrait le contenu du modèle de référence dans un JSON compact.

Outil de développement : il sert à comparer les résultats de l'application au
modèle de référence sans avoir à lire `content.xml` à chaque fois.

La feuille n'est jamais modifiée : elle est seulement lue.

Usage :

    uv run python tools/extract_reference.py                    # JSON sur stdout
    uv run python tools/extract_reference.py --sheet Sheet2     # une seule feuille
    uv run python tools/extract_reference.py --output ref.json  # vers un fichier
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"

DEFAULT_SOURCE = Path(__file__).resolve().parent.parent / "reference.ods"


def column_name(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def extract_sheet(sheet) -> dict[str, dict[str, object]]:
    """Cellules non vides d'un modèle, indexées par ligne puis colonne."""
    rows: dict[str, dict[str, object]] = {}
    row_number = 0
    for row in sheet.findall(f"{{{TABLE}}}table-row"):
        repeat = int(row.get(f"{{{TABLE}}}number-rows-repeated", 1))
        cells: dict[str, object] = {}
        column = 0
        for cell in row.findall(f"{{{TABLE}}}table-cell"):
            cell_repeat = int(cell.get(f"{{{TABLE}}}number-columns-repeated", 1))
            text = "".join(cell.itertext()).strip()
            formula = cell.get(f"{{{TABLE}}}formula")
            value = cell.get(f"{{{OFFICE}}}value")
            if text or formula or value is not None:
                if formula or value is not None:
                    entry: dict[str, object] = {}
                    if value is not None:
                        entry["value"] = float(value)
                    if formula:
                        entry["formula"] = formula
                    if text:
                        entry["text"] = text
                    cells[column_name(column)] = entry
                else:
                    cells[column_name(column)] = text
            column += cell_repeat
        if cells:
            for offset in range(repeat):
                rows[str(row_number + offset + 1)] = cells
        row_number += repeat
    return rows


def extract(source: Path) -> dict[str, object]:
    """Parcourt le modèle et renvoie le contenu de chaque feuille."""
    with zipfile.ZipFile(source) as archive:
        root = ElementTree.fromstring(archive.read("content.xml"))

    sheets: dict[str, object] = {}
    for sheet in root.iter(f"{{{TABLE}}}table"):
        name = sheet.get(f"{{{TABLE}}}name")
        sheets[name] = {"rows": extract_sheet(sheet)}
    return {"source": source.name, "sheets": sheets}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--sheet", default=None, help="ne garder qu'un modèle")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    data = extract(args.source)
    if args.sheet:
        data["sheets"] = {args.sheet: data["sheets"][args.sheet]}

    text = json.dumps(data, ensure_ascii=False, indent=1)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
