# libchassis 使用文档

## 概述

`libchassis.so` 是一个用于控制麦克纳姆轮底盘的 C 动态链接库，通过 wiringOP 库驱动两个迷你 L298N 模块控制 4 个 TT 马达，支持全向移动。

## 编译

```bash
# x86 开发环境（使用 mock，无需 wiringPi）
make

# ARM64 目标板（链接真实 wiringPi）
make    # 自动检测架构

# 编译并运行测试
make test

# 清理
make clean
```

## API 接口

### `chassis_set_log_level(int level)`

设置日志级别。可选值：

| 常量 | 值 | 说明 |
|------|:--:|------|
| `CHASSIS_LOG_DEBUG` | 0 | 全部日志 |
| `CHASSIS_LOG_INFO` | 1 | 信息 + 错误（默认） |
| `CHASSIS_LOG_ERROR` | 2 | 仅错误 |

### `int chassis_init(const int pins[8])`

初始化 GPIO 引脚。**必须在任何运动控制之前调用。**

参数 `pins` 为长度 8 的数组，索引映射：

| 索引 | 宏名 | 说明 |
|:----:|-------|------|
| 0 | `FL_IN1` | 左前 IN1 |
| 1 | `FL_IN2` | 左前 IN2 |
| 2 | `FR_IN1` | 右前 IN1 |
| 3 | `FR_IN2` | 右前 IN2 |
| 4 | `RL_IN1` | 左后 IN1 |
| 5 | `RL_IN2` | 左后 IN2 |
| 6 | `RR_IN1` | 右后 IN1 |
| 7 | `RR_IN2` | 右后 IN2 |

返回：`0` 成功，`-1` 失败。

### `int chassis_set_velocity(float vx, float vy, float omega)`

全向运动控制。三个参数均为归一化输入，范围 `[-1.0, 1.0]`：

| 参数 | 正方向 | 负方向 |
|------|--------|--------|
| `vx` | 右 | 左 |
| `vy` | 前 | 后 |
| `omega` | 顺时针 | 逆时针 |

返回：`0` 成功，`-1` 未初始化。

### `void chassis_cleanup(void)`

停止所有电机，拉低全部引脚。**程序退出前必须调用**，防止电机失控。

## C 语言使用示例

```c
#include "chassis.h"

int main(void) {
    const int pins[8] = {0,1, 2,3, 4,5, 6,7};

    chassis_init(pins);
    chassis_set_velocity(0.0f, 1.0f, 0.0f);  // 前进
    // ... 运动逻辑 ...
    chassis_cleanup();
    return 0;
}
```

## Python (ctypes) 调用示例

```python
import ctypes, time

lib = ctypes.CDLL("./libchassis.so")

# 定义参数类型
lib.chassis_init.argtypes = [ctypes.POINTER(ctypes.c_int)]
lib.chassis_init.restype  = ctypes.c_int

lib.chassis_set_velocity.argtypes = [ctypes.c_float, ctypes.c_float, ctypes.c_float]
lib.chassis_set_velocity.restype  = ctypes.c_int

lib.chassis_cleanup.argtypes = []
lib.chassis_cleanup.restype  = None

# 初始化
pins = (ctypes.c_int * 8)(0, 1, 2, 3, 4, 5, 6, 7)
lib.chassis_init(pins)

# 前进 2 秒
lib.chassis_set_velocity(0.0, 1.0, 0.0)
time.sleep(2)

# 停止并清理
lib.chassis_set_velocity(0.0, 0.0, 0.0)
lib.chassis_cleanup()
```

## 推荐 8 路 GPIO（实机可用）

以下引脚已在当前 OrangePi Zero3 实机上确认处于可用状态，且均位于物理引脚 1-26 范围：

| 角色 | wiringPi | 物理脚位 | SoC |
|---|---:|---:|---|
| FL_IN1 | 6 | 12 | PC11 |
| FL_IN2 | 5 | 11 | PC6 |
| FR_IN1 | 7 | 13 | PC5 |
| FR_IN2 | 8 | 15 | PC8 |
| RL_IN1 | 10 | 18 | PC14 |
| RL_IN2 | 9 | 16 | PC15 |
| RR_IN1 | 13 | 22 | PC7 |
| RR_IN2 | 16 | 26 | PC10 |

对应数组：

```python
pins = (ctypes.c_int * 8)(6, 5, 7, 8, 10, 9, 13, 16)
```

## 简单控制脚本

项目根目录新增了 `simple_control.py`，可直接调用 `libchassis.so` 进行基础控制。

默认启动为交互式菜单模式：

```bash
make
sudo python3 simple_control.py
```

交互示例（前进 1 秒）：
- 菜单输入 `1`（forward）
- 速度输入 `0.4`（或直接回车使用默认值）
- 时长输入 `1`

使用方式：

```bash
make
sudo python3 simple_control.py               # 默认 interactive
sudo python3 simple_control.py interactive   # 显式 interactive
sudo python3 simple_control.py stop
sudo python3 simple_control.py forward --speed 0.4 --duration 1.5
sudo python3 simple_control.py left --speed 0.5 --duration 1.0
sudo python3 simple_control.py cw --speed 0.3 --duration 1.2
sudo python3 simple_control.py demo --speed 0.4 --duration 0.8
```

动作参数说明：
- `action`: `interactive|demo|forward|backward|left|right|cw|ccw|stop`
- `--speed`: 归一化速度，范围 `[0.0, 1.0]`
- `--duration`: 每次动作持续秒数
- `--log-level`: `0=DEBUG, 1=INFO, 2=ERROR`
