"""report.html: one self-contained interactive page (no network access needed)."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

ASSETS = Path(__file__).parent / "assets"
PLACEHOLDER = re.compile(r"/\*\{\{(\w+)\}\}\*/|\{\{(\w+)\}\}")


def to_html(doc: dict[str, Any]) -> str:
    parts = {
        "TITLE": html.escape(f"crgraph · {doc['range']['label']}"),
        "CSS": (ASSETS / "report.css").read_text(),
        "CYTOSCAPE": (ASSETS / "vendor" / "cytoscape.min.js").read_text(),
        "CYTOSCAPE_DAGRE": (ASSETS / "vendor" / "cytoscape-dagre.js").read_text(),
        "APP": (ASSETS / "report.js").read_text(),
        # no '<', '>' or '&' in the JSON: '</script>' or '<!--<script>' from repo content would break the page
        "DATA": json.dumps(doc, ensure_ascii=False)
        .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"),
    }
    template = (ASSETS / "report.html").read_text()
    return PLACEHOLDER.sub(lambda m: parts[m.group(1) or m.group(2)], template)
