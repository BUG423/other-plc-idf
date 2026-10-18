"""Real local subprocess streams; no Windows, network or device access."""
import json
import sys

from plc_idf.interop import RetryPolicy, run_command


def test_windows_ansi_error_keeps_exit_and_utf8_stdout():
    message = "参数验证失败：未发送输入"
    script = ("import sys; sys.stdout.buffer.write('可读结果\\r\\n'.encode('utf-8')); "
              f"sys.stderr.buffer.write({message!r}.encode('gb18030')); sys.exit(7)")
    result = run_command([sys.executable, '-c', script],
                         policy=RetryPolicy(5, retries=0), retryable=False)
    assert result.returncode == 7 and not result.ok
    assert result.stdout == '可读结果\n' and result.stderr == message
    assert json.loads(json.dumps({'error': result.stderr}))['error'] == message


def test_undecodable_error_after_side_effect_is_not_replayed(tmp_path):
    marker = tmp_path / 'performed'
    script = ("from pathlib import Path; import sys; "
              f"p=Path({str(marker)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x'); "
              "sys.stderr.buffer.write(bytes([255,129,255])); sys.exit(1)")
    result = run_command([sys.executable, '-c', script],
                         policy=RetryPolicy(5, retries=3), retryable=False)
    assert result.returncode == 1 and result.attempts == 1
    assert result.stderr and marker.read_text() == 'x'
