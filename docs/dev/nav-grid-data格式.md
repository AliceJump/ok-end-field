# nav-grid-data 数据与上传约定

本文是 ok-end-field 与 nav-grid-editor 协作整理的兼容方案，面向地图区域扩展与资源上传。
采用现有 PNG、JSON、NPZ 格式；不为扩大游戏区域更换网格编码。
统一发布清单由新版编辑器导出，当前运行端仍按现有目录读取。

## 现有格式保持不变

```text
nav-grid-data/
  dataset.json                         新增：统一发布索引，详见下文
  levels.json                          全部已声明层，包含尚未画网格的层
  links.json                           显式跨层连接
  <map>_<zoom>.grid.npz                 主图网格
  <map>__<tier>_<zoom>.grid.npz          分层网格
  localization/
    manifest.json                      现有定位文件完整性清单
    maps/<map>/4/<map>_4.png
    maps/<map>/4/<map>_4_mapping.json
    layers/catalog.json
    layers/<map>/<level>/<tier>.png
```

| 数据 | 约定 | 当前消费端 |
|---|---|---|
| 网格 | `magic=okef-nav-grid`，`schema_version=2`；`cells` 为二维 `uint8`，0 未知、1 可走、2 阻挡；`meta` 为 JSON 字符串 | `src/nav/grid_io.py` |
| 主图标定 | `matrix` 为像素到世界 X/Z 的 2×3 仿射矩阵；`inverse_matrix` 为其逆变换；`image_size` 与 PNG 一致 | `src/localization/minimap_map_locator.py` |
| 分层底图 | `catalog.json` schema 1，保留官方层 ID、`level_id`、`map_category`、bounds、尺寸和缺失资源声明 | `src/localization/layered_minimap_locator.py` |
| 层声明 | `levels.json` schema 1；声明层与实际存在的网格分别记录 | `src/nav/layered_map.py` |
| 跨层连接 | 沿用 links v1/v2/v3 读取兼容；高度缺失就是未知 | `src/nav/layered_map.py` |

## 统一清单 v1

新增仓库根 `dataset.json`，只索引资源身份、关联与文件完整性，不在其中另写一份
cells、仿射矩阵、bounds 或层声明。现有 `localization/manifest.json` 继续保留；
统一清单通过 SHA-256 固定定位子清单，再展开其 76 项资源；不另写第二份定位文件摘要。

| 字段 | 内容 |
|---|---|
| `schema_version` / `kind` | `1` / `okef-nav-dataset`，与内部 NPZ、catalog 的版本分别演进 |
| `dataset_id` | 数据集稳定身份，如 `endfield` |
| `release_id` / `parent_release_id` | 本次不可变发布 ID 与可为空的前一发布 ID；Git 提交号作为发布记录另存，避免文件包含自身提交号 |
| `coordinate_spaces[]` | `id`、`kind=world/independent`、`map_name`、独立场景的 `level_id`、坐标单位 |
| `declarations` | 固定为 `levels.json`，完整层声明仍只由 levels 提供 |
| `localization_manifest` | 现有定位 manifest 的 `path`、`size`、`sha256`；允许纯网格包为 null |
| `registrations[]` | 按坐标系和资源身份关联 visual 与 grid，详见下表 |
| `navigation_files[]` | 导航文件的 `role=grid/levels/links`、相对 `path`、字节数 `size`、小写十六进制 `sha256` |

每条 registration 的联合键为 `(coordinate_space_id, map_name, tier, zoom)`，不得重复：

| 字段 | 内容 |
|---|---|
| `coordinate_space_id` | 必须引用已声明坐标系；大地图扩区域时保持不变 |
| `map_name` / `tier` / `zoom` | 对应 NPZ 元数据；主图 tier 空串，zoom 为字符串 |
| `visual` | 主图为 `{kind:base,image,mapping}`；分层为 `{kind:layer,image,catalog_entry}`；允许 null |
| `grid` | 对应 `.grid.npz` 的相对路径；未标网格时为 null |

分层 `catalog_entry` 引用 layers/catalog 的唯一层 ID；visual、mapping、catalog、NPZ
都必须直接或经定位子清单被 SHA-256 绑定。通过共同的坐标系身份关联它们，不能仅凭文件名相似
或最近修改时间拼装。grid 与 visual 不允许同时为空；没有实体资源的层仍可存在于 levels。

所有路径相对数据仓根，统一使用 `/`；拒绝绝对路径、`..`、越出包根的链接、重复路径
与重复身份。导航文件表与定位子清单合起来列出当前快照完整的受管资源，
不包含 dataset 自身、文档或工具，避免递归自校验。定位子清单中的文件路径相对其所在
`localization/` 目录；registration 路径相对仓库根。文件删除以新快照为准，不能把旧目录
直接覆盖后残留的旧网格当作新数据。

坐标单位沿用现有游戏世界数值，不在发布时缩放；`game_world_unit` 表示该约定。
图像、网格尺寸可以导出用于报告，但不构成已经验证的定位或导航覆盖。第一版不设置
一个笼统的 `ready=true`：分别校验资源、标注和路线实际可用性。

编辑器应从一次冻结的保存快照自动生成清单、计算摘要并校验引用，再打包；上传者
只需保存标注并选择导出。导出功能在 nav-grid-editor 实现；消费端运行时仍按现有目录读取，
不会自动下载或切换到最新清单版本。

权威 schema、基线示例和发布端说明位于数据仓的 `schemas/dataset.schema.json`、
`examples/dataset.example.json`、`docs/data-format.md`。本文不另定义第二套 schema。

## 发布图片保留颜色与透明度

当前发布使用彩色 WebP 原生瓦片和无损搜索索引，保持原分辨率、标定与网格尺寸。
主图保留 BGR；楼层保留原 alpha，匹配时只将 alpha=0 的 BGR 置零，合成当前层与
压暗主图时使用完整透明度，包括不透明黑色与半透明像素。

当前定位器使用彩色 ZNCC。将灰度图片扩为三个相同通道会丢失地图色度信息，不能认为
与彩色地图等价；旧灰度体积试验没有验证当前评分与 0.45 接受阈值下的兼容性，不能作为
发布转换规范。发布端必须逐块核对匹配 BGR、楼层 alpha，以及完整粗搜和排序索引。

v3 的 `roi` 仅描述非零匹配 BGR，不是楼层覆盖范围。`full` 瓦片的 alpha 从完整载荷读取，
即使 ROI 外的 BGR 为零也不能裁掉覆盖。完全黑色但仍有 alpha 的楼层块保留完整载荷，
允许 `roi=[0,0,0,0]`；楼层 `zero` 只能表示全透明块。`roi` 载荷没有独立透明通道，
它只适用于 ROI 内全不透明、外部全透明的情况；正式导出对非空楼层保留 `full`。

主图在磁盘上的 tier 为 `""`，编辑器 API 的 `__main__` 仅为接口表示。
`zoom` 是资源选择档次，`cell_size` 是世界单位/格，二者不能互换。
当前视觉定位入口固定使用基础图 zoom `4`；新增其他 zoom 不代表运行端会自动选用。

## 身份和坐标

区分四类信息：地图 `map_name`、场景 `level_id`、层 `tier`、世界坐标系
`coordinate_space`。标注名称、显示名称、图像尺寸和文件更新时间都不能充当稳定身份。

- 同一大地图内共享世界坐标的普通区域使用同一坐标系，不因扩区域改 ID。
- 独立场景按真实 `level_id` 分配独立坐标系；归档在 map01/map02 下不说明可以合并坐标。
- 没有证据证明同坐标系时保持独立；现有视觉消费端不把 `independent` 层当普通大地图层匹配。
- Y 缺失应保留未知。二维网格 `origin[1]=0` 不能当作实测高度，也不能用于猜楼层。

网格以世界 X/Z 为准，数组为 `cells[i,j]`，i 沿正 Z、j 沿正 X：

```text
x_min = origin[0] + j * cell_size
z_min = origin[2] + i * cell_size
格中心 = (x_min + cell_size/2, z_min + cell_size/2)
extent = [origin.x, origin.z, origin.x + W*cell_size, origin.z + H*cell_size]
```

地图标定精度与网格分辨率分别记录。底图更清晰不意味着必须缩小 `cell_size`。
网格与底图的原点也无需相同，它们通过世界坐标相互关联。

## 扩展游戏区域

旧地形世界坐标、朝向和单位不变时，保留原有坐标系身份，并遵守以下规则。

1. **扩网格**：正 X/Z 方向追加行列；负 X/Z 方向增加行列时同步移动 origin。
   平移必须为整数格，旧 cells 原样搬移，新增格子为 0 未知。
   不能把放大的数组直接拉伸，也不能把新区域默认标可走。
2. **扩底图**：旧内容像素位置不变时保留矩阵并更新尺寸；旧内容发生平移、缩放或旋转时
   必须同时更新 matrix、inverse_matrix、image_size 及控制点像素位置。
   分层图同步更新 catalog 中对应尺寸与 bounds。
3. **保留世界标注**：`floor_regions`、links 端点与目标点已经是世界坐标，不能跟着图片
   像素平移。只有真实世界坐标定义改变时才作显式迁移。
4. **对照旧区域**：抽查分散的旧控制点、旧格中心和连接端点；扩图本身不应使这些点漂移。
   同时检查新区域接缝、未知区域和可走连通性。格式通过不能代替定位精度验证。

例如向西加 7 列、向南加 11 行：

```text
new_origin.x = old_origin.x - 7 * cell_size
new_origin.z = old_origin.z - 11 * cell_size
new_cells[i+11, j+7] = old_cells[i,j]
```

底图左边加 L 像素、上边加 T 像素，旧内容不缩放时：

```text
p_old = p_new - [L,T]
M_new[:,0:2] = M_old[:,0:2]
M_new[:,2] = M_old[:,2] - M_old[:,0:2] @ [L,T]
```

不要把底图的像素 Y 方向与网格正 Z 方向混为一谈；PNG 像素行向下增加，分层 bounds
的 Z 则由 north 向 south 递减。统一使用各自的标定公式。

## 上传哪些文件

在新版 nav-grid-editor 顶部点击 **导出数据包**，在侧栏填写一个新目录或空目录，再点击
**一键导出 nav-grid-data**。路径留空会在数据根目录 `exports/` 下创建带时间和唯一后缀的
目录。先保存网格编辑；有未保存修改时前端会提示保存。

导出后台任务会冻结源文件、生成灰度图片和清单，并校验源文件在导出期间没有变化，
完成后显示目标路径及图片/网格数量。目标目录中已有内容时拒绝覆盖；导出失败不交付半包。
导出目录中的 dataset、定位子清单、schema、README 和 LFS 配置一起保留。
该功能不执行 Git 提交或推送；输出目录可作为后续提交的数据快照来源。

| 修改内容 | 需要一起提交的内容 |
|---|---|
| 只画通行/阻挡 | 变化的 `.grid.npz` 与统一清单；声明或连接变化时追加 levels/links |
| 只改高度区间 | 保存了 `floor_regions` 的对应 `.grid.npz` 与统一清单 |
| 主图扩区域 | PNG、mapping 成对更新；变化的网格；定位 manifest、统一清单；必要的 levels/links |
| 新增或扩展分层 | 分层 PNG、catalog；必要的 levels、网格和 links；定位 manifest、统一清单 |
| 只改跨层连接 | links 与统一清单；端点所需的网格必须已在目标快照中存在 |

若新底图由独立流程完成而网格尚未标注，允许先发布定位资源，但必须明确无导航覆盖。
网格也可以独立服务抓包定位；纯网格上传不强制携带未变化的 PNG。

Git 上传仅提交发生变化的文件；每个发布版本仍描述一份完整资源快照。
ZIP 则应从已经拉取 LFS 实体的干净快照导出完整包。暂不引入手工差分包，避免遗漏依赖
或不知道接收端基线。用户不应手工维护 SHA-256 或改写 NPZ 内部数组。

PNG 与 NPZ 沿用 Git LFS。根 `.gitattributes` 当前默认为所有文件启用 LFS，新增的
dataset、schema 和文档必须显式豁免；JSON 清单宜作为普通文本提交，便于审查。
不能把 LFS 指针文本打包成实际资源，也不能假定 GitHub 网页单文件上传会完成 LFS 流程。

## 获取与校验

在 ok-end-field 根目录使用仓库的 `use-local-venv` 工作流：

```powershell
git submodule update --init -- assets/nav-grid-data
git -C assets/nav-grid-data lfs pull
uv run --locked python -X utf8 scripts/nav/verify_grid.py assets/nav-grid-data
uv run --locked python -X utf8 assets/nav-grid-data/scripts/verify_localization.py --root assets/nav-grid-data/localization
```

还需要检查层身份、连接端点和实际覆盖；当前这两个工具不等于统一清单校验器。
新版导出器在图片转码前严格检查 NPZ 的 `floor_regions`，包括逐格子区域、有限数、
区间顺序和数量上限；坏元数据会使导出失败，不能静默删除区间后把原文件当合格包发布。
存在未知格或多个可走连通块可以是合法数据，校验警告不应通过扩大可走区消除。
层声明存在、PNG 存在、网格存在、实际路线可达是四个不同结论。

ok-end-field 子模块固定到具体提交，数据仓上传成功后仍需主项目更新子模块指针并发布。
运行时不自动拉取数据仓最新 master；AnyLabeling 模板仓也不负责自动同步导航资源。

## 本轮核查

基于数据仓提交 `a78e109`，现有 5 份网格全部通过独立格式校验；3 张基础图、69 张分层图
及标定/目录共 76 个定位文件通过字节数、SHA-256 与资源引用校验，总计 50,140,814 字节。
保留 `scene_map01_lv007_layer_tips_6` 的缺失声明，不把尚缺资源当作完整覆盖。

使用现有 map02 数据验证了上述网格扩边与底图加边公式，抽查世界坐标保持不变。
这些检查验证格式兼容与换算；未用新区域游戏截图验证定位精度或进行新区域实机导航。

新版编辑器的真实导出另外通过独立消费端验收：72 张 PNG、6 份当前网格、72 组资源关联，
展开 85 项 payload。新增的第 6 份网格来自编辑器当前未提交的标注，不与 a78e109 的
5 份发布基线混为同一快照。OpenCV 5 导出与消费端 OpenCV 4 的灰度逐像素一致，
cells 与 meta 均保持源网格内容；两份清单的摘要、实际 schema 与资源引用校验通过。

编辑器最终全量回归 237 项中 236 通过、1 失败、0 跳过；唯一失败为既有本地 map02
网格分块数 14 与测试固定期望 12 不同。新增导出专项 17 项全通过，相关回归 58 项通过；
安装包检查确认包含最新导出服务、共享高度校验、schema 和前端资源。
