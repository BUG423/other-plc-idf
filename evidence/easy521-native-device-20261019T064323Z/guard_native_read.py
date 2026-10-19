"""Run one existing native reader with Python process/network fallbacks denied.

This experiment wrapper is executed as the ordinary WSL user after explicit,
temporary host preparation. It does not prepare USB, detach a driver or write
PLC state. Input: runtime/memory, fixed profile and current Linux BUS:ADDRESS.
Output: the reader's single JSON result. A Python audit hook rejects process
creation and Python socket access during import/execution; native libusb I/O
remains allowed. This is evidence of this invocation, not OS attestation.
"""
import pathlib
import runpy
import sys


def deny_fallback(event, arguments):
    """Reject subprocess/exec/system/socket audit events; no external effects."""
    if event.startswith(("subprocess.", "socket.")) or event in {
        "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork",
        "os.forkpty",
    }:
        raise RuntimeError("Native read fallback denied: " + event)


kind, profile, device = sys.argv[1:]
if kind not in {"runtime", "memory"}:
    raise SystemExit("Unsupported reader kind")
root = pathlib.Path(__file__).resolve().parents[2]
reader = root / "tools" / ("read_easy521_usb_" + kind + ".py")
sys.argv = [str(reader), "--transport", "linux-native", "--profile", profile,
            "--linux-device", device, "--timeout-ms", "2500", "--execute"]
sys.addaudithook(deny_fallback)
runpy.run_path(str(reader), run_name="__main__")
