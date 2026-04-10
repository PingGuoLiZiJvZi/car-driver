# OrangePi Zero3 GPIO 可用引脚清单（实机检测）

检测时间：2026-04-06

检测环境：
- 设备型号：OrangePi Zero3
- 内核：Linux orangepizero3 6.1.31-sun50iw9 #1.0.4 SMP Thu Jul 11 16:37:41 CST 2024 aarch64 GNU/Linux
- 数据来源：
  - `sudo gpio readall`
  - `sudo cat /sys/kernel/debug/pinctrl/*/pins`
  - `sudo cat /sys/kernel/debug/pinctrl/*/pinmux-pins`

判定口径：
- "可用" = 当前内核 pinmux 状态为 `UNCLAIMED`（未被驱动占用）
- "占用" = 当前已被内核设备/驱动绑定（如 SPI/UART/ETH/IR/LED）
- 注意："可用"不等于"一定在排针上"，部分 GPIO 仅为 SoC 管脚，不在外部排针引出

## 1. 总览统计

### 1.1 GPIO 控制器总量

| GPIO 控制器 | 总引脚数 | 可用 | 占用 |
|---|---:|---:|---:|
| 300b000.pinctrl (`/dev/gpiochip0`) | 85 | 45 | 40 |
| 7022000.pinctrl (`/dev/gpiochip1`) | 2 | 2 | 0 |
| 合计 | 87 | 47 | 40 |

### 1.2 34Pin 排针中 GPIO 能力概览

| 项目 | 数量 |
|---|---:|
| 具备 GPIO 功能的排针位 | 23 |
| 当前可用（未占用） | 19 |
| 当前占用 | 4 |

## 2. 34Pin 排针 GPIO 详细表

说明：本表基于 `gpio readall` 与 pinmux 占用状态交叉整理。

| 排针物理脚位 | SoC GPIO 编号 | SoC 名称 | wiringOP 名称 | wPi | 当前模式 | 状态 | 占用者 |
|---:|---:|---|---|---:|---|---|---|
| 3 | 229 | PH5 | SDA.3 | 0 | OFF | 可用 | - |
| 5 | 228 | PH4 | SCL.3 | 1 | OFF | 可用 | - |
| 7 | 73 | PC9 | PC9 | 2 | OFF | 可用 | - |
| 8 | 226 | PH2 | TXD.5 | 3 | OFF | 可用 | - |
| 10 | 227 | PH3 | RXD.5 | 4 | OFF | 可用 | - |
| 11 | 70 | PC6 | PC6 | 5 | ALT5 | 可用 | - |
| 12 | 75 | PC11 | PC11 | 6 | OFF | 可用 | - |
| 13 | 69 | PC5 | PC5 | 7 | ALT5 | 可用 | - |
| 15 | 72 | PC8 | PC8 | 8 | OFF | 可用 | - |
| 16 | 79 | PC15 | PC15 | 9 | OFF | 可用 | - |
| 18 | 78 | PC14 | PC14 | 10 | OFF | 可用 | - |
| 19 | 231 | PH7 | MOSI.1 | 11 | OFF | 可用 | - |
| 21 | 232 | PH8 | MISO.1 | 12 | OFF | 可用 | - |
| 22 | 71 | PC7 | PC7 | 13 | OFF | 可用 | - |
| 23 | 230 | PH6 | SCLK.1 | 14 | OFF | 可用 | - |
| 24 | 233 | PH9 | CE.1 | 15 | OFF | 可用 | - |
| 26 | 74 | PC10 | PC10 | 16 | OFF | 可用 | - |
| 27 | 65 | PC1 | PC1 | 17 | OFF | 可用 | - |
| 28 | 224 | PH0 | PWM3 | 21 | ALT2 | 占用 | device 5000000.serial |
| 29 | 272 | PI16 | PI16 | 18 | ALT2 | 占用 | device 5020000.ethernet |
| 30 | 225 | PH1 | PWM4 | 22 | ALT2 | 占用 | device 5000000.serial |
| 31 | 262 | PI6 | PI6 | 19 | OFF | 可用 | - |
| 33 | 234 | PH10 | PH10 | 20 | ALT3 | 占用 | device 7040000.ir |

## 3. SoC 全量可用 GPIO（UNCLAIMED）

以下为当前系统判定为未占用的全部 GPIO（共 47 个）：

| GPIO 编号 | 名称 |
|---:|---|
| 0 | PA0 |
| 1 | PA1 |
| 2 | PA2 |
| 3 | PA3 |
| 4 | PA4 |
| 5 | PA5 |
| 6 | PA6 |
| 7 | PA7 |
| 8 | PA8 |
| 9 | PA9 |
| 10 | PA10 |
| 11 | PA11 |
| 12 | PA12 |
| 65 | PC1 |
| 69 | PC5 |
| 70 | PC6 |
| 71 | PC7 |
| 72 | PC8 |
| 73 | PC9 |
| 74 | PC10 |
| 75 | PC11 |
| 78 | PC14 |
| 79 | PC15 |
| 198 | PG6 |
| 199 | PG7 |
| 200 | PG8 |
| 201 | PG9 |
| 202 | PG10 |
| 203 | PG11 |
| 204 | PG12 |
| 205 | PG13 |
| 206 | PG14 |
| 207 | PG15 |
| 208 | PG16 |
| 209 | PG17 |
| 211 | PG19 |
| 226 | PH2 |
| 227 | PH3 |
| 228 | PH4 |
| 229 | PH5 |
| 230 | PH6 |
| 231 | PH7 |
| 232 | PH8 |
| 233 | PH9 |
| 262 | PI6 |
| 352 | PL0 |
| 353 | PL1 |

## 4. 当前占用 GPIO（用于避让）

| GPIO 编号 | 名称 | 占用者 |
|---:|---|---|
| 64 | PC0 | device 5010000.spi |
| 66 | PC2 | device 5010000.spi |
| 67 | PC3 | device 5010000.spi |
| 68 | PC4 | device 5010000.spi |
| 76 | PC12 | GPIO 300b000.pinctrl:76 |
| 77 | PC13 | GPIO 300b000.pinctrl:77 |
| 80 | PC16 | GPIO 300b000.pinctrl:80 |
| 160 | PF0 | device 4020000.mmc |
| 161 | PF1 | device 4020000.mmc |
| 162 | PF2 | device 4020000.mmc |
| 163 | PF3 | device 4020000.mmc |
| 164 | PF4 | device 4020000.mmc |
| 165 | PF5 | device 4020000.mmc |
| 166 | PF6 | GPIO 300b000.pinctrl:166 |
| 192 | PG0 | device 4021000.mmc |
| 193 | PG1 | device 4021000.mmc |
| 194 | PG2 | device 4021000.mmc |
| 195 | PG3 | device 4021000.mmc |
| 196 | PG4 | device 4021000.mmc |
| 197 | PG5 | device 4021000.mmc |
| 210 | PG18 | GPIO 300b000.pinctrl:210 |
| 224 | PH0 | device 5000000.serial |
| 225 | PH1 | device 5000000.serial |
| 234 | PH10 | device 7040000.ir |
| 256 | PI0 | device 5020000.ethernet |
| 257 | PI1 | device 5020000.ethernet |
| 258 | PI2 | device 5020000.ethernet |
| 259 | PI3 | device 5020000.ethernet |
| 260 | PI4 | device 5020000.ethernet |
| 261 | PI5 | device 5020000.ethernet |
| 263 | PI7 | device 5020000.ethernet |
| 264 | PI8 | device 5020000.ethernet |
| 265 | PI9 | device 5020000.ethernet |
| 266 | PI10 | device 5020000.ethernet |
| 267 | PI11 | device 5020000.ethernet |
| 268 | PI12 | device 5020000.ethernet |
| 269 | PI13 | device 5020000.ethernet |
| 270 | PI14 | device 5020000.ethernet |
| 271 | PI15 | device 5020000.ethernet |
| 272 | PI16 | device 5020000.ethernet |

## 5. 物理引脚 1-26 速查表

说明：
- 数据来源于当前实机的 `gpio readall` 与 pinmux 状态。
- 下表中的 GPIO 状态均为当前系统时刻下的状态，重启或启用新外设后可能变化。

| 物理脚位 | 类型 | SoC GPIO | SoC 名称 | wiringOP 名称 | wPi | 当前模式 | 当前状态 |
|---:|---|---:|---|---|---:|---|---|
| 1 | 电源 | - | - | 3.3V | - | - | 电源 |
| 2 | 电源 | - | - | 5V | - | - | 电源 |
| 3 | GPIO | 229 | PH5 | SDA.3 | 0 | OFF | 可用 |
| 4 | 电源 | - | - | 5V | - | - | 电源 |
| 5 | GPIO | 228 | PH4 | SCL.3 | 1 | OFF | 可用 |
| 6 | 地 | - | - | GND | - | - | 地 |
| 7 | GPIO | 73 | PC9 | PC9 | 2 | OFF | 可用 |
| 8 | GPIO | 226 | PH2 | TXD.5 | 3 | OFF | 可用 |
| 9 | 地 | - | - | GND | - | - | 地 |
| 10 | GPIO | 227 | PH3 | RXD.5 | 4 | OFF | 可用 |
| 11 | GPIO | 70 | PC6 | PC6 | 5 | ALT5 | 可用 |
| 12 | GPIO | 75 | PC11 | PC11 | 6 | OFF | 可用 |
| 13 | GPIO | 69 | PC5 | PC5 | 7 | ALT5 | 可用 |
| 14 | 地 | - | - | GND | - | - | 地 |
| 15 | GPIO | 72 | PC8 | PC8 | 8 | OFF | 可用 |
| 16 | GPIO | 79 | PC15 | PC15 | 9 | OFF | 可用 |
| 17 | 电源 | - | - | 3.3V | - | - | 电源 |
| 18 | GPIO | 78 | PC14 | PC14 | 10 | OFF | 可用 |
| 19 | GPIO | 231 | PH7 | MOSI.1 | 11 | OFF | 可用 |
| 20 | 地 | - | - | GND | - | - | 地 |
| 21 | GPIO | 232 | PH8 | MISO.1 | 12 | OFF | 可用 |
| 22 | GPIO | 71 | PC7 | PC7 | 13 | OFF | 可用 |
| 23 | GPIO | 230 | PH6 | SCLK.1 | 14 | OFF | 可用 |
| 24 | GPIO | 233 | PH9 | CE.1 | 15 | OFF | 可用 |
| 25 | 地 | - | - | GND | - | - | 地 |
| 26 | GPIO | 74 | PC10 | PC10 | 16 | OFF | 可用 |

物理引脚 1-26 统计：
- GPIO：17 个（当前全部可用）
- 电源：4 个（3.3V x2，5V x2）
- 地：5 个

## 6. 复查命令

后续若系统升级、启用/禁用外设后，可用以下命令重新生成同类数据：

```bash
cat /proc/device-tree/model | tr -d '\0'
sudo gpio readall
sudo cat /sys/kernel/debug/pinctrl/300b000.pinctrl/pinmux-pins
sudo cat /sys/kernel/debug/pinctrl/7022000.pinctrl/pinmux-pins
```
