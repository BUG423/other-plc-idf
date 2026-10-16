"""backends/base.py — 构建/烧录/监测后端的接口定义（ABC）。

约定：
    - 每个真机动作先 plan_*() 产出 PlanStep 列表（可独立 dry-run 打印），
      再 execute_*() 真执行；dry-run 就是"只渲染 plan，绝不执行"。
    - PlanStep.command 是真实可执行的 argv（interop 调用链原样可拷贝），
      expected_artifacts 是该步/整链完成后应存在的产物路径。
    - execute 返回 BackendResult，调用方负责写证据日志。
"""

from __future__ import annotations

import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass
class PlanStep:
    """调用链上的一步。kind: verify=只读校验 / interop=跨入 Windows / local=本机动作。"""

    index: int
    title: str
    kind: str
    command: list[str]
    description: str = ""
    expected_artifacts: tuple[str, ...] = ()

    def command_line(self) -> str:
        """可直接拷贝执行的命令行（Windows 路径参数用引号包裹）。"""
        return " ".join(
            f'"{a}"' if ("\\" in a or " " in a) else shlex.quote(a)
            for a in self.command)


@dataclass
class BackendResult:
    """一次执行的汇总。"""

    ok: bool
    message: str
    outputs: dict[str, Any] = field(default_factory=dict)
    steps: list[PlanStep] = field(default_factory=list)


@dataclass
class OperationRequest:
    """build/flash 通用请求。"""

    project: str                          # 工程名，如 BESS_E521_DEV
    windows_project: str                  # Windows 侧工程目录（如 E:\PLCProjects\...）
    dry_run: bool = True
    authorized: bool = False              # 命令行 --i-have-authorized
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def hcp_path(self) -> str:
        """AutoShop 工程文件（.hcp）的 Windows 全路径。"""
        base = self.windows_project.rstrip("\\")
        return f"{base}\\{self.project}.hcp"


class BuildBackend(ABC):
    """编译后端接口。"""

    name: ClassVar[str] = "build"

    @abstractmethod
    def plan_build(self, request: OperationRequest) -> list[PlanStep]:
        """产出编译调用链（不执行）。"""

    @abstractmethod
    def execute_build(self, request: OperationRequest) -> BackendResult:
        """真执行编译调用链（调用方已过双因子门禁）。"""


class FlashBackend(ABC):
    """烧录/下载后端接口。"""

    name: ClassVar[str] = "flash"

    @abstractmethod
    def plan_flash(self, request: OperationRequest) -> list[PlanStep]:
        """产出下载调用链（不执行）。"""

    @abstractmethod
    def execute_flash(self, request: OperationRequest) -> BackendResult:
        """真执行下载调用链（调用方已过双因子门禁）。"""


class MonitorBackend(ABC):
    """监测后端接口：组装只读监测命令并运行。"""

    name: ClassVar[str] = "monitor"

    @abstractmethod
    def build_command(self, opts: dict) -> list[str]:
        """把选项 dict 组装成 subprocess argv。"""

    @abstractmethod
    def run(self, opts: dict, timeout: float | None = None) -> int:
        """运行监测（透传 stdout/stderr），返回进程退出码。"""


def render_plan(title: str, steps: list[PlanStep], request: OperationRequest,
                decision, backend_name: str, extra_lines: list[str] | None = None) -> str:
    """渲染 dry-run 输出：头部 + 逐步调用链 + 预期产物 + 解锁提示。"""
    lines = [
        f"=== plcidf {title} — {backend_name} dry-run（不执行任何真机操作）",
        f"工程: {request.project}  工程文件: {request.hcp_path}",
        f"授权状态: {'已解锁（但 dry-run 不执行）' if decision.granted else '未解锁（dry-run 无需授权）'}",
        f"预计 interop 调用链（{len(steps)} 步）:",
    ]
    for step in steps:
        lines.append(f"  [{step.index}] ({step.kind}) {step.title}")
        if step.command:
            lines.append(f"        $ {step.command_line()}")
        if step.description:
            lines.append(f"        # {step.description}")
        for art in step.expected_artifacts:
            lines.append(f"        -> 预期产物: {art}")
    if extra_lines:
        lines.extend(extra_lines)
    unlocked_hint = (
        "解锁真执行: .plcidf.toml 设 [bridge] enabled=true，"
        "且命令行同时加 --execute --i-have-authorized（双因子，缺一即拒）")
    lines.append(unlocked_hint)
    return "\n".join(lines)
