# Easy521 固定接口原生编译实验

两个 MAIN 正文分别每次调用加 `1.0` 和 `2.0`，用于验证 Linux 新源码与 PLC 运行变化的关联。变量复用固定工程的 `g_rBESS_Pcmd:REAL`；实验中的数值没有功率单位，不适用于实际 BESS 工艺。

默认 LLVM 前端只接受这两种单句正文与注释。显式 `matiec-clang` 后端通过真实 MatIEC 支持该固定 REAL 的无局部状态赋值与 IF/ELSE；`conditional_plus1.st`／`conditional_plus2.st` 展示小于10时递增、否则清零。两者均不支持任意 IEC 程序。地址来自同工程 GVT，并与 CSV 交叉核验；任务周期、保持和设备兼容性分别验收。

`tools/bind_native_application.py` 使用这些源码、`globals.json`、调用方自有的固定工程模板和两项厂商库，经 Linux LLVM/LLD 生成绑定对象；`tools/prepare_easy521_application.py` 继续组合 18 文件应用候选。模板与厂商二进制不随仓库分发，不能用其他机型文件代替。命令参数、输入哈希与复现步骤见[编译绑定记录](../../docs/research/firmware/27-native-bound-application-2026-10-19.md)和[应用配置记录](../../docs/research/firmware/28-fixed-application-config-2026-10-19.md)。

候选生成通过不代表真机下装或执行通过。当前设备读回、下载与运行结果应以带日期的独立实验记录为准。

公开条件示例已由统筹实际完成源到现机配置派生和命令预览，步骤、主机语义结果与未验收项见[原生集成记录](../../docs/research/firmware/36-native-linux-integration-2026-10-19.md)。
