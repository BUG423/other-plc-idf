"""config.py — .plcidf.toml 配置读取（项目根自动发现 + 全默认值）。

查找顺序：
    1. 命令行 --config 指定的文件
    2. 环境变量 PLCIDF_CONFIG 指定的文件（旧名 PY2PLC_CONFIG 仍被识别）
    3. 从 cwd 逐级向上找 .plcidf.toml
    4. 源码工作区根下的 .plcidf.toml（editable 安装）；独立安装以 cwd 为根

任一环节没找到就用纯默认值——工具在任何目录下都能跑，配置只是覆盖项。
Python 3.12 标准库 tomllib 解析，零第三方依赖。
"""

from __future__ import annotations

import os
import tomllib
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType

#: 项目根标记文件（配置/脚本路径自动发现的锚点）
ROOT_MARKER = Path("wsl-tools") / "e521_monitor.py"
CONFIG_NAME = ".plcidf.toml"

# =====================【默认值（单一事实来源）】=====================

DEFAULTS: dict = {
    "paths": {
        # 项目根与 wsl-tools 目录；None = 自动发现（见 find_project_root）
        "project_root": None,
        "wsl_tools": None,
    },
    "device": {
        "busid": "6-4",                       # Easy521（H5U）所在 USB 总线 ID
        "vidpid": "0471:a531",                # H5U USB Device（usbipd list 实测）
        "tty_glob": "/dev/ttyACM*",           # attach 后 WSL 侧 CDC 串口
        "usbipd_exe": "/mnt/c/Program Files/usbipd-win/usbipd.exe",
        "attach_settle_s": 3.0,               # attach 后等设备枚举的时长上限
    },
    "monitor": {
        "transport": "tcp",
        "host": "127.0.0.1",
        "port": 1502,
        "device": "/dev/ttyUSB0",             # serial 传输的串口路径
        "baud": 9600,
        "unit_id": 1,
        "interval": 1.0,
        "timeout": 3.0,
        "calibrate": False,
        "word_order": None,                   # None=自动/默认 ABCD
    },
    "sim": {
        "host": "127.0.0.1",
        "port": 1502,
        "scenario": "steady",
        "word_order": "abcd",
        "tick": 0.1,
        "hold_dip": 10.0,
        "ready_timeout": 15.0,                # --with-monitor 等从站 READY 的上限
        "monitor_exec_timeout": 60.0,         # --with-monitor 里监测子进程硬超时
    },
    "bridge": {
        # 安全默认：false。真执行需此处 true 且命令行 --i-have-authorized 双因子。
        "enabled": False,
        "project": "BESS_E521_DEV",
        "windows_project": r"E:\PLCProjects\BESS_E521_DEV",
        # AutoShop GUI 驱动脚本（plc/tools/autoshop_ui.ps1）的 Windows 可见路径；
        # None = 自动用 \\wsl.localhost\<发行版>\... UNC 路径
        "tools_ps1_windows": None,
        "powershell_exe": "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe",
        "accept_compile_text": "编译",         # 编译成功对话框文案（P2 现场校准点）
        "accept_download_text": "成功",        # 下载成功对话框文案（P2 现场校准点）
        # AutoShop 本体（冷启动步用；管线发现未运行时自动拉起）
        "autoshop_exe": r"E:\Software\AutoShop\AutoShop.exe",
        # 编译成功是否弹模态对话框：4.11 及以前弹，4.12 起只写信息输出窗格。
        # false 时 build 链跳过 accept 步，以日志面板+产物校验判定成功（flash 不受影响）
        "compile_success_dialog": True,
    },
    "net": {
        "timeout_s": 10.0,                    # 单次 interop/网络操作超时
        "retries": 2,                         # 重试上限（总尝试 = retries + 1）
        "retry_backoff_s": 1.0,
    },
    "log": {
        # 证据日志（JSONL）：相对路径基于项目根
        "evidence": "logs/evidence.jsonl",
    },
}


class Section:
    """配置节：属性访问 + .get()，只读视图（写回请改 toml 文件）。"""

    def __init__(self, data: dict):
        self._data = data

    def __getattr__(self, key):
        try:
            return self._data[key]
        except KeyError as exc:
            raise AttributeError(f"配置节缺少键: {key}") from exc

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __getitem__(self, key):
        return self._data[key]

    def __contains__(self, key):
        return key in self._data

    def __repr__(self):
        return f"Section({self._data!r})"

    def to_dict(self) -> dict:
        return self._data


class Config:
    """合并默认值后的配置对象：cfg.bridge.enabled / cfg.get("net", "timeout_s")。"""

    def __init__(self, merged: dict, source: Path | None):
        self._merged = merged
        self.source = source
        for name, section in merged.items():
            setattr(self, name, Section(section))

    def get(self, section: str, key: str, default=None):
        return self._merged.get(section, {}).get(key, default)

    def __repr__(self):
        return f"Config(source={self.source})"


# =====================【路径发现】=====================

def _env(name: str) -> str | None:
    """读环境变量：PLCIDF_* 新名优先，PY2PLC_* 旧名作兼容别名兜底。"""
    for key in (name, "PY2PLC_" + name[len("PLCIDF_"):]):
        value = os.environ.get(key)
        if value:
            return value
    return None


def find_project_root(explicit: str | os.PathLike | None = None) -> Path:
    """定位用户工作区，独立安装不得把 site-packages 当作可写项目目录。"""
    if explicit:
        return Path(explicit).resolve()
    env = _env("PLCIDF_ROOT")
    if env:
        return Path(env).resolve()
    cwd = Path.cwd()
    for base in (cwd, *cwd.parents):
        if (base / CONFIG_NAME).is_file() or (base / ROOT_MARKER).is_file():
            return base
    # editable 安装可以继续发现源码工作区；wheel 安装回落到调用目录。
    source_root = Path(__file__).resolve().parents[2]
    return source_root if (source_root / ROOT_MARKER).is_file() else cwd


def runtime_asset(project_root: Path, relative: str) -> Path:
    """优先使用工作区原始资源；普通安装使用 wheel 随包携带的资源。"""
    candidate = project_root / relative
    if candidate.exists():
        return candidate
    source_candidate = Path(__file__).resolve().parents[2] / relative
    if source_candidate.exists():
        return source_candidate
    # Wheel 安装时 setuptools/pip 将资源解包到包目录，无需写入用户缓存。
    return Path(__file__).resolve().parent / "_resources" / relative


def find_config_file(project_root: Path) -> Path | None:
    """配置文件查找：PLCIDF_CONFIG（旧名 PY2PLC_CONFIG 亦可）> 项目根/.plcidf.toml。"""
    env = _env("PLCIDF_CONFIG")
    if env:
        p = Path(env)
        return p if p.exists() else None
    p = project_root / CONFIG_NAME
    return p if p.exists() else None


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并：override 覆盖 base，嵌套 dict 深合并，其余整体替换。"""
    # 自动资源定位会写入 paths；不能污染 DEFAULTS 或下一次 load_config。
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _auto_tools_ps1_windows(project_root: Path) -> str:
    """autoshop_ui.ps1 的 Windows 侧 UNC 路径（\\\\wsl.localhost\\<发行版>\\...）。"""
    from .interop import to_windows_unc
    return to_windows_unc(runtime_asset(project_root, "plc/tools/autoshop_ui.ps1"))


def load_config(config_path: str | os.PathLike | None = None,
                project_root: str | os.PathLike | None = None) -> Config:
    """读配置并合并默认值；文件缺失/解析失败都按"未提供"处理（默认值保底）。

    解析失败时打印告警到 stderr，避免一个坏 toml 让整条 CLI 瘫痪。
    """
    import sys

    root = find_project_root(project_root)
    path = Path(config_path) if config_path else find_config_file(root)
    user_data: dict = {}
    if path is not None:
        try:
            with open(path, "rb") as fh:
                user_data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            print(f"[plcidf] 配置 {path} 读取失败（按默认值继续）: {exc}",
                  file=sys.stderr)
            path = None

    merged = _deep_merge(DEFAULTS, user_data)
    merged["paths"]["project_root"] = str(root)
    tools_override = merged["paths"].get("wsl_tools")
    if tools_override:
        tools_path = Path(tools_override).expanduser()
        if not tools_path.is_absolute():
            tools_path = root / tools_path
    else:
        tools_path = runtime_asset(root, "wsl-tools/e521_monitor.py").parent
    merged["paths"]["wsl_tools"] = str(tools_path.resolve())
    if not merged["bridge"]["tools_ps1_windows"]:
        merged["bridge"]["tools_ps1_windows"] = _auto_tools_ps1_windows(root)
    return Config(MappingProxyType(merged), path)


def evidence_path(cfg: Config) -> Path:
    """证据日志的绝对路径（相对路径基于项目根）。"""
    raw = Path(cfg.log.get("evidence", "logs/evidence.jsonl"))
    return raw if raw.is_absolute() else Path(cfg.paths["project_root"]) / raw
