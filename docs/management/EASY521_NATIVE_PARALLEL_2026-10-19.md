# Easy521 纯 Linux 并行开发批次

2026-10-19。响应用户“那就并行继续”。本批直接在唯一 `main` 上按文件范围并行，没有分支/worktree；AI子代理不是外聘人员，阶段交付后也不代表后台持续研发。统筹独占设备操作、提交、推送与 GitHub 状态同步。

## 分工、产物与实际验收

| 工作线 | 执行 / 复核 | 新增行为 | 实际验证 | 未完成 |
|---|---|---|---|---|
| 原生USB | `linux_usb_native` / `distribution` | ctypes/libusb-1.0固定只读；现有两reader显式选择Linux，Windows默认兼容 | fake libusb/codec、零I/O预览守卫；统筹真实库无目标枚举与拒绝；sdist/wheel仓库外验证 | PLC原生读请求、永久身份/程序校准；无下载/CPU写接口 |
| 应用配置 | `application_packaging` / `distribution` | 严格FOID结构/哈希/源比较；恢复调用方模块版本来源 | 16项合成测试；12个真实配置、11对源文件离线比较；31项输入复核 | opaque 32B复现、配置序列化、任务/变量完整文件闭包 |
| 原生ST/ARM | `native_st_abi` / `distribution` | 单个外部DINT自增的独立前端→IR→真实ARM对象 | 18项测试含4项真实LLVM；三个增量对象不同；同IR x86执行45样本 | 通用ST、变量地址/保持/任务、完整runtime ABI、ARM/真机执行 |
| 集成与交付 | `/root` | 源→编译对象→两级LLD链接；新增资源安装与CI；WSL主机USB准备 | +1/+2分别真实编译并链接、哈希关联；611项工具与825项源码/monitor回归；独立安装与实际motor场景 | 关闭Windows后完整Easy521下装/运行闭环 |

工具611项为收集的顶层测试数，JUnit另包含1029个嵌套子用例记录；不把它们与代理分项重复相加。825项源码/monitor测试启用真实MatIEC，排除1项Windows实际USB枚举，0失败/0跳过。安装包另验真实motor 6场景/56断言，是分发集成结果，不当作PLC运行成绩。

所有命令、源码哈希、编译/链接报告和验证计数见[软件验证记录](../../evidence/easy521-native-parallel-20261019T062312Z/software-validation.json)。独立只读审阅无剩余P0/P1；未以审阅或测试数量替代硬件验收。

## 本机条件与需要用户完成的一步

统筹实际加载WSL的`vhci-hcd`主机模块，原本缺失的USB sysfs/devfs出现，只看到两个root hubs。PLC在Windows新鲜枚举中为唯一`0471:a531 / 7-2 / Not shared`，AutoShop未运行；当前Windows会话非管理员。没有为此关闭应用、切换设备归属或发送PLC请求。

微软要求`usbipd bind`使用管理员权限，已向用户请求在管理员PowerShell先核对`usbipd list`，再对当前匹配的BUSID执行共享。收到结果后统筹重新核对当前设备，尝试WSL附加及原生只读请求。不会用历史位置替代新鲜设备选择；也不将待用户步骤写成软件实现未开始。具体条件见[原生USB报告](../research/firmware/24-linux-native-usb-2026-10-19.md)。

本批没有新增PLC变量写入、CPU RUN/STOP、应用下装/清除或输出动作。之前Windows后端的Y0闪烁/STOP目视结果仍按[历史真机报告](../research/firmware/20-powered-led-bench-2026-10-19.md)保留，不据其推断当前负载、供电或身份。

## 剩余门禁和下一步

1. **原生只读真机**：用户完成共享后，重新核对并附加设备；用固定GCM/时钟读取保存原始帧和目标证据。读取成功也不等于应用下载成功。
2. **合法变量和任务绑定**：当前`COUNTER`没有已证明的地址，不能套用另一个工程八项变量的绑定器。先建立最小固定接口的合法分配与任务调用来源，避免编译对象与配置错配。
3. **完整应用生成**：解析配置有效长度和序列化过程、版本表初始化及型号分支，形成同源候选闭包；保留FOID字段未知，不伪造生成器。
4. **原生应用事务**：恢复已初始化的会话、文件传输与有序提交及设备读回，写入发起后不确定就保留unknown，不自动重放。
5. **最终验收**：Windows后端关闭，完成两轮源码变化→完整构建→下装→运行观察，保持来源/候选/目标对应，再宣告Easy521纯Linux全流程通过。

相关研究：[22：FOID](../research/firmware/22-linux-foid-packaging-2026-10-19.md)、[23：ST/ARM](../research/firmware/23-linux-st-abi-2026-10-19.md)、[24：Linux USB](../research/firmware/24-linux-native-usb-2026-10-19.md)。GitHub继续跟踪[#10](https://github.com/BUG423/other-plc-idf/issues/10)、[#8](https://github.com/BUG423/other-plc-idf/issues/8)、[#12](https://github.com/BUG423/other-plc-idf/issues/12)；当前不提前结案。
