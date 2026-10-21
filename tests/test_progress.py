import io

from crgraph.progress import CLEAR, Progress


class Terminal(io.StringIO):
    def isatty(self):
        return True


def test_progress_redraws_one_line_and_clears_it():
    out = Terminal()
    with Progress(out, tick=60) as progress:
        progress.stage("parsing changed files", 3)
        progress.advance()
        progress.advance()
        assert "parsing changed files 2/3" in progress.line()
        progress.stage("analysing")
        assert "analysing ·" in progress.line()
    text = out.getvalue()
    assert "\n" not in text and text.endswith(CLEAR)
    assert "parsing changed files 0/3" in text and "crgraph: analysing" in text


def test_no_output_off_a_terminal_or_when_disabled(monkeypatch):
    for progress in (Progress(io.StringIO()), Progress(Terminal(), enabled=False)):
        progress.stage("parsing", 1)
        progress.advance()
        progress.close()
        assert progress.stream.getvalue() == ""
    monkeypatch.setenv("TERM", "dumb")
    assert not Progress(Terminal()).enabled


def test_labels_cannot_write_escape_sequences():
    progress = Progress(io.StringIO())
    progress.stage("AI review (m\x1b]0;owned\x07\r\x1b[2Jx)")
    assert "\x1b" not in progress.line() and "AI review (m]0;owned[2Jx)" in progress.line()
