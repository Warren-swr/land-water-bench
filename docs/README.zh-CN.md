# Land / Water Bench 中文说明

输入一个经纬度，让模型只回答 Land 或 Water，再把全球 16,200 个答案拼成地图。所有请求直接调用模型 API，不提供 Agent、工具、地图、检索或历史对话。

[在线排行榜](https://warren-swr.github.io/land-water-bench/) · [完整成绩](../results/2026-10-10/RESULTS.md) · [英文说明](../README.md)

仓库包含独立运行框架、当前 12 个模型的逐点结果、冻结的 GSHHG 参考、浅色交互页面和 GitHub Pages 自动部署配置。

## 本地查看

Python 3.11 以上；运行框架支持 Linux/macOS，Windows 可用 WSL。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
lwb verify
lwb build
lwb serve --directory dist --port 8732
```

打开 <http://localhost:8732>。查看和重新核分不需要 API Key，也不会重新调用模型。再次构建时，使用新的输出目录，或仅移除上次生成的 `dist/`。

## 运行模型

复制 `configs/models.example.yaml` 为被忽略的 `configs/local.yaml`，配置完整 API 地址、密钥环境变量和实际可用模型 ID。凭据只通过环境变量读取，不写进公开配置；程序不自动加载 `.env`。支持 Responses 和 Messages 两种接口格式。

```bash
lwb run --config configs/local.yaml --models responses-model \
  --run-dir runs/smoke --limit 8 --concurrency 2 --max-retries 3
lwb run --config configs/local.yaml --models responses-model messages-model \
  --run-dir runs/full-001 --concurrency 512 --max-retries 1000
```

新框架中的并发是 **每个模型单独计算** ：两个模型各 512，合计最高 1,024。先小规模验证接口，再使用新目录正式运行。每点默认最多 1,000 次重试，恢复运行保留原重试预算。输出上限默认 65,536，能否使用取决于接口。超时会重试；普通答错和格式无效不会自动重采样。

运行完成后，可冻结、导出并展示：

```bash
lwb verify-run --run-dir runs/full-001 --seal
lwb export-run --run-dir runs/full-001 --output results/full-001
lwb build --results results/full-001 --output dist-full
```

更完整的恢复、截断重跑和追加结果说明见 [RUNNING.md](RUNNING.md)。

## 分数口径

主分数按纬度余弦加权。无效答案计错；未决 API 请求从该模型的分子和分母剔除。当前公开结果共 194,400 条，3 条未决，320 条无效。解释和明确答案可以共存，但含糊、冲突和截断仍无效。

参考使用 GSHHG 2.3.7：湖泊、漂浮冰架为 Water；陆地、湖中岛、接地冰为 Land。与 Natural Earth 的 54 个分歧点仍保留计分，详情会标注。静态参考不是绝对真值。

模型名沿用实验接口别名，没有独立认证底层模型身份，也不代表公开 API 一定提供同名模型。历史运行的推理设置、输出上限不完全一致，包含有记录的截断重跑和失败恢复；细小分差不代表稳定能力差异。

代码采用 MIT 许可证，参考数据、字体等保留各自上游许可。详情见 [第三方声明](../THIRD_PARTY_NOTICES.md)。
