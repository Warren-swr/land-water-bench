# Land / Water / Elevation Bench

输入经纬度，预测固体或水体及高程。主榜按“分类正确且高程误差不超过 500 米”的面积加权准确率排列，同时展示独立分类任务的准确率。每点直接调用模型，不提供 Agent、工具、地图、参考答案或历史对话。

[在线排行榜](https://warren-swr.github.io/land-water-bench/) · [最新成绩](../results/2026-10-11/RESULTS.md) · [英文说明](../README.md)

仓库已包含 12 个模型、两项任务、每项 16,200 点，共 388,800 条结果，以及冻结参考、逐点回答、评分代码和页面源码。最新快照为 2026-10-11：联合任务 471 条无效、723 条未决；独立分类 40 条无效、2 条未决。

## 克隆后查看

Python 3.11 以上，支持 Linux/macOS；Windows 可使用 WSL。

```bash
git clone https://github.com/Warren-swr/land-water-bench.git
cd land-water-bench
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
lwb verify
lwb build
lwb serve --directory dist --port 8732
```

打开 <http://localhost:8732>。默认校验并展示两项最新任务；不需要 API Key、外部工作区或下载地理源文件。再次构建可使用新的 `--output` 目录。

展示统一在一个深色结果页面：排行榜（模型筛选、CSV 下载）；容差曲线（±100 m 到 ±2,000 m，每档 100 m，拖动滑块实时重排名次）；基于 three.js 的“模型脑中的地球”，每个 2° 网格一根柱子，可切换到误差尖刺模式；拖动分界线对比任意两个模型或参考的地图；滚动驱动的“海水退去”，逐层展示各深度的联合准确率；以及坐标详情和两侧原始回答。主榜继续按 ±500 m 联合准确率排序，“仅分类”单独列出。

两张地图均可选择任意模型或冻结参考，也可交换左右。分类分歧与高程差值来自联合任务的回答，按两侧有效值逐点比较；无效、未决或参考高程缺失不补零。高程差视图以“该侧 − 另一侧”着色，点开坐标显示原始差值和两侧回答。模型间差异不是相对参考的错误率；下方误差散点和分层成绩会标明当前分析的模型。

## 运行模型

复制 `configs/models.example.yaml` 为 `configs/local.yaml`，按配置中的环境变量提供完整 API 地址、密钥和实际可用模型 ID。配置支持 Responses 和 Messages；程序不自动读取 `.env`。

```bash
# 联合任务：先用 8 点检查自己的接口。
lwb run-terrain --config configs/local.yaml --models responses-model \
  --run-dir runs/terrain-smoke --limit 8 --concurrency 2 --max-retries 3

# 联合任务：全量，使用仓库自带的冻结输入。
lwb run-terrain --config configs/local.yaml --models responses-model \
  --run-dir runs/terrain-full --concurrency 512 --max-retries 1000

# 单独分类。
lwb run --config configs/local.yaml --models responses-model \
  --run-dir runs/classification-full --concurrency 512 --max-retries 1000
```

并发按 **每个模型** 计算。默认输出上限 65,536 tokens，接口需要支持该参数。联合任务首次读取时限 300 秒，读取超时后为 600 秒，默认最多重试两次；限流预算另计。原样重复命令可恢复未封存运行，累计尝试次数不会清零。普通答错和无效回答不会为了得分重采样。

[运行与导出](RUNNING.md) · [高程协议与集合展示](TERRAIN-GLOBAL.md) · [未决恢复](RECOVERY.md) · [离线抽取修订](REEXTRACTION.md)

## 评分口径

- 全球 16,200 个坐标使用纬度余弦权重；15,928 点可评分高程，其余 272 点仍参与分类。
- 无效回答计错；未决 API 请求从分子、分母剔除并展示覆盖率。内容过滤统一归入未决，包括返回 HTTP 200 的过滤。
- 已兼容正文解释、公式花括号、相同 JSON 重复与明确更正。真正的语法或字段错误、空正文、截断仍无效；不从思考字段抽答案。
- 主榜使用联合任务；“仅分类”列来自另一次独立分类任务，不能和联合任务内部的分类指标混用。
- Seed 0902 与 0915 已按明确确认合为同一模型，保留两次任务的原始来源。

参考为 GSHHG 2.3.7 与 ETOPO 2022 / EGM2008 固定数据版本，不是无误差的物理真值。模型名是实验网关别名；历史推理预算、输出上限和重试情况不同，细小分差不代表稳定能力差异。

当前结果保存在 `results/2026-10-11/`，旧分类快照保存在 `results/2026-10-10/`。结果包、参考与源码都在仓库中；私有凭据、连接和完整原始接口日志保留在实验归档，不参与公开构建。GitHub Pages 通过 Actions 从同一仓库校验、构建和部署。
