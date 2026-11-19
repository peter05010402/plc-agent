# 测试计划：泵站离心泵启停控制（S7-1500 / SCL）

| 编号 | 用例 | 前置条件 | 操作 | 期望结果 |
|---|---|---|---|---|
| TC-01 | 上电初始态 | 无故障 | 保持所有按钮释放 | 所有输出为 FALSE，FaultCode=0 |
| TC-02 | 手动启动 | LevelLow=TRUE，安全回路正常 | 点动 StartBtn | MotorRun 置 TRUE 并自保持 |
| TC-03 | 正常停止 | 运行中 | 按 StopBtn | 输出立即 FALSE |
| TC-04 | 急停 | 运行中 | 断开急停回路（EStop→FALSE） | 输出立即 FALSE，FaultCode=1，故障锁存 |
| TC-05 | 过载 | 运行中 | Overload→TRUE | 输出 FALSE，FaultCode=2 |
| TC-06 | 故障复位 | 故障锁存 | 安全恢复后按 StopBtn | 故障清零，输出保持停止 |
| TC-07 | 复位后重启 | 故障已复位 | 按 StartBtn | 输出重新置 TRUE |
| TC-08 | 故障灯 | 故障锁存 | 观察 2 s | FaultLamp 以约 1 Hz 闪烁 |
| TC-09 | 无液位联锁 | AutoMode=FALSE，LevelLow=FALSE | 按 StartBtn | 输出不置 TRUE |
| TC-10 | 掉电重启 | 运行中 | 断电再上电 | 输出不自行启动（自保持不跨掉电） |
