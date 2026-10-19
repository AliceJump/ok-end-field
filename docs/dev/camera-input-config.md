# 摄像机输入配置读取

`src/core/camera_input_config.py` 提供普通键鼠默认相机的理论前馈参数。函数读取当前用户注册表中的保存设置，并校验安装文件、VFS 索引与三个相机资源包切片。客户端或配置资源更新后会报错，要求重新核验并更新配置数据。

```python
from src.core.camera_input_config import (
    get_default_camera_vertical_track,
    get_default_camera_yaw_ratio,
)

game_dir = r"D:\GAMES\Endfield Game"  # 调用方提供安装目录
yaw = get_default_camera_yaw_ratio(1920, game_dir=game_dir)
track = get_default_camera_vertical_track(1080, game_dir=game_dir)
```

宽、高参数必须是 Unity 实际 `Screen.width` / `Screen.height`，不自动使用桌面、保存分辨率或缩放截图尺寸。完整文件哈希适合初始化调用，不能逐帧调用。当前支持国服 Windows 保存设置，未找到设置时不猜测灵敏度。

## 水平

返回单位为 **度 / 游戏收到的相对 X 计数**。默认灵敏度 0、1920 宽度时为 `0.084375`：

```text
k_x = 0.5 × 0.9 × 360 / width × dragSpeed × C(S_x)
      × horizontalSpeedFactor × mouseSensitivity × direction
```

`minimap=True` 将符号转换为项目逆时针小地图箭头坐标，前提是角色已经稳定追随相机，例如滑索停留状态。这个转换不适用于任意角色移动状态。

## 纵向轨道

纵向输入控制轨道参数，不是直接加俯仰角。返回字典包含线性区域的变化率、配置范围、插值速度、加速配置、轨道高度和距离曲线，以及几何采样。

```text
k_y = 0.5 × 0.63 / height × dragSpeed × C(S_y)
      × verticalSpeedFactor × mouseSensitivity × direction
```

默认灵敏度 0、1080 高度时为 `0.000291666667` **轨道单位 / 游戏收到的相对 Y 计数**。Windows 鼠标 Y 路径乘 -1，纵向拖动方法又取反，因此默认计数正方向增加轨道参数；反向设置翻转此符号。此处没有水平的 ×360。

原生方法在计算拖动前还按帧速度检查加速：阈值是归一化输入每秒 1000。默认 1080 高度下约对应每秒 3428571 个收到的 Y 计数；60 Hz 时约 57143 个计数/帧。超过阈值后，不能继续使用固定线性比率。函数返回阈值和完整加速参数，不假装为高速度输入提供恒定比率。

默认配置范围为 0～1，纵向插值速度为 16。`SetVerticalValue` 保存内部目标值时不会截断到该范围；不能把原始目标、插值中的值和最终可见轨道位置混为一谈。

| 轨道参数 | 高度曲线值 | 距离曲线值 | 几何参考角 |
| ---: | ---: | ---: | ---: |
| 0.00 | -2.0 | 0.20 | -84.29° |
| 0.20 | -1.6 | 3.00 | -28.07° |
| 0.40 | -0.3 | 5.21 | -3.30° |
| 0.60 | 1.7 | 6.375 | 14.93° |
| 0.84 | 4.5 | 6.00 | 36.87° |
| 1.00 | 6.6 | 4.50 | 55.71° |

几何参考角按 `atan2(高度曲线值, 距离曲线值)` 计算，与资源生成 pitch 查表时使用的曲线对应；它不包含最终相机的肩部/注视偏移、缩放、碰撞或状态覆盖。资源中的反向 `pitchToVerticalValue` 查表只生成到轨道 0.99（约 54.287°），不能把它直接外推到 1。两条几何曲线按非加权 Hermite 插值，相同鼠标位移在不同轨道位置不产生相同俯仰角变化。

## 版本和适用边界

配置数据位于 `assets/data/camera_input_profile.json`，仅包含计算所需的数值和版本校验信息；原始解包、二进制、反汇编及独立研究资料留在主仓库忽略目录。

2026-10-19 核验的 GameAssembly SHA-256 为 `ab00f1c41e13edf9816f142b166e3c2536d5bc7707992c39e5ed52d1cc888e2c`，metadata 为 `461321d9dca6a832bce3bd2390402efc7c83c145554e97a8058bb68fbec1c8ca`。重新定位的 Windows 鼠标回调 method 497826 / RVA `0x57097d0` 中相对输入乘 0.5；水平拖动 method 52899 / RVA `0x54a0fb0`，纵向拖动 method 52900 / RVA `0x54a2270`。当前 Lua 的水平/纵向速度分别为 0.9 / 0.63。相机曲线和倍率经当前 VFS manifest 定位、包 MD5 校验、专用解包和内嵌 type tree 完整读取；Rewired 配置正文与此前严格解析结果逐字节相同。

函数没有读取运行中的相机状态和未保存的设置，也不能证明模拟接口发送的 dx/dy 与游戏收到的计数相等。实际控制仍需等待角度稳定并反馈校准。此改动只提供查询函数，不改变现有任务的转向或学习策略。
