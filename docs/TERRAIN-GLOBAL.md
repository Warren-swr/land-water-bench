# Global Terrain / Elevation

输入经纬度，输出 `type: Solid | Water` 和 `elevation_m`。全球版对原始 2° 网格的 **全部 16,200 个坐标** 发起请求，保留独立的固定参考、原始响应、代码快照、检查点和结果页。

## 范围与定义

- 普通陆地：Solid，裸地高程；接地冰：Solid，冰面高程。
- 海洋：Water，海床高程；湖泊和浮冰架也按 Water 定义，要求水底高程。
- 不提供 Agent、工具、地图、额外系统提示词或历史对话。
- 每条请求使用相同的冻结题目模板。可解释正文中的单个完整答案 JSON；格式无效、输出截断仍计错。

分类参考沿用 GSHHG 2.3.7。高程使用 NOAA ETOPO 2022 的 15 角秒 ice-surface 产品与 EGM2008 基准。每个整数经纬度位于四个像元中心之间，以四个原始值的双线性均值作为高程参考。原始来源 URL、CSV、坐标注册、哈希和验证记录均保存。

2026-10-10 参考共有 15,928 个高程可评分点和 272 个高程不可评分点。湖泊、浮冰架、嵌套岛屿、已知分类参考冲突以及海洋高程异常使用统一的、在调用模型前冻结的高程掩码。所有这些坐标仍被请求并参与分类评分。参考掩码与每个模型的 API 失败分别统计。

参考定义对应固定栅格，并非没有误差的物理真值。相邻像元的高程范围不能当作测量置信区间。100 m 容差只表示相对参考栅格的误差。

## 指标

主指标为高程参考有效区域内、按 `cos(latitude)` 加权的联合准确率，容差固定为 500 m。成功需要同时满足类型正确与绝对高程误差不超过容差。另保存 100/1,000 m、未加权准确率、分类准确率、加权及未加权 MAE、中位误差、P90、各类型和高程档结果。

- 完整但无效的回答在准确率分母中计错。
- 未决 API 请求按已有实验规则从该模型分子和分母剔除，数量与覆盖率保留。
- Pending 请求禁止最终成绩发布。
- MAE 只计算可解析数值且高程参考有效的回答，包含类型错误的回答，须与数值覆盖率一起解读。
- 完整全球网格与 512 题均衡试验的分布及权重不同，两者分数不直接比较。

## 运行与展示

仓库自带 `src/land_water_bench/data/terrain-global-dataset.json`，包含全部 16,200 条冻结输入、高程、掩码和原始像元值。默认运行不需要下载地理数据，也不依赖任何外部工作区。

```bash
# 先验证自己的 API 配置。
lwb run-terrain --config configs/local.yaml --models responses-model \
  --run-dir runs/terrain-smoke --limit 8 --concurrency 2 --max-retries 3

# 一模型一目录；多个模型可各自运行，保留独立来源。
lwb run-terrain --config configs/local.yaml --models responses-model \
  --run-dir runs/terrain-full --concurrency 512 --max-retries 1000 \
  --timeout 300 --max-timeout-retries 2

# 全量完成后，校验并导出自己的模型展示页面。
lwb export-terrain-run --run-dir runs/terrain-full --output dist-my-terrain
lwb serve --directory dist-my-terrain --port 8733
```

公开配置格式见 `configs/models.example.yaml`。连接地址、密钥和可用模型 ID 由使用者提供。默认输出上限 65,536 tokens；接口须支持相应参数。读取超时最多重试两次，原样恢复会保留累计次数。普通错答、格式无效、截断不会因为得分而自动重跑。小规模 smoke 不允许导出为完整榜单。

已有 2026-10-11 公开结果包含 12 个模型、每个 16,200 点。其后续恢复为部分未决请求增加了额度，最多累计尝试八次；模型请求、题目和原先有效回答保留。旧批次不可原地覆盖。[恢复协议](RECOVERY.md)。

## 参考重建与批量集合

需要重新下载并构建参考时，使用独立目录；原冻结输入始终保留。

```bash
python -m land_water_bench.terrain_global_reference \
  --output runs/global-reference-new \
  --classification-details src/land_water_bench/data/reference.details.json
```

参考生成器下载 180 个原始 CSV 切片；下载 URL、哈希与四像元插值证据保存在该目录。重新下载需要网络。显示当前结果和再次核分不需要运行此步骤。

高级批量展示使用 `terrain_global_collection`，输入 `preflight/inventory.json` 与 `launches.json`。它在运行锁释放后检查完整性、实际请求、工具缺失、抽取规则与分数，再发布最终成绩；原始队列顺序与独立浮点重算分别校验。简单单模型运行直接使用上面的 `export-terrain-run` 即可。

[统一两任务展示](UNIFIED-LEADERBOARD.md)使用两份已封存结果。主榜展示联合任务，独立分类指标保留单独来源。模型名为网关实验别名，不是提供方身份认证；不同推理预算和单次响应仍限制排名解释。
