# 状态机、定时器与扫描周期

## 为什么用状态机而不是一串 IF
多步顺序控制（启动→运行→停机→故障）应显式建模状态：

```
CASE #statStep OF
    0:  // 待机
        IF #StartBtn AND #tmpSafetyOk THEN
            #statStep := 10;
        END_IF;
    10: // 启动中（等反馈）
        #MotorRun := TRUE;
        #tonFeedback(IN := #MotorRun, PT := T#3s);
        IF #RunFeedback THEN
            #statStep := 20;
        ELSIF #tonFeedback.Q THEN
            #FaultCode := 16#0004;
            #statStep := 90;
        END_IF;
    20: // 运行
        ...
    90: // 故障
        #MotorRun := FALSE;
END_CASE;
```
- 每个状态只做一件事，转移条件写清楚；异常统一跳到故障态。
- 状态号用常量或 `Int`，不要用实数比较。

## 定时器
- IEC 定时器：`TON`（延时接通）、`TOF`（延时断开）、`TP`（脉冲）。
- 调用形式 `#inst(IN := ..., PT := ...);`，读结果用 `#inst.Q` / `#inst.ET`。
- 实例必须在静态区声明；每个定时器独立实例，不要复用同一个实例给两处逻辑。
- 闪烁灯：`TON` 自复位实现约 1 Hz：
  ```
  #tonBlink(IN := NOT #tonBlink.Q, PT := T#500ms);
  IF #tonBlink.Q THEN
      #statBlink := NOT #statBlink;
  END_IF;
  ```

## 扫描周期相关
- 程序按扫描顺序自上而下执行，同一变量在同一扫描内先写后读才有意义；
  跨网络依赖执行顺序是隐性 bug 源。
- 不要用"循环等待"实现延时（会看门狗超时），延时一律用定时器。
- 一个扫描周期内输出可能被多次赋值，只有最后一次生效 —— 输出映射集中放在程序末尾最安全。

## 自保持（启停）标准写法
```
IF #tmpSafetyOk AND NOT #statFault THEN
    IF #StopBtn THEN
        #statRun := FALSE;          // 停止优先
    END_IF;
    IF #StartBtn THEN
        #statRun := TRUE;
    END_IF;
ELSE
    #statRun := FALSE;              // 故障强制停机
END_IF;
```
- 停止优先于启动，避免同时按下时误启动。
- 自保持变量放静态区；掉电重启后不应自动恢复运行（需明确需求，必要时用 Retain 但要有上电确认）。
