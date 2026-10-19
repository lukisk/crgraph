"""Adds the saved `--explain` review of the demo change to a generated demo report (CI has no API key).

    python docs/add_review.py <report-dir> docs/demo-review.txt

docs/demo-review.txt is real `--explain` output for `main...feature/quick-buy` of docs/demo_repo.py.
"""
import json
import sys
from pathlib import Path

from crgraph.render.html import to_html
from crgraph.render.jsonout import to_json
from crgraph.render.markdown import to_markdown
from crgraph.review import parse_review

out, text = Path(sys.argv[1]), Path(sys.argv[2]).read_text().strip()
doc = json.loads((out / "delta.json").read_text())
doc["explain"], doc["review"] = text, parse_review(text)
(out / "delta.json").write_text(to_json(doc))
(out / "summary.md").write_text(to_markdown(doc))
(out / "report.html").write_text(to_html(doc))
