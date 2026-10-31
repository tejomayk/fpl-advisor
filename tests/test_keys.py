"""Key redaction and the shared .env loader. Every key here is fake."""

from __future__ import annotations

import logging
import os
import sys

import pytest

from fpl_advisor import keys

# Built at runtime so the source file itself never contains a key-shaped string.
FAKE_ODDS = "0f" * 16
FAKE_OR = "sk-or-v1-" + "ab12" * 16
FAKE_YT = "AIza" + "Fake" * 8 + "Key"
FAKE_NTFY = "tk_" + "n0t4r34lt0k3n" * 2


@pytest.fixture
def fake_keys(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", FAKE_ODDS)
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OR)
    monkeypatch.setenv("YOUTUBE_API_KEY", FAKE_YT)
    monkeypatch.setenv("NTFY_TOKEN", FAKE_NTFY)
    keys.install_redaction()
    return (FAKE_ODDS, FAKE_OR, FAKE_YT, FAKE_NTFY)


def test_loaded_values_are_masked(fake_keys):
    text = " | ".join(fake_keys)
    out = keys.redact(text)
    for k in fake_keys:
        assert k not in out
    assert out.count("***") >= 4


def test_url_encoded_value_is_masked(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "abc/def+ghi=jkl")
    assert "abc" not in keys.redact("x=abc%2Fdef%2Bghi%3Djkl")


@pytest.mark.parametrize(
    "text, gone",
    [
        (
            "https://api.example.com/v4/odds?regions=uk&apiKey=SECRETVALUE1",
            "SECRETVALUE1",
        ),
        (
            "https://www.googleapis.com/youtube/v3/x?part=a&key=SECRETVALUE2",
            "SECRETVALUE2",
        ),
        ("{'Authorization': 'Bearer SECRETVALUE3'}", "SECRETVALUE3"),
        ("{'X-Goog-Api-Key': 'SECRETVALUE4'}", "SECRETVALUE4"),
        ("token sk-or-v1-" + "9" * 40 + " end", "9" * 40),
        ("AIza" + "Q" * 35, "Q" * 35),
    ],
)
def test_key_shapes_are_masked_even_when_not_loaded(text, gone):
    out = keys.redact(text)
    assert gone not in out and "***" in out


def test_ordinary_text_is_untouched():
    s = "odds: /sports/soccer_epl/odds returned HTTP 401 (key rejected)"
    assert keys.redact(s) == s


def test_log_message_args_and_traceback_are_redacted(fake_keys, caplog):
    log = logging.getLogger("fpl_advisor.test")
    with caplog.at_level(logging.INFO):
        log.info("GET %s", f"https://h/x?apiKey={FAKE_ODDS}")
        log.warning("headers %r", {"Authorization": f"Bearer {FAKE_OR}"})
        try:
            raise RuntimeError(f"boom https://h/y?key={FAKE_YT}")
        except RuntimeError:
            log.exception("failed")
        log.error("stack", stack_info=True)
    text = caplog.text + "".join(r.getMessage() for r in caplog.records)
    for k in fake_keys:
        assert k not in text
    assert "RuntimeError" in caplog.text  # the error type is kept
    assert caplog.records[0].args == ("https://h/x?apiKey=***",)  # shape kept


def test_records_from_child_loggers_and_new_handlers_are_redacted(fake_keys):
    seen: list[str] = []

    class H(logging.Handler):
        def emit(self, record):
            seen.append(self.format(record))

    h = H()
    lg = logging.getLogger("some.library.deep")
    lg.addHandler(h)
    try:
        lg.warning("url=%s", f"https://x/?apiKey={FAKE_ODDS}")
    finally:
        lg.removeHandler(h)
    assert seen and FAKE_ODDS not in seen[0]


def test_uncaught_exceptions_are_redacted(fake_keys, capsys):
    try:
        raise ValueError(f"leak {FAKE_OR}")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    err = capsys.readouterr().err
    assert "ValueError" in err and FAKE_OR not in err


def test_dotenv_never_overrides_and_reads_new_keys(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text(f"ODDS_API_KEY={FAKE_ODDS}\nOPENROUTER_API_KEY=from-file\nOTHER=x\n")
    f.chmod(0o600)
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    monkeypatch.delenv("OTHER", raising=False)
    keys.load_dotenv(f)
    assert os.environ["ODDS_API_KEY"] == FAKE_ODDS
    assert os.environ["OPENROUTER_API_KEY"] == "from-env"
    assert "OTHER" not in os.environ


def test_dotenv_readable_by_others_warns_without_values(tmp_path, caplog):
    f = tmp_path / ".env"
    f.write_text(f"YOUTUBE_API_KEY={FAKE_YT}\n")
    f.chmod(0o644)
    keys._warned_perms.discard(str(f))
    with caplog.at_level(logging.WARNING, logger="fpl_advisor.keys"):
        keys.load_dotenv(f)
    assert "chmod 600" in caplog.text
    assert FAKE_YT not in caplog.text
    f.chmod(0o600)
    assert keys.check_permissions(f) is True


def test_arg_shapes_are_kept_and_numbers_untouched(fake_keys):
    rec = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "%s %d %r", (FAKE_OR, 200, None), None
    )
    keys.scrub_record(rec)
    assert rec.args == ("***", 200, None)
    assert rec.getMessage() == "*** 200 None"
    rec = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "%(a)s %(n)d", ({"a": FAKE_YT, "n": 5},), None
    )
    keys.scrub_record(rec)
    assert rec.args == {"a": "***", "n": 5}

    class Holder:
        def __repr__(self):
            return f"Holder(key={FAKE_ODDS})"

    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "%r", (Holder(),), None)
    keys.scrub_record(rec)
    assert FAKE_ODDS not in rec.getMessage()


def test_uvicorn_access_log_still_formats_and_is_redacted(fake_keys):
    from uvicorn.logging import AccessFormatter

    lines: list[str] = []

    class H(logging.Handler):
        def emit(self, record):
            lines.append(self.format(record))

    h = H()
    h.setFormatter(
        AccessFormatter(
            '%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False
        )
    )
    lg = logging.getLogger("uvicorn.access")
    lg.addHandler(h)
    old = lg.level
    lg.setLevel(logging.INFO)
    try:
        lg.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:5000",
            "GET",
            f"/api/advice?team=1&apiKey={FAKE_ODDS}",
            "1.1",
            200,
        )
    finally:
        lg.removeHandler(h)
        lg.setLevel(old)
    assert lines and "GET /api/advice?team=1&apiKey=*** HTTP/1.1" in lines[0]
    assert "200" in lines[0] and FAKE_ODDS not in lines[0]
