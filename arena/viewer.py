"""Offline zero-dependency replay animation. Does not execute any fighter code.

The HTML template lives next to this module in viewer_template.html; render_html
inlines it, so generated replay files remain fully self-contained.
"""
import json
from pathlib import Path

_TEMPLATE_PATH = Path(__file__).with_name("viewer_template.html")


def render_html(replay: dict, path: Path) -> None:
    # Escape HTML-significant code points so identifiers cannot inject script tags.
    payload = json.dumps(replay, sort_keys=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = _TEMPLATE_PATH.read_text(encoding="utf-8")
    if "__REPLAY__" not in html:
        raise ValueError("viewer template is missing the __REPLAY__ placeholder")
    path.write_text(html.replace("__REPLAY__", payload), encoding="utf-8")
