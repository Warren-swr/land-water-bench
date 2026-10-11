# 两项任务的统一展示

同一个页面以“分类 + 高程”的联合准确率为主榜，同时提供旧的独立分类任务准确率。联合任务内部的分类准确率仅放在模型详情，并明确标注来源。

展示构建只读取已冻结结果，不调用模型、不修改抽取器，也不改变历史分数。它核验输入哈希、全部坐标、面积权重和分类标签，默认按完整模型名称连接结果，并应用明确确认的名称映射。用户已确认 Seed 2.1 的 0915 和 0902 是同一模型，因此统一为 Seed 2.1 一行：联合任务使用 0915，独立分类任务使用 0902。每个任务的 `source_model`、原始响应文件名及哈希分别保留，不合并分数或覆盖原始记录。

```bash
# 默认使用仓库自带的 results/2026-10-11 两项结果。
lwb verify
lwb build
lwb serve --directory dist --port 8732

# 也可显式组合两份已封存的结果包。
python -m land_water_bench.unified \
  --terrain results/2026-10-11/terrain \
  --classification results/2026-10-11/classification \
  --output dist-unified
```

`--terrain` 需指向完整且已封存的 `terrain_global_collection` 导出目录。`--classification` 使用公开分类结果包。输出目录必须不存在，构建会生成两任务分开的指标和响应文件、来源哈希、报告、验证回执及文件校验清单。GitHub Pages 等静态托管可直接使用该目录。

主榜保留联合 ±500 m、高程 MAE、独立分类准确率、联合任务覆盖率。选中模型后可查看更严格和更宽松的容差、联合任务内部分类指标、无效/未决数、全球高程、散点、各高程档，以及同一坐标在两次任务中的原始回答。

```bash
python scripts/check_unified_browser.py \
  --url http://127.0.0.1:8734/ \
  --output runs/unified-browser-check
```

浏览器检查逐行对照两任务指标，检查 Seed 名称合并与原始记录对应、模型切换、原始回答、参考掩码、地球旋转、窄屏布局和私有路径隔离。

公式大括号、重复答案、明确更正、解释中的类别词及内容过滤的修订使用独立的[离线重算工具](REEXTRACTION.md)。统一展示会显示来源采用的抽取版本与修订状态。仅合并旧结果不会更改其历史分数。
