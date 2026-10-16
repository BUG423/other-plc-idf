"""backends/bridge_autoshop.py — 桥式 AutoShop 后端（build/flash 完整管线）。

桥式思路：WSL 侧不做任何 AutoShop 协议逆向，而是驱动 Windows 侧已装好的
AutoShop GUI 完成"打开工程 -> 编译 -> 下载"，工具是本仓库已验证过的
plc/tools/autoshop_ui.ps1（截图/控件树/按键/对话框确认，见该脚本头注）。
每次 GUI 动作都是一条 powershell.exe interop 调用。

dry-run（默认）：
    只打印完整 interop 调用链（每步真实 argv、Windows 工程路径、预期产物），
    绝不执行任何命令。

真执行（双因子门禁锁住，见 safety.check_bridge_authorization）：
    配置 bridge.enabled=true 且命令行 --i-have-authorized，二者缺一即拒。
    执行路径已写好：逐步 run_command（超时+重试）+ 逐步证据日志 + 失败即停。
    菜单 ID / 对话框文案属于现场校准点（P2），脚本里显式标注。
"""

from __future__ import annotations

from .. import device as device_mod
from ..interop import InteropResult, RetryPolicy, run_command
from ..safety import (AuthorizationDecision, EvidenceLogger, SafetyError,
                      check_bridge_authorization)
from .base import (BackendResult, BuildBackend, FlashBackend, OperationRequest,
                   PlanStep, render_plan)

#: 真执行时单步 GUI 动作的超时（AutoShop 弹窗/编译等待比普通 interop 长）
GUI_STEP_TIMEOUT_S = 120.0


class BridgeAutoshopBackend(BuildBackend, FlashBackend):
    """桥式后端：一个后端同时实现 Build 与 Flash 两条管线。"""

    name = "bridge_autoshop"

    def __init__(self, cfg, evidence: EvidenceLogger | None = None):
        self.cfg = cfg
        self.evidence = evidence

    # ---------- 公共构件 ----------

    def _request(self, request: OperationRequest) -> OperationRequest:
        """补全请求里可从配置推导的字段（工程名/目录）。"""
        if not request.project:
            request.project = self.cfg.bridge["project"]
        if not request.windows_project:
            request.windows_project = self.cfg.bridge["windows_project"]
        return request

    def _powershell_argv(self, action: str, value: str = "") -> list[str]:
        """构造一条 autoshop_ui.ps1 的 interop 调用（真实 argv）。"""
        argv = [
            self.cfg.bridge["powershell_exe"],
            "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", self.cfg.bridge["tools_ps1_windows"],
            "-Action", action,
        ]
        if value:
            argv += ["-Value", value]
        return argv

    def _usbipd_list_argv(self) -> list[str]:
        return [self.cfg.device["usbipd_exe"], "list"]

    # ---------- 调用链规划 ----------

    def _plan_open_project(self, request: OperationRequest, start: int) -> list[PlanStep]:
        """打开工程的公共三步（键击唤出对话框 -> 填路径 -> 校验加载）。"""
        hcp = request.hcp_path
        return [
            PlanStep(start, "唤出 AutoShop 打开文件对话框", "interop",
                     self._powershell_argv("keys", "^o"),
                     "keys 动作：激活主窗口并发送 Ctrl+O"),
            PlanStep(start + 1, "在文件对话框填入工程路径并确认", "interop",
                     self._powershell_argv("file", hcp),
                     "file 动作：向 #32770 对话框 Edit 填路径并点击 打开"),
            PlanStep(start + 2, "校验工程已加载（主窗口标题含工程名）", "interop",
                     self._powershell_argv("inspect"),
                     f"inspect 动作：期望标题含 {request.project}"),
        ]

    def _plan_verify_device(self, start: int) -> PlanStep:
        return PlanStep(
            start, "前置校验：PLC USB 在 Windows 侧（usbipd list 只读）", "verify",
            self._usbipd_list_argv(),
            f"期望 BUSID {self.cfg.device['busid']}（{self.cfg.device['vidpid']} H5U）"
            f" 存在且未挂载到 WSL（AutoShop 需要设备在 Windows 侧；若已挂载请先 detach）")

    def _plan_read_logs(self, start: int) -> list[PlanStep]:
        return [
            PlanStep(start, "读取 AutoShop 编译/下载日志面板", "interop",
                     self._powershell_argv("log"),
                     "log 动作：抓取结果 ListBox（必要时回退 screenshot）"),
            PlanStep(start + 1, "读取错误诊断列表", "interop",
                     self._powershell_argv("diagnostics"),
                     "diagnostics 动作：空列表=无错误"),
        ]


    def plan_build(self, request: OperationRequest) -> list[PlanStep]:
        """编译调用链：校验设备 -> 打开工程 -> 编译 -> 确认 -> 日志/产物。"""
        request = self._request(request)
        win_dir = request.windows_project
        return [
            self._plan_verify_device(1),
            *_plan_spacer(2, "打开工程 BESS 项目"),
            *self._plan_open_project(request, 3),
            *_plan_spacer(6, "编译"),
            PlanStep(7, "定位编译菜单入口", "interop",
                     self._powershell_argv("menus"),
                     "menus 动作：导出菜单树定位 编译/Compile（P2 现场校准点）"),
            PlanStep(8, "触发编译", "interop",
                     self._powershell_argv("keys", "{F7}"),
                     "keys 动作：编译快捷键（P2 现场校准点，按菜单树结果修正）"),
            PlanStep(9, "等待并确认编译结果对话框", "interop",
                     self._powershell_argv(
                         "accept", self.cfg.bridge["accept_compile_text"]),
                     f"accept 动作：等待含“{self.cfg.bridge['accept_compile_text']}”"
                     "的成功弹窗并点确定（P2 现场校准点）"),
            *self._plan_read_logs(10),
            PlanStep(12, "校验编译产物", "verify", [],
                     "预期产物：编译输出目录与工程文件更新时间",
                     expected_artifacts=(
                         win_dir + r"\Compile",
                         hcp_mtime_artifact(request),
                     )),
        ]

    def plan_flash(self, request: OperationRequest) -> list[PlanStep]:
        """下载调用链：校验设备 -> 打开工程 -> PLC 下载 -> 确认 -> 日志复核。"""
        request = self._request(request)
        return [
            self._plan_verify_device(1),
            *_plan_spacer(2, "打开工程 BESS 项目"),
            *self._plan_open_project(request, 3),
            *_plan_spacer(6, "下载到 PLC（真机写操作，仅在本解锁路径出现）"),
            PlanStep(7, "定位 PLC 下载菜单入口", "interop",
                     self._powershell_argv("menus"),
                     "menus 动作：定位 PLC->写入/下载 入口（P2 现场校准点）"),
            PlanStep(8, "触发 PLC 下载", "interop",
                     self._powershell_argv("keys", "%p+d"),
                     "keys 动作：Alt+P,D 菜单序列（P2 现场校准点）"),
            PlanStep(9, "确认下载警示对话框", "interop",
                     self._powershell_argv("accept", "确定"),
                     "accept 动作：进入下载模式的确认框（P2 现场校准点）"),
            PlanStep(10, "等待并确认下载完成对话框", "interop",
                     self._powershell_argv(
                         "accept", self.cfg.bridge["accept_download_text"]),
                     f"accept 动作：等待含“{self.cfg.bridge['accept_download_text']}”"
                     "的成功弹窗（P2 现场校准点）"),
            *self._plan_read_logs(11),
            PlanStep(13, "下载后复核", "verify", [],
                     "复核 PLC 在线与运行状态（只读监测链路复核寄存器）",
                     expected_artifacts=(
                         "logs/evidence.jsonl（本次下载全链证据）",
                     )),
        ]

    # ---------- dry-run 渲染 ----------

    def render_dry_run(self, action: str, request: OperationRequest,
                       decision: AuthorizationDecision) -> str:
        steps = (self.plan_build(request) if action == "build"
                 else self.plan_flash(request))
        extra = None
        if decision.granted and request.dry_run:
            extra = ["（授权已满足但未加 --execute：保持 dry-run）"]
        elif not decision.granted:
            extra = [f"（当前未解锁原因: {decision.reason_text}）"]
        return render_plan(action, steps, request, decision, self.name, extra)

    # ---------- 真执行（双因子门禁内） ----------

    def _gate(self, request: OperationRequest, action: str) -> AuthorizationDecision:
        decision = check_bridge_authorization(self.cfg, request.authorized)
        if not decision.granted:
            raise SafetyError(
                f"已拒绝 {action} 真执行：{decision.reason_text}。"
                "解锁方式：.plcidf.toml 设 [bridge] enabled=true，且命令行同时加 "
                "--execute --i-have-authorized。")
        return decision

    def _verify_plc_online(self) -> InteropResult:
        """verify 步：usbipd list（只读）+ PLC 在 Windows 侧（未挂载到 WSL）。

        build/flash 由 Windows 侧 AutoShop 执行：设备若 Attached 到 WSL，
        Windows/AutoShop 反而看不到它，必须处于 Shared/未共享状态。"""
        policy = RetryPolicy.from_config(self.cfg)
        result = run_command(self._usbipd_list_argv(), policy=policy)
        if result.ok:
            entries = device_mod.parse_usbipd_list(result.stdout)
            plc = device_mod.find_plc_entry(self.cfg, entries)
            if plc is None:
                raise SafetyError(
                    f"前置校验失败：BUSID {self.cfg.device['busid']} 设备不存在"
                    "（usbipd list 无该设备），拒绝继续。")
            if plc.is_attached:
                raise SafetyError(
                    f"前置校验失败：BUSID {self.cfg.device['busid']} 仍挂载在 WSL"
                    "（Attached），AutoShop 无法访问。请先在 WSL 执行"
                    f" \"{self.cfg.device['usbipd_exe']}\" detach --busid"
                    f" {self.cfg.device['busid']}，再重试。")
        return result

    def _execute(self, action: str, request: OperationRequest) -> BackendResult:
        """两条管线共用的执行机：门禁 -> 逐步执行 -> 逐步证据 -> 失败即停。"""
        decision = self._gate(request, action)
        steps = self.plan_build(request) if action == "build" else self.plan_flash(request)
        policy = RetryPolicy.from_config(self.cfg)
        executed: list[dict] = []
        for step in steps:
            if step.kind == "verify":
                if step.command:  # 带命令的 verify 步 = usbipd/串口在线校验（只读）
                    result = self._verify_plc_online()
                    if not result.ok:
                        self._evidence_step(action, step, result, ok=False)
                        return BackendResult(False, f"verify 步失败: {step.title}",
                                             outputs={"failed_step": step.index},
                                             steps=steps)
                    self._evidence_step(action, step, result, ok=True)
                    executed.append({"step": step.index, "ok": True})
                continue  # 空命令 verify 步 = 产物核对说明，无动作
            if step.kind == "phase":
                continue  # 渲染用阶段分隔，无动作
            # interop 步：GUI 动作超时上限单独放宽
            step_policy = RetryPolicy(policy.timeout_s if policy.timeout_s > GUI_STEP_TIMEOUT_S
                                      else GUI_STEP_TIMEOUT_S,
                                      policy.retries, policy.backoff_s)
            result = run_command(step.command, policy=step_policy)
            ok = result.ok
            self._evidence_step(action, step, result, ok=ok)
            executed.append({"step": step.index, "ok": ok})
            if not ok:
                return BackendResult(
                    False,
                    f"第 {step.index} 步失败: {step.title}（{result.summary(80)}）",
                    outputs={"failed_step": step.index, "executed": executed},
                    steps=steps)
        if self.evidence:
            self.evidence.log(f"bridge.{action}.done",
                              params={"project": request.project},
                              result={"ok": True, "steps": executed})
        return BackendResult(True, f"{action} 调用链 {len(executed)} 步全部执行完成",
                             outputs={"executed": executed}, steps=steps)

    def _evidence_step(self, action: str, step: PlanStep,
                       result: InteropResult, ok: bool) -> None:
        if self.evidence:
            self.evidence.log(
                f"bridge.{action}.step",
                params={"step": step.index, "title": step.title,
                        "kind": step.kind, "argv": step.command},
                result={"ok": ok, "rc": result.returncode,
                        "attempts": result.attempts,
                        "stdout_head": result.stdout[:200],
                        "stderr_head": result.stderr[:200]})

    # 接口实现
    def execute_build(self, request: OperationRequest) -> BackendResult:
        return self._execute("build", request)

    def execute_flash(self, request: OperationRequest) -> BackendResult:
        return self._execute("flash", request)


# ---------- 小工具（模块级，便于独立测试） ----------

def _plan_spacer(index: int, title: str) -> list[PlanStep]:
    """阶段分隔（渲染用，不产生命令）。"""
    return [PlanStep(index, f"— {title} —", "phase", [])]


def hcp_mtime_artifact(request: OperationRequest) -> str:
    """编译后应更新的工程文件（.hcp）路径。"""
    return request.hcp_path
