# nav-grid-data 数据格式

`assets/nav-grid-data` 是固定到具体提交的数据子模块。运行端使用当前目录，不自动下载或切换最新数据。网格、定位底图、楼层声明和跨层连接分别校验；资源存在不表示路线一定可达。

## 目录与消费入口

```text
nav-grid-data/
  dataset.json
  levels.json
  links.json
  <map>_<zoom>.grid.npz
  <map>__<tier>_<zoom>.grid.npz
  localization/
    manifest.json
    maps/<map>/4/tiles.json
    maps/<map>/4/<map>_4_mapping.json
    layers/catalog.json
    layers/<map>/<level>/<tier>/tiles.json
    objects/...webp
```

对象载荷的实际相对路径由清单和图像索引定义，不由运行端按示例路径猜测。

| 数据 | 当前读取代码 |
| --- | --- |
| `.grid.npz` 网格和高度区域 | `src/nav/grid_io.py` |
| 楼层声明与跨层连接 | `src/nav/layered_map.py` |
| v3 定位清单、图像索引与载荷 | `src/localization/localization_bundle.py` |
| 基础图标定和地图集合 | `src/localization/calibrated_map_store.py` |
| 原生瓦片与缩放区域 | `src/localization/native_tile_image.py` |

## 网格与世界坐标

网格的 `magic=okef-nav-grid`、`schema_version=2`；`cells` 为二维 `uint8`，0 未知、1 可走、2 阻挡；`meta` 为 JSON。当前导航将未知格视为不可通行。

数组为 `cells[i,j]`，i 沿正 Z、j 沿正 X。格中心为：

```text
x = origin.x + (j + 0.5) * cell_size
z = origin.z + (i + 0.5) * cell_size
```

`cell_size` 是世界单位/格，`zoom` 是资源选择档次，不能互换。主图网格的 tier 为空字符串，运行接口的主图身份为 `__main__`。当前视觉定位使用 zoom `4`。

地图 `map_name`、场景 `level_id`、楼层 `tier` 和世界坐标系是不同身份。独立场景即使归档在同一地图目录，也不能与普通大地图层合并竞争。名称、图片尺寸或更新时间都不构成坐标系证据。

Y 缺失表示未知；二维网格的 `origin[1]=0` 不能当作实测高度。`floor_regions` 的区间、有限数、逐格范围和数量必须有效。层声明、网格覆盖、图片覆盖和实际路线连通性分别判断。

## v3 定位包

定位清单为 `kind=okef-tiled-localization`、`schema_version=3`，绑定基础图、楼层目录、图像索引及全部对象载荷的大小与 SHA-256。运行端只读取这一格式，不在启动时重新切瓦片。

主图标定以 2×3 `matrix` 将整数像素中心映射到世界 X/Z，`inverse_matrix` 执行逆变换，`image_size` 与图像索引的逻辑尺寸一致。图像原点在左上，行向下，列向右；楼层 bounds 的 Z 从 north 向 south 递减。

基础图保存 BGR，楼层保存完整 alpha。当前定位使用彩色 ZNCC；发布时不能以灰度扩三通道替代原色度。

- `full` 瓦片使用完整载荷，包含不透明黑色与半透明像素。
- `roi` 是逻辑瓦片中的裁剪载荷，不带独立 alpha，只用于裁剪区内不透明、区外透明的情况。
- `zero` 明确表示没有非零载荷；楼层仅能用它表示全透明块。
- 搜索图使用逻辑尺寸、ROI 与零填充，恢复像素原点；BGR 的非零 ROI 不等于楼层覆盖。

发布包提供精确尺度的粗搜概览和楼层排序图，使用 `INTER_AREA`、4 匹配像素/米及 0.5 排序像素/米。定位服务归一化输入为 1280 宽，因此包内必须具备该宽度的搜索尺度。排序图缺失不能使对应楼层从完整竞争中消失。

所有路径相对包根并使用 `/`。读取器拒绝绝对路径、父目录跳转、Windows 驱动器或数据流路径、越界链接、重复资源身份、不完整瓦片覆盖和不符的摘要。缺失声明保留为缺失，不能通过删除声明制造完整覆盖。

## 数据集清单与发布

根 `dataset.json` 使用 `kind=okef-nav-dataset`、`schema_version=1`，索引坐标系、不可变发布身份、导航文件和视觉/网格关联。它通过摘要绑定 `localization/manifest.json`，不重复定义 cells、标定矩阵或楼层 bounds。

每条资源关联以 `(coordinate_space_id, map_name, tier, zoom)` 为联合身份。导航文件和定位子清单共同描述完整受管快照；删除以新快照为准，不能覆盖旧目录后把残留网格当作当前资源。

运行端按当前网格和定位目录读取，不通过 `dataset.json` 自动更新。编辑器的导出行为、权威 schema 及数据发布校验见数据仓的 `schemas/dataset.schema.json` 和 `docs/data-format.md`，本项目不重复维护发布端实现。

## 扩展标注

扩网格时按整数格移动 origin，保留已有 cells，新增格设为未知。底图内容发生平移、缩放或旋转时，同步调整标定、逻辑尺寸和控制点；世界坐标中的连接端点与高度区域不随图片像素平移。

底图向左补 L 像素、向上补 T 像素且原内容不缩放时：

```text
p_old = p_new - [L,T]
M_new[:,0:2] = M_old[:,0:2]
M_new[:,2] = M_old[:,2] - M_old[:,0:2] @ [L,T]
```

修改后同时检查旧控制点、格中心、连接端点和新区域接缝。格式校验不能代替实机定位精度与路线可达性验证。

## 获取与校验

```powershell
git submodule update --init -- assets/nav-grid-data
git -C assets/nav-grid-data lfs pull
uv run --locked python -X utf8 scripts/nav/verify_grid.py assets/nav-grid-data
uv run --locked python -X utf8 scripts/nav/verify_localization.py --root assets/nav-grid-data/localization
```

必须取得 LFS 实体，不能将指针文本当作图片发布。更改数据后由主项目更新子模块指针并发布。定位包是正式资源，不属于 `cache/localization` 的清理对象；可重建缓存的上限和删除规则见 [小地图定位](小地图定位.md#磁盘缓存)。
