"""Frontend XSS smoke test (T8).

ARCHITECTURE.md flagged user-text interpolation into innerHTML as a known,
unverified risk. Auditing app.js found escapeHTML() already existed and
was used in most places, but several real gaps: the calendar grid and
week-report views interpolated workout titles raw (including inside an
HTML attribute), and the vacation-planner preview did too. Workout titles
come from Garmin Connect calendar items -- third-party/Runna-originated
text, not something this app controls (see test_builders.py's
TestDeleteFilterSafety for confirmed real-world examples of that
third-party naming). Fixed by wrapping every such site in escapeHTML();
this test makes sure that holds going forward.

No DOM/jsdom dependency: escapeHTML() is a pure string function, so it's
extracted from app.js and evaluated directly via Node (present on
GitHub Actions' ubuntu-latest runners by default, and on this sandbox).
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).parent.parent / "app.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available in this environment")


def _run_escape_html(payload: str) -> str:
    src = APP_JS.read_text()
    m = re.search(r"function escapeHTML\(s\)\{.*?\n\}", src, re.S)
    assert m, "escapeHTML() definition not found in app.js -- did it move/get renamed?"
    script = m.group(0) + "\nconsole.log(JSON.stringify(escapeHTML(process.argv[1])));"
    out = subprocess.run(["node", "-e", script, payload],
                          capture_output=True, text=True, check=True)
    import json
    return json.loads(out.stdout)


class TestEscapeHTML:
    def test_img_onerror_payload_is_inert(self):
        out = _run_escape_html('<img src=x onerror=alert(1)>')
        assert "<img" not in out
        assert "&lt;img" in out

    def test_script_tag_payload_is_inert(self):
        out = _run_escape_html('</script><script>alert(1)</script>')
        assert "<script" not in out

    def test_attribute_breakout_quotes_are_escaped(self):
        """Must be safe to drop into title="...", which several call
        sites do (e.g. the calendar grid chip's title attribute)."""
        out = _run_escape_html('"><svg onload=alert(1)>')
        assert '"' not in out
        assert "&quot;" in out

    def test_plain_text_is_unchanged(self):
        """Sanity: normal workout titles shouldn't get mangled."""
        assert _run_escape_html("6x800") == "6x800"
        assert _run_escape_html("16mi MP Finish") == "16mi MP Finish"


class TestKnownRiskySitesUseEscaping:
    """Static regression guard: the specific lines this audit fixed must
    keep routing third-party text through escapeHTML (or, for the
    onclick-JS-string-context sites, through the quote-stripping pattern
    that's the only thing that actually works there -- HTML-escaping a
    single quote doesn't prevent a JS string breakout inside an inline
    event handler, since the browser HTML-decodes the attribute before
    handing it to the JS parser)."""

    def test_calendar_grid_chip_escapes_title(self):
        src = APP_JS.read_text()
        assert "escapeHTML(it.title.replace(/^W\\d+ \\w+ /,''))" in src
        assert "title=\"'+escapeHTML(it.title)+' — tap for details\">" in src

    def test_week_report_escapes_title(self):
        src = APP_JS.read_text()
        # renderWeek's per-item title line
        assert "'<b style=\"flex:1;min-width:120px\">'+escapeHTML(it.title.replace(/^W\\d+ \\w+ /,''))+'</b>'" in src

    def test_vacation_preview_escapes_title_and_reason(self):
        src = APP_JS.read_text()
        assert "const name=escapeHTML(a.it.title.replace(/^W\\d+ /,''));" in src
        assert "escapeHTML(a.why)" in src

    def test_onclick_title_args_strip_quotes(self):
        """The three openRun(...) onclick call sites that pass a title
        through a single-quoted JS string argument -- not an
        escapeHTML() context, see class docstring."""
        src = APP_JS.read_text()
        assert src.count("title.replace(/'/g,'')") >= 3
