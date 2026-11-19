# SCL 功能块结构与书写规则

## 块类型选择
- 有状态、需要多个实例（多台泵/多台电机）→ `FUNCTION_BLOCK`（FB）+ 背景数据块（实例 DB）。
- 纯组合逻辑、无记忆 → `FUNCTION`（FC）。
- 只在 OB 里做调用与调度，不要把工艺逻辑写进 OB1；OB 里写逻辑无法做单元测试，也无法多实例复用。

## 标准结构
```
FUNCTION_BLOCK "MotorCtrl"
{ S7_Optimized_Access := 'TRUE' }
VERSION : 0.1
   VAR_INPUT
      StartBtn : Bool;
   END_VAR
   VAR_OUTPUT
      MotorRun : Bool;
   END_VAR
   VAR
      statRun : Bool;
   END_VAR
BEGIN
   // 逻辑
END_FUNCTION_BLOCK
```

## 分区语义（S7-1500 / 优化块）
| 分区 | 用途 | 注意 |
|---|---|---|
| VAR_INPUT | 外部传入的按钮、传感器、设定值 | 禁止在块内赋值 |
| VAR_OUTPUT | 驱动输出、状态、故障码 | 每条执行路径都应给出确定值 |
| VAR_IN_OUT | 需要读写的引用（数组、结构体） | 少用，调试困难 |
| VAR（STAT） | 状态位、锁存、定时器实例 | 掉电保持需勾 Retain |
| VAR_TEMP | 单次扫描的中间计算 | 不能存定时器实例，不能跨扫描记忆 |

## 语法硬约束
- 每条声明与语句以 `;` 结尾；`END_IF;` `END_CASE;` `END_FOR;` 同样带分号。
- `IF ... THEN` / `END_IF`、`CASE ... OF` / `END_CASE`、`FOR ... DO` / `END_FOR` 必须成对。
- 定时器/计数器用 IEC 实例调用：`#tonBlink(IN := #statFault, PT := T#500ms);`，
  实例必须声明在静态区（`VAR`），不能放 `VAR_TEMP`。
- 时间字面量：`T#500ms`、`T#2s`、`T#1m30s`；十六进制：`16#0001`；二进制：`2#0000_1111`。
- 局部变量加 `#`，全局/块引用加双引号 `"DB_Name".Member`。

## 绝对地址
- 格式必须是 `%I0.0`、`%Q0.0`、`%IW64`、`%MW10`，漏掉 `%` 直接编译失败。
- 开启优化块访问后，块内部按符号访问，绝对地址只应出现在 IO 表或注释里；
  依赖绝对地址的 `PEEK/POKE` 在优化块中不可用。

## 常见 LLM 生成错误
1. `END_IF` 漏分号或漏闭合（嵌套 IF 时最常发生）。
2. 变量名拼错，导致"向未声明变量赋值"。
3. 把 `TON` 实例声明在 `VAR_TEMP`。
4. 直接给 `VAR_INPUT` 赋值。
5. 地址写成 `I0.0`（缺 `%`）。
6. 只在某条分支给输出赋值，其余分支保持上次扫描值（输出抖动）。
