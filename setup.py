"""Package existing runtime assets without maintaining a second source copy.

The register contract stays in wsl-tools/e521_regs.py. Both wheels and wheels
built from an sdist obtain these files from the same repository paths.
"""

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


ASSETS = (
    "wsl-tools/e521_monitor.py",
    "wsl-tools/virtual_easy521.py",
    "wsl-tools/e521_regs.py",
    "plc/tools/autoshop_ui.ps1",
    "plc/tools/read_autoshop_online.ps1",
    "plc/tools/compile_vendor_st.ps1",
    "examples/easy521_native_probe/MAIN_plus1_body.st",
    "examples/easy521_native_probe/MAIN_plus2_body.st",
    "examples/easy521_native_probe/README.md",
    "examples/easy521_native_probe/source-review.json",
    "plc/easy521/MAIN_5ms_variables.csv",
    "plc/easy521/autoshop_global_import.csv",
    "plc/easy521/autoshop/BESS_E521_DEV/VarNameAndAddr.csv",
    "examples/open_linux/counter/main.st",
    "examples/open_linux/motor/main.st",
    "examples/open_linux/motor/plcidf.json",
    "examples/open_linux/motor/scenarios.json",
    "examples/open_linux/motor/README.md",
    "examples/open_linux/motor/AGENTS.md",
    "examples/open_linux/analog_hysteresis/main.st",
    "examples/open_linux/analog_hysteresis/plcidf.json",
    "examples/open_linux/analog_hysteresis/scenarios.json",
    "examples/open_linux/analog_hysteresis/README.md",
    "examples/open_linux/analog_hysteresis/AGENTS.md",
    "tools/bootstrap_open_linux.py",
)
ROOT = Path(__file__).resolve().parent


class BuildPyWithAssets(build_py):
    def run(self):
        super().run()
        for relative in ASSETS:
            target = Path(self.build_lib) / "plc_idf" / "_resources" / relative
            self.mkpath(str(target.parent))
            self.copy_file(str(ROOT / relative), str(target))

    def get_outputs(self, include_bytecode=1):
        return super().get_outputs(include_bytecode) + [
            str(Path(self.build_lib) / "plc_idf" / "_resources" / relative)
            for relative in ASSETS
        ]


setup(cmdclass={"build_py": BuildPyWithAssets})
