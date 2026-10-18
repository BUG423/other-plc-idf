"""OCR protocol/path handling is tested without invoking Windows or a GUI."""

import base64
import json
import re
import subprocess
from types import SimpleNamespace

import pytest

from plc_idf.windows_ocr import read_image_text


def payload(**changes):
    value = {"schema_version": 1, "status": "ok", "text": "错误 MAIN 行号26",
             "lines": ["错误 MAIN 行号26", "统计：(1)错误"], "language": "zh-Hans-CN",
             "available_languages": ["zh-Hans-CN", "en-US"], "error": None, "stage": "recognize"}
    value.update(changes)
    return value


def runner_with(value, *, stderr="", returncode=0):
    def run(argv, timeout, cwd=None):
        return SimpleNamespace(returncode=returncode, stdout=json.dumps(value, ensure_ascii=False), stderr=stderr)
    return run


def test_returns_lines_language_and_raw_output():
    value = payload()
    result = read_image_text(r"C:\Users\me\log.png", runner=runner_with(value))
    assert result.ok
    assert result.lines == value["lines"]
    assert result.language == "zh-Hans-CN"
    assert json.loads(result.stdout) == value
    assert result.as_dict()["engine"] == "Windows.Media.Ocr"
    assert result.as_dict()["ok"] is True


def test_path_quotes_unicode_and_powershell_syntax_are_encoded_as_data():
    path = "C:\\测试\\o'brien $(Write-Output bad);`x.png"
    captured = []
    def runner(argv, timeout, cwd=None):
        captured.append(argv)
        assert timeout == 17
        assert cwd is None
        script = base64.b64decode(argv[-1]).decode("utf-16-le")
        assert path not in script
        request = re.search(r"FromBase64String\('([^']+)'\)", script)[1]
        data = json.loads(base64.b64decode(request).decode("utf-8"))
        assert data["path"] == path
        assert data["languages"] == ["zh-Hans", "zh-CN", "en-US"]
        assert data["scale"] == 3
        assert data["region"] == [0, 2, 100, 40]
        assert "SendKeys" not in script
        assert "mouse_event" not in script
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload()), stderr="")
    result = read_image_text(path, powershell_exe="custom-powershell.exe", runner=runner,
                             timeout_s=17, scale=3, region=(0, 2, 100, 40))
    assert result.ok
    assert len(captured) == 1
    assert captured[0][0] == "custom-powershell.exe"
    assert "-EncodedCommand" in captured[0]


def test_accepts_unc_png_and_empty_recognition():
    result = read_image_text(r"\\wsl.localhost\Ubuntu\home\me\LOG.PNG",
                             runner=runner_with(payload(text="", lines=[])))
    assert result.ok
    assert result.text == ""
    assert result.lines == []


@pytest.mark.parametrize("path", ["", "relative.png", r"C:log.png", "/tmp/log.png", r"C:\log.jpg", "C:\\bad\x00.png"])
def test_invalid_path_fails_before_runner(path):
    def must_not_run(*args, **kwargs):
        pytest.fail("invalid path reached process runner")
    with pytest.raises(ValueError):
        read_image_text(path, runner=must_not_run)


@pytest.mark.parametrize("kwargs", [{"languages": "en-US"}, {"languages": []}, {"languages": [None]},
                                    {"timeout_s": 0}, {"timeout_s": True}, {"timeout_s": 121},
                                    {"timeout_s": float("nan")}, {"scale": float("inf")},
                                    {"scale": 0.5}, {"scale": True}, {"region": (0, 0, 1)},
                                    {"region": (-1, 0, 1, 1)}, {"region": (0, 0, 0, 1)},
                                    {"region": (False, 0, 1, 1)}])
def test_invalid_options(kwargs):
    with pytest.raises(ValueError):
        read_image_text(r"C:\log.png", **kwargs)


def test_missing_executable_is_explicitly_unsupported_and_not_retried():
    calls = []
    def runner(*args, **kwargs):
        calls.append(1)
        raise FileNotFoundError("no Windows here")
    result = read_image_text(r"C:\log.png", runner=runner)
    assert result.status == "unsupported"
    assert "no Windows here" in result.error
    assert len(calls) == 1


def test_winrt_missing_language_and_file_errors_remain_distinct():
    unsupported = read_image_text(r"C:\log.png", runner=runner_with(payload(
        status="unsupported", text="", lines=[], language=None,
        error="No requested Windows OCR language is installed", stage="language-selection")))
    assert unsupported.status == "unsupported"
    assert unsupported.stage == "language-selection"
    missing_file = read_image_text(r"C:\log.png", runner=runner_with(payload(
        status="error", text="", lines=[], language=None,
        error="The system cannot find the file specified", stage="read-image")))
    assert missing_file.status == "error"
    assert missing_file.stage == "read-image"


def test_process_error_preserves_raw_output_and_does_not_retry():
    calls = []
    def runner(argv, timeout, cwd=None):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stdout="raw stdout", stderr="raw stderr")
    result = read_image_text(r"C:\log.png", runner=runner)
    assert result.status == "error"
    assert result.returncode == 1
    assert result.stdout == "raw stdout"
    assert result.stderr == "raw stderr"
    assert len(calls) == 1


def test_timeout_is_bounded_and_raw_output_is_retained():
    def runner(argv, timeout, cwd=None):
        raise subprocess.TimeoutExpired(argv, timeout, output=b"partial", stderr=b"timeout detail")
    result = read_image_text(r"C:\log.png", runner=runner)
    assert result.status == "error"
    assert result.timed_out
    assert result.returncode == 124
    assert result.stdout == "partial"
    assert result.stderr == "timeout detail"


@pytest.mark.parametrize("changes", [{"schema_version": True}, {"schema_version": 2},
                                     {"status": "success"}, {"status": []}, {"status": {}},
                                     {"text": []}, {"lines": [1]},
                                     {"lines": "one line"}, {"language": []}, {"language": None},
                                     {"error": {}}, {"error": "bad"},
                                     {"status": "error", "error": None},
                                     {"available_languages": [None]}])
def test_malformed_protocol_is_error_not_success(changes):
    result = read_image_text(r"C:\log.png", runner=runner_with(payload(**changes)))
    assert result.status == "error"
    assert "invalid Windows OCR response" in result.error


def test_non_json_response_is_preserved_and_rejected():
    def runner(argv, timeout, cwd=None):
        return SimpleNamespace(returncode=0, stdout="PowerShell banner, no JSON", stderr="")
    result = read_image_text(r"C:\log.png", runner=runner)
    assert not result.ok
    assert result.stdout == "PowerShell banner, no JSON"
