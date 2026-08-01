# NPC 与送货点多语言对照方案

> 目标：让配置区域（角色配置区域、送货点配置区域）在 6 种语言下显示官方译名，与游戏内实际名称一致，各语言节点相互对应。

## 1. 现状

游戏是《明日方舟：终末地》（Arknights: Endfield，鹰角网络）。配置数据在 `src/data/delivery_area.py`：

| 配置区域 | 数据来源 | 示例 |
|---|---|---|
| 送货点（地点） | `delivery_locations` + `transfer_search_area` | 武陵城、试验园区 |
| 角色（目标 NPC） | `delivery_targets_by_location` | 常沄、彦宁、齐纶、于施、苏白易、普里莫（武陵城）；赵昭、裴令容、阿禾（试验园区） |
| 送货点配置键 | `_to_delivery_point_config_key` → `通向{地点}送货点` | 通向武陵城送货点、通向试验园区送货点 |

### 1.1 多语言通道

- **OCR/识别文本**：`assets/lang/world_map.json`（85 键，6 语言节点），经 `get_world_map_text`/`get_world_map_matcher` 读取（`src/data/world_map_utils.py`），`get_delivery_targets(area, lang_accessor)` 已支持传入 `lang_accessor`。
- **UI 显示**：配置键名（中文）经 gettext `og.app.tr()` 翻译，`i18n/*/LC_MESSAGES/ok.po`。
- **语言激活**：`src/data/lang/__init__.py` `ACTIVE_LOCALES_CONFIG` 目前仅激活 zh_CN/zh_TW，en_US/ja_JP/ko_KR/es_ES 节点存在但未激活。

### 1.2 现有问题

1. **NPC 名无任何语言节点**：world_map.json 中没有常沄/彦宁等条目，OCR 与本地化会 fallback 到中文。
2. **现有地点译名与官方不符**（机翻/拼音）：

| 中文 | 现有 en_US | 官方英文 |
|---|---|---|
| 武陵城 | Wulingcheng | Wuling City |
| 试验园区 | Experimental Park | Test Area |
| 武陵 | 武陵 | Wuling |
| 四号谷地 | valley four | Valley IV |
| 清波寨 | Qingbo Village | Qingbo Stockade |

日/韩/西同理（実験公園 / 실험공원 / Parque Experimental 均为非官方译法）。

## 2. 官方译名数据源

**官方站点**：`endfield.gryphline.com` 语言切换支持 **13 种**（English / 繁體中文 / 日本語 / 한국어 / Español / Português / Français / Deutsch / Русский / Italiano / Indonesia / ไทย / Tiếng Việt），URL 前缀 `/en-us`、`/zh-tw`、`/ja-jp`、`/ko-kr` 等；简中站 `endfield.hypergryph.com`。官方多语言 X 账号：`@AKEndfieldJP`、`@AKEndfieldKR`。

**官方地图 API（最强稳定源，2026-08 已实测可用）**：
- 双域名，语言由域名决定（`lang`/`language`/`locale`/`i18n_lang` query、`lang`/`x-language`/`accept-language` header、`lang`/`sk-language`/`x-language` cookie 全部实测无效，`sk-language: ja-jp` 等长代码返回空串）：
  - **国际服 `zonai.skport.com`** → 官方英文（mark/info.lang=`en`）
  - **国服 `zonai.skland.com`** → 官方简中（mark/info.lang=`zh_Hans`）
- **四语扩展（2026-08 实测突破）**：`sk-language` header 接受**短代码** `ja` / `ko` / `en` / `zh`，返回官方日/韩/英/简中数据（如 map02：`sk-language: ja`→武陵、`ko`→무릉、`zh`→空/回退 skland 域、`en`→Wuling）。实测覆盖：`ja` `ko` `en` `zh` 四码有效；`zh-tw` `de` `es` `fr` `id` `it` `pt` `ru` `th` `vi` 及所有长代码返回空串（服务端仅发布这 4 语）。**注意**：`zh` 在 skport 域返回空名，简中须走 skland 域。
- 端点：`/web/v1/game/endfield/map/{tree|catalog|mark/list|mark/info}`，公开免鉴权（`web/v1/wiki/*` 需签名 401，不在本方案范围）。
- `tree`：地图/层级/地区名对照（Wuling City=武陵城=武陵城=무릉성、Test Area=试验园区=実験区域=실험 구역、Qingbo Stockade=清波寨=清波砦=청파채、Valley IV=四号谷地=四号谷地=4번 협곡、Yinglung Pass=应龙关=応龍関=응룡 관문、Jingyu Valley=景玉谷=景玉谷=경옥 골짜기、Sword Vault Dale=藏剑谷=蔵剣谷=장검 골짜기、Marker Stone=首墩=首礎=수돈、North Wuling Exclusion Zone=北部禁区=北部封鎖区域=북쪽 금지 구역 等，含全部 subLevel 与 region 层名）；`catalog`：POI 类型名对照（Depot Node=仓储节点=保管ボックス=저장고 노드、Recycling Station=资源回收站=資源回収所=재활용센터、Zipline=滑索=ジップライン=집라인、TP Point=协议传送点=協約転送ポイント=프로토콜 전송 지점、Stock Redistribution Terminal=物资调度终端=商品取引端末=물자 관리 단말기、Energy Alluvium=能量淤积点=超域活性点=에너지 응집점 等 160 项）；`mark/list`+`mark/info`：点位坐标及类型（mark/info 的 lang 字段随 sk-language 变：en/zh_Hans/ja/ko）。
- **西语源（es_ES，2026-08 实测）**：官方地图 API 不提供 es。`github.com/Terra-Online/Atlos`（社区互动地图仓库，TypeScript）的 `talos/src/locale/data/region/es-ES.json` 提取自游戏本地化，覆盖 VL/WL/DJ 全地图与 site 级：Valle IV=四号谷地、La Base=枢纽区、Meseta de poder=供能高地、Ciudad de Wuling=武陵城、Valle de Jingyu=景玉谷、Empalizada Qingbo=清波寨、Área de pruebas=试验园区、Piedra Marcadora=首墩、Valle Ocultaespadas=藏剑谷、Cantera Aburrey=阿伯莉采石场 等（共 145/168 site，缺 应龙关 lv007 与 北部禁区 lv008 的 site 级——Atlos 全语言 region 数据均未收录这两区域）。该仓库同时含 `locale/data/game/{en-us,zh-cn,ja-JP,ko-KR,es-ES}.json`（markerType 全部 POI 分类多语言）与 `locale/data/ui/*`（13 语 UI 文案）。
- 前端参考：`game.skport.com/map/endfield`（webpack `main.aaa4ad7b.js` → chunk `871.e75ec14f.js`，确认域名由 `L` 标志切换；语言选择 UI 支持 13 种但 API 数据仅 英/简中/日/韩 四语）。
- 其它可编程源：`endfield.wiki.gg/api.php`、`endfield.fandom.com/api.php`（MediaWiki API，可查 Category:NPCs）。
- 社区逆向文档：`github.com/AixLnyt/skport-api-docs`（Skport/Gryphline API 端点汇总，含签名算法；游戏数据 API 需 cred/salt 签名，不在本方案范围）。`daydreamer-json/ak-endfield-api-archive`（官方 CDN 清单存档，`archive` 分支 `output/`，包体在外部下载库，过大不采用）。
- **wiki.gg NPC 源实测（2026-08）**：`api.php?action=query&list=categorymembers&cmtitle=Category:NPCs&cmlimit=500` 返回 176 个 NPC 标题，10 个送货 NPC 全部命中（Chang Yun、Yan Ning、Qi Lun、Yu Shi、Su Baiyi、Primo Linde、Zhao Zhao、Pei Lingrong、Ah He、Ruan Yi），en_US 列以此为准。物品名规律：`opensearch` 可查电池类（中容谷地电池=LC Valley Battery、低容=SC、高容=HC、武陵电池=Wuling Battery）。**注意**：该分类混入明日方舟本体 NPC（Abner、Ace、Andoain 等），筛选时须排除。

| 语言 | 主要源 | 备选源 |
|---|---|---|
| en_US | **官方地图 API（zonai.skport.com + `sk-language: en`，免鉴权）**、endfield.wiki.gg（NPC 最全）、endfield.fandom.com（按地区+职责） | 官网 /en-us、endfieldhub.org、Game8 EN |
| ja_JP | **官方地图 API（zonai.skport.com + `sk-language: ja`）** | 官网 /ja-jp、appmedia.jp/arknights_endfield、game8.jp/arknights-endfield、gamewith.jp/akendfield、gameranbu.jp/endfield |
| ko_KR | **官方地图 API（zonai.skport.com + `sk-language: ko`）** | 官网 /ko-kr、namu.wiki/명일방주: 엔드필드、arca.live akendfield 频道、game.naver.com 专区 |
| es_ES | **Atlos 仓库 `locale/data/region/es-ES.json`（游戏本地化提取，GitHub raw 免鉴权）** | 官网 Español 版、guslok.com（Ciudad de Wuling 攻略）、vortexgaming.io/es、evelongames.com |

### 2.1 已核实对照表（中 → 英/日/韩/西）

> 「待核实」= 该语言尚无官方来源证实（日/韩地名与 POI 类型已全部官方核实；es 地名除应龙关/北部禁区 site 级外已核实；NPC 名日/韩/西仍待核实），落地时节点留空回退 zh_CN（见 §3）。

| 中文 | en_US（官方） | ja_JP（官方 API） | ko_KR（官方 API） | es_ES（Atlos） | 来源 |
|---|---|---|---|---|---|
| 武陵 | Wuling | 武陵 | 무릉 | Wuling | 官方地图 API（`sk-language` ja/ko） |
| 武陵城 | Wuling City | 武陵城 | 무릉성 | Ciudad de Wuling | 官方地图 API、Atlos region |
| 试验园区 | Test Area | 実験区域 | 실험 구역 | Área de pruebas | 官方地图 API、Atlos region |
| 首墩 | Marker Stone | 首礎 | 수돈 | Piedra Marcadora | 官方地图 API、Atlos region |
| 清波寨 | Qingbo Stockade | 清波砦 | 청파채 | Empalizada Qingbo | 官方地图 API、Atlos region |
| 应龙关 | Yinglung Pass | 応龍関 | 응룡 관문 | （Atlos 缺） | 官方地图 API |
| 北部禁区 | North Wuling Exclusion Zone | 北部封鎖区域 | 북쪽 금지 구역 | （Atlos 缺） | 官方地图 API |
| 四号谷地 | Valley IV | 四号谷地 | 4번 협곡 | Valle IV | 官方地图 API、Atlos region |
| 枢纽区 | The Hub | 中枢エリア | 거점 지역 | La Base | 官方地图 API、Atlos region |
| 谷地通道 | Valley Pass | 谷地通路 | 협곡길 | Senda del valle | 官方地图 API、Atlos region |
| 阿伯莉采石场 | Aburrey Quarry | アブリー採石場 | 아부레이 채석장 | Cantera Aburrey | 官方地图 API、Atlos region |
| 源石研究园 | Originium Science Park | 源石研究パーク | 오리지늄 연구 구역 | Parque científico de originio | 官方地图 API、Atlos region |
| 矿脉源区 | Origin Lodespring | 鉱山エリア | 광맥 구역 | Veta de origen | 官方地图 API、Atlos region |
| 供能高地 | Power Plateau | エネルギー高地 | 에너지 공급 고지 | Meseta de poder | 官方地图 API、Atlos region |
| 景玉谷 | Jingyu Valley | 景玉谷 | 경옥 골짜기 | Valle de Jingyu | 官方地图 API、Atlos region |
| 藏剑谷 | Sword Vault Dale | 蔵剣谷 | 장검 골짜기 | Valle Ocultaespadas | 官方地图 API、Atlos region |
| 常沄 | Chang Yun | 待核实 | 상운（攻略站） | 待核实 | wiki.gg、arca.live zip-line 帖 |
| 彦宁 | Yan Ning | 待核实 | 언녕（攻略站） | 待核实 | wiki.gg、arca.live zip-line 帖 |
| 齐纶 | Qi Lun | 待核实 | 제륜（攻略站） | Chi-Lun | wiki.gg、arca.live zip-line 帖、vortexgaming.es |
| 于施 | Yu Shi | 待核实 | 待核实 | 待核实 | wiki.gg |
| 苏白易 | Su Baiyi | 待核实 | 소백（攻略站） | 待核实 | wiki.gg、naver lounge 帖 |
| 普里莫 | Primo Linde | 待核实 | 待核实 | 待核实 | wiki.gg |
| 赵昭 | Zhao Zhao | 待核实 | 待核实 | 待核实 | wiki.gg |
| 裴令容 | Pei Lingrong | 待核实 | 待核实 | 待核实 | wiki.gg |
| 阿禾 | Ah He | 待核实 | 待核实 | 待核实 | wiki.gg |
| 阮艺（干员） | Ruan Yi | 待核实 | 待核实 | 待核实 | wiki.gg（Ruan Yi/NPC） |
| 资源回收站 | Recycling Station | 資源回収所 | 재활용센터 | 待核实（Atlos game 缺？） | 官方地图 API（`sk-language` ja/ko） |
| 仓储节点 | Depot Node | 保管ボックス | 저장고 노드 | 待核实 | 官方地图 API（`sk-language` ja/ko） |
| 滑索 | Zipline | ジップライン | 집라인 | 待核实 | 官方地图 API（`sk-language` ja/ko） |
| 物资调度终端 | Stock Redistribution Terminal | 商品取引端末 | 물자 관리 단말기 | 待核实 | 官方地图 API（`sk-language` ja/ko） |
| 能量淤积点 | Energy Alluvium | 超域活性点 | 에너지 응집점 | 待核实 | 官方地图 API（`sk-language` ja/ko） |
| 中容谷地电池 | LC Valley Battery | 待核实 | 待核实 | 待核实 | wiki.gg（LC=Low Capacity）；同类：低容=SC Valley Battery、高容=HC Valley Battery、武陵电池=Wuling Battery |
| 协议采录桩 | Protocol Datalogger | 協約測定装置 | 프로토콜 데이터 수집기 | 待核实 | 官方地图 API（`sk-language` ja/ko） |
| 协议传送点 | TP Point | 協約転送ポイント | 프로토콜 전송 지점 | 待核实 | 官方地图 API（`sk-language` ja/ko） |
| 袭击预警终端 | EW Terminal | 襲撃警備端末 | 습격 경보 단말기 | 待核实 | 官方地图 API（`sk-language` ja/ko） |
| 危机合约 | Contingency Contract | 危機契約 | 위기 협약 | 待核实 | 官方地图 API（`sk-language` ja/ko） |
| 影拓丰碑 | Umbral Monument | 映像の記念碑 | 그림자 이정표 | 待核实 | 官方地图 API（`sk-language` ja/ko） |
| 待回收物品 | Salvageable | 未回収アイテム | 회수 대기 아이템 | 待核实 | 官方地图 API（`sk-language` ja/ko） |
| 炽燃！竞技大会！ | HEAT RAGE! MEGA ARENA! | 燃えよ！アリーナ！ | 타올라라! 경기 대회! | 待核实 | 官方地图 API（`sk-language` ja/ko） |

**备注**：
- 官方地图 API 四语数据同构（templateId/地图 ID 完全一致），仅名称语言不同（skport 域 + `sk-language` 短代码：en/ja/ko；skland 域=简中），已实测脚本化批量拉取（`/map/tree`、`/map/catalog`），产出 JSON 快照保存于 `C:\Users\26309\AppData\Local\Temp\opencode\skport\official_tree_{zh,en,ja,ko}.json`、`official_catalog_{zh,en,ja,ko}.json`、`official_five_lang.json`（含 es）。mark/list 含滑索/仓储节点/资源回收站/物资调度终端/协议传送点等 POI 坐标（templateId 已确认一致），可用于 OCR 匹配词校验。
- 日文地名多用汉字（武陵城/清波砦/景玉谷/首礎/蔵剣谷/応龍関），且官方日文与中文同名的情况不少（武陵、景玉谷、幽谷、南山 等）；韩文为纯谚文转写（무릉/무릉성/실험 구역）。
- es 地名由 Atlos 仓库（游戏本地化提取）提供，覆盖 145/168 site（应龙关 lv007 与北部禁区 lv008 的 site 级缺失，Atlos 全语言 region 数据均未收录这两区域——如需可后续用游戏客户端文本表补）；es NPC 名保留拼音转写（Chi-Lun），西语官方 NPC 译名仍待核实。
- 官方地图 API 仅发布 英/简中/日/韩 四语（`sk-language` 短代码验证），**不含 es**；`sk-language` 用长代码（ja-jp）返回空串而非报错，排查语言问题时应优先用短代码。

## 3. 各语言"对上"的规则

落地时每条翻译必须满足"各语言节点相互对应"：

1. **zh_CN**：保持现有中文（配置键名、OCR 主语言）。
2. **zh_TW**：官方繁中，用系统转换（常沄→常沄、彦宁→彥寧、齐纶→齊綸、于施→于施、苏白易→蘇白易、普里莫→普里莫、赵昭→趙昭、裴令容→裴令容、阿禾→阿禾；武陵城→武陵城、试验园区→試驗園區、资源回收站→資源回收站、仓储节点→倉儲節點）。
3. **en_US**：本方案核实填写的官方英文（上表）。
4. **ja_JP / ko_KR / es_ES**：按 §2.1 对照表填入**已多源核实的官方译名**；表中标「待核实」的节点留空（缺省），LangAccessor 自动回退 zh_CN（`_load_module` 的 fallback 链：当前 locale → zh_CN/zh_TW → 首个可用）。留空优于机翻错误译名。
5. **禁用手工翻译校验**：任何语言节点的值必须是"官方译名或空"，不允许拼音/机翻占位（现有 Wulingcheng / Experimental Park 属此类，需修正）。

## 4. 落地设计

### 4.1 world_map.json 扩展

为每个 NPC/地点新增键（键名沿用现有 hash 风格），如：

```json
"k_changyun": {
  "zh_CN": {"pattern": "常沄"},
  "zh_TW": {"pattern": "常沄"},
  "en_US": {"pattern": "Chang Yun"}
}
```

`pattern` 字段用于 OCR 匹配（`get_world_map_matcher`），`string` 字段用于显示文本——按目标用途选择。

### 4.2 配送链路适配

- `delivery_area.py` 中 `delivery_locations` / `delivery_targets_by_location` 保持中文 canonical 名（键名不变，避免触发 `config_key_migrations`）。
- 所有对外显示/OCR 处已通过 `get_delivery_locations(area, lang_accessor)` / `get_delivery_targets(area, lang_accessor)` 本地化（`delivery_area_service.py:40-69` 已支持）。
- **待办**：`DeliveryTask._configure_delivery_area` 目前调用 `get_delivery_targets(self.delivery_area)` 未传 `lang_accessor`，需补传，使 ends 列表本地化。
- `通向{地点}送货点` 配置键：键名保持中文不变（存储稳定性），显示翻译继续走 gettext ok.po；po 中已存在的「通向武陵城送货点」等 msgid 由 `scripts/sync_world_map_langs.py` 的 po 同步逻辑（§5.4）内嵌替换内旧译名片段。

### 4.3 激活语言

`ACTIVE_LOCALES_CONFIG` 保持现状（zh_CN/zh_TW），en_US 激活前需确保全部英文译名经官方核实。

## 5. 自动翻译替换方案（工程化）

### 5.1 数据层（一次性脚本）

`tools/sync_world_map_from_wiki.py`：
1. 内置中→英对照表（上表，人工核实后固化）。
2. 优先从**官方地图 API**（`zonai.skland.com`=简中 + `zonai.skport.com`=`sk-language: en|ja|ko`，`/map/tree`、`/map/catalog`）拉取地点/类型**四语**官方名作交叉校验；es 从 Atlos 仓库 `talos/src/locale/data/region/es-ES.json` 拉取；再从 endfield.wiki.gg Category:NPCs 抓取并解析 NPC 英文名（注意排除方舟本体 NPC），输出 JSON 供校验：仓库缺的、wiki 有的 → 列出待补。
3. 生成/更新 `assets/lang/world_map.json` 节点：
   - zh_CN = 中文名
   - zh_TW = 繁中转换（`zhconv` 或维护表）
   - en_US / ja_JP / ko_KR = 官方 API 四语（缺失则留空不写）；es_ES = Atlos 数据（缺失留空）
4. 校验规则（失败即报错）：
   - en_US 非空时必须是字母/空格，禁止拼音数字混写（正则 `^[A-Za-z][A-Za-z .'-]*$`）
   - 不允许 `Wulingcheng`、`Experimental Park` 类已知错误译名残留
   - 每个中文名在对照表中必须有映射，或显式标记"未核实"

### 5.2 检查机制（CI/测试）

`tests/test_world_map_i18n.py`：
- 遍历 `delivery_area.py` 全部地点/NPC 名，断言 world_map.json 中存在对应节点。
- 断言 en_US 节点值满足官方名规则（非空、无拼音混写）。
- 断言 zh_TW 节点存在。
- 断言不包含黑名单错误译名。

### 5.3 后续语言补充流程（ja/ko/es）

按 §2.1 表，**日/韩已全量官方核实**（官方地图 API `sk-language: ja|ko`，160 项 catalog + 全地图/层级/site 名）；**西语地名**已由 Atlos 提取（145/168 site，缺 应龙关/北部禁区 site 级）；其余 NPC 名（日/韩/西）待以下官方渠道齐备后逐条核实再填，遵循「官方译名或留空」原则：

- 日文：appmedia 武陵城ジップライン/NPC 相关页面、game8.jp、官网 /ja-jp 公告
- 韩文：namu.wiki 등장인물 文档（被反爬时可经搜索摘要/存档站读取）、X @AKEndfieldKR
- 西语：官网 Español 版新闻、西语社区攻略（guslok.com 等）
- es 应龙关/北部禁区 site 级：后续可从游戏客户端本地化文本表补（或查 Atlos 更新）

### 5.4 ok.po 译名同步

`scripts/sync_world_map_langs.py` 在刷新 world_map.json 与 `tools/official_five_lang.json` 快照后，还会把官方译名同步进 `i18n/{en_US,ja_JP,ko_KR,es_ES}/LC_MESSAGES/ok.po`：

1. **精确匹配**：msgid（去尾 `\n`）与官方 zh 名一致 → 整条 msgstr 替换为官方译名（ja 官方未翻译的名可能直接是中文/片假名，属官方数据）。
2. **内嵌替换**：msgid 含官方 zh 名（子串）且 msgstr 含该名旧译名 → 文本替换为新译名，如「通向试验园区送货点」的 `Test Zone` → `Test Area`、ja「武陵买入价」的 `ウーリン` → `武陵`。按 zh 名长度降序处理，避免短名子串冲突。
3. 变更后同目录 `ok.mo` 同步用 polib 重编译。
4. 幂等：官方译名已是最新时不产生任何 diff。

注意：官方 catalog 个别条目存在编号错位（如 高阶培养I ↔ II 的英文名颠倒），po 同步忠实采用官方数据；`tests/TestPoLocaleConsistency.py` 对官方各语言与英文相同的条目（如 es「武陵」= `Wuling`）有 `OFFICIAL_SAME_AS_ENGLISH` 豁免集。

## 6. 分阶段计划

| 阶段 | 内容 | 产出 |
|---|---|---|
| 1 | 固化多语言对照表 + 修正 world_map.json 现有错误译名 + 补 NPC 节点（见 §8 清单 A/C） | world_map.json 更新 |
| 2 | DeliveryTask 补传 lang_accessor，ends/地点 OCR 本地化生效；修 DeliveryTask.json / TakeDeliveryTask.json / WarehouseTransferTask.json 错译与重复键（清单 D） | 代码小改 |
| 3 | 同步脚本 + 测试文件 | tools/ + tests/ |
| 4 | zh_TW 全量核对；en_US 激活 `ACTIVE_LOCALES_CONFIG` | 语言切换可用 |
| 5 | 统一两通道译名：po（gettext）与 assets/lang JSON 同名概念保持一致（清单 B） | po 重编译 + JSON 校准 |
| 6 | 杂项：login_mixin 硬编码中文、to_model_area 中文模块名、daily_demo/daily_liaison 残缺节点（清单 E） | 代码小改 |

## 7. 相关文档

- `docs/dev/i18n_OCR配置流程.md`：语言资源两套机制说明
- `docs/dev/API.md` §530-558：语言资源 API
- `src/data/lang/__init__.py`：LangAccessor / fallback 实现

## 8. 类似数据配置盘点（2026-08 全库调查）

> 除 `delivery_area.py` + `world_map.json` 外，以下配置同样包含中文名/中文文本，涉及多语言或 OCR 匹配。状态分三级：**已本地化**（节点齐全但译名待校准）、**半本地化**（部分通路未接 lang / 缺节点）、**未本地化**（硬编码中文）。

### A. 同类的"中文地点/NPC/物品"数据源

| # | 文件 | 内容 | 状态 | 问题 |
|---|---|---|---|---|
| A1 | `assets/data/delivery_area.json`（加载器 `src/data/delivery_area.py`） | 地区/地点/NPC | 半本地化 | 10 个 NPC 无语言节点；`DeliveryTask._configure_delivery_area:92-93` 未传 lang_accessor |
| A2 | `assets/data/world_map.json`（加载器 `src/data/world_map.py`） | 地点/据点/货物/关卡/仓库映射 | 已本地化 | 主数据全名自动补 lang 节点（`sync_world_map_langs.py` sync_canon），官方有译名则填；官方新增名打印 MANUAL 提示人工决定是否入主数据 |
| A3 | `assets/data/characters.json`（加载器 `src/data/characters.py`） | 30 干员名 | 已本地化 | 官方六语已同步（endfield.wiki.gg 自动同步）；canonical `en` 为内部 ID（FeatureList 绑定），不与官方名一致属设计 |
| A4 | `src/data/zh_en.py:19-130` | 103 物品中→英 feature 名 | 未本地化（设计如此） | 仅模板匹配（WarehouseTransferTask.py:61,176），无需 lang |
| A5 | `assets/items/map/item_names.json` | 154 地图实体中文名 | 未本地化 | ItemNavigatorTask.py:22,65,365 纯中文读取 |

### B. 两通道译名不一致（po gettext vs JSON OCR）

| 概念 | po（gettext） | assets/lang JSON | 官方参考 |
|---|---|---|---|
| 仓储节点 | Storage Node | Warehousing node（DeliveryTask.json `k_a72a252f`） | **Depot Node** |
| 武陵城 | Wuling City | Wulingcheng（world_map.json `k_93e2bdac`） | **Wuling City** |
| 试验园区 | Test Zone/Test Area | Experimental Park（world_map.json `k_ba83829f`） | **Test Area** |

### C. world_map.json 内的可疑/错误译名（§2.1 表之外）

| 键 | 中文 | 现有 en_US | 官方（2026-08 官方地图 API 四语 + wiki.gg + Atlos es） |
|---|---|---|---|
| `k_214efec8` | 四号谷地 | valley four | **Valley IV**（map01），ja 四号谷地 / ko 4번 협곡 / es Valle IV |
| `k_f87e5ddb` | 中容谷地电池 | Zhongrong Valley Battery | **LC Valley Battery**（wiki.gg，LC=Low Capacity）；zh_TW 多"量"字、ja `中栄谷砲台` 错译（同类：低容谷地电池=SC Valley Battery、高容=HC Valley Battery、武陵电池=Wuling Battery） |
| `k_ece5ec1b` | 清波寨 | Qingbo Village | **Qingbo Stockade**（map02_lv003），ja 清波砦 / ko 청파채 / es Empalizada Qingbo；现有 ja `青波村` 错译 |

### D. 任务 OCR 按钮错译（assets/lang/*.json）

| 文件 | 键 | 中文 | 现有译名 | 问题 |
|---|---|---|---|---|
| DeliveryTask.json | `k_c7b4d04e` | 送达 | en `service` / ja `サービス` / ko `서비스` | 全部译成"服务"，应 delivered/納品 类 |
| DeliveryTask.json | `k_6536f6f1` | 资源 | ko `의지`（意志） | 可疑 |
| DeliveryTask.json | `k_a72a252f` / `_1` | 仓储节点 | Warehousing node | 官方 Depot Node；且存在重复键 |
| DeliveryTask.json | `k_ae8fb114` vs `k_accept_delivery` | 运送委托列表/接取运送委托 | 译名不统一 | 统一术语 |
| WarehouseTransferTask.json | （存放） | 存放 | ja `店` / ko `가게` | 商店？错译 |
| daily_routine_mixin.json | `k_3ecdd4bb` | 键领取 | — | 疑似"一键领取"漏字 |
| daily_routine_mixin.json | `k_7d394484`/`_1`、`k_39d12e73`/`_1` | 重复键 | — | 清理 |
| daily_liaison_mixin.json | — | 舰桥/信赖/存放 | — | **缺 en_US**，英文环境回退中文 |

### E. 硬编码中文 / 结构残缺（未本地化）

| 文件 | 位置 | 问题 |
|---|---|---|
| `src/tasks/mixin/login_mixin.py` | L64 `re.compile("最近")`、L72 `click_text("登录")` | 硬编码中文 |
| `src/core/base_mixin/game_flow_mixin.py:604-608`、`daily_logistics_mixin.py:57,104`、`daily_buy_mixin.py:44`、`daily_demo_mixin.py:87` | `to_model_area("物资调度"/"仓储节点"/"据点管理"/"武陵")` | 中文模块名硬编码（OCR 用 feature 识别，影响较小） |
| `assets/lang/daily_demo_mixin.json` | 结构残缺 | `double_reward` 仅中繁；`k_27d2b829` 等仅有 en_US 无中文 |
| `assets/lang/map_mixin.json`、`navigation_mixin.json` | 死资源 | 代码无引用（`k_8967d3c6` 在 daily_battle 复用） |
