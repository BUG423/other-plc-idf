"""Versioned target capabilities, separate from device instances and transports.

Static capabilities describe code paths, never the state of a connected PLC.
The existing USB-only v1 deployment records keep their strict validation.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TargetProfile:
    id: str
    name: str
    dialect: str
    compiler_backend: str
    runtime_backend: str
    host_platform: str
    transport_kinds: tuple[str, ...]
    operations: tuple[str, ...]
    limitations: tuple[str, ...]

    def require(self, operation: str) -> None:
        if operation not in self.operations:
            raise ValueError(f"target {self.id!r} does not support {operation!r}")

    def as_dict(self) -> dict:
        return {
            "schema": "plcidf.target-profile.v2", "id": self.id, "name": self.name,
            "compile_target": {"dialect": self.dialect,
                               "compiler_backend": self.compiler_backend},
            "runtime_backend": self.runtime_backend, "host_platform": self.host_platform,
            "transport_kinds": list(self.transport_kinds),
            "operations": list(self.operations), "limitations": list(self.limitations),
            "capability_evidence": "implementation-description; verify each operation",
            "device_instance": None, "hardware_checked": False,
        }


_PROFILES = {
    "easy521": TargetProfile(
        "easy521", "Inovance Easy521 / existing vendor runtime", "AutoShop LiteST",
        "autoshop-working-project", "inovance-vendor", "Windows AutoShop + Linux CLI",
        ("vendor-usb", "modbus-tcp", "modbus-serial"),
        ("new", "check", "build", "flash", "monitor"),
        ("Build requires an existing compatible AutoShop working project",
         "Application download and real PLC runtime results remain unverified",
         "USB BUSID and VID/PID checks are retained in v1 deployment paths")),
    "open-linux": TargetProfile(
        "open-linux", "Local Linux / MatIEC scan runtime", "MatIEC IEC ST subset",
        "matiec-gcc", "plcidf-local-scan", "Linux", ("local-filesystem",),
        ("new", "check", "build", "flash", "run", "stop", "monitor", "verify"),
        ("Local runtime only; no physical I/O provider or remote device deployment",
         "Functional scan execution does not validate hard real-time deadlines",
         "Application packages are not compatible with Easy521 vendor firmware")),
}


def get_profile(name: str) -> TargetProfile:
    try:
        return _PROFILES[name]
    except (KeyError, TypeError):
        raise ValueError(f"unknown target profile: {name!r}") from None


def list_profiles() -> list[dict]:
    return [profile.as_dict() for profile in _PROFILES.values()]


@dataclass(frozen=True)
class BuildRequestV2:
    """Target-neutral build inputs; no attached device or Windows path required."""

    source: Path
    build_dir: Path
    profile_id: str

    def validate(self) -> TargetProfile:
        profile = get_profile(self.profile_id)
        profile.require("build")
        if not self.source.is_file():
            raise FileNotFoundError(self.source)
        if self.source.is_relative_to(self.build_dir):
            raise ValueError("build output directory must not contain its source input")
        return profile


def local_runtime_locator(state_dir: str | Path) -> dict:
    """Declare a transport location, without fabricating observed device identity."""
    return {
        "schema": "plcidf.device-locator.v2", "profile_id": "open-linux",
        "transport": {"kind": "local-filesystem",
                      "state_dir": str(Path(state_dir).expanduser().resolve())},
        "instance_id": None, "identity_verification": "not-observed",
    }
