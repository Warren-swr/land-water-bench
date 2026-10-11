# 离线答案抽取修订

`terrain-answer/2.0-final-json` 与 `land-water-answer/2.2-explicit-labels` 修复正文解释造成的抽取误判。抽取只看模型输出与接口完成状态，不使用模型名、坐标或参考答案。

- 高程：跳过公式及模板占位符；相同 JSON 答案去重；识别明确标记的最终答案或更正。普通冲突答案仍无效。字段、数值和 JSON 语法要求不变，不修补英文数字、单位字符串、缺字段、重复键或不完整 JSON。
- 分类：明确的开头答案可附带提到 land / water 的解释。相反类别的普通词语、季节性海冰的不确定性不代表答案冲突；明确冲突标签或类别判断不确定仍无效。
- 内容过滤：根据接口的 `content_filter` / `DataInspectionFailed` 元数据统一记为未决，排除分子和分母，并保留覆盖率。HTTP 200 也采用相同规则。模型正文提到过滤，不会自动触发此归类。
- 截断、空正文、`pause_turn` 等不完整输出仍无效。仅使用最终正文，不从 thinking / reasoning 中补答案。

旧结果包继续使用其记录的解析版本校验。重算生成新的结果包和审计目录，保留原始正文、已有有效预测、参考数据和旧分数。两项任务使用同一套修订原则分别重新计分；主榜继续按面积加权联合 ±500 m 准确率排序。

```bash
PYTHONPATH=src python -m land_water_bench.reextract \
  --terrain /path/to/sealed-terrain-results \
  --classification /path/to/sealed-classification-results \
  --response-evidence /path/to/response-evidence.json \
  --output /path/to/new-result-release \
  --audit-dir /path/to/new-audit-directory
```

`response-evidence.json` 按任务、模型指定不完整响应所在的已封存 JSONL 文件。例如：

```json
{"terrain":{"model-a":["/path/to/sealed-run/model-a.jsonl"]},"classification":{}}
```

每个不完整响应都必须有与公开正文、请求尝试次数一致的原始接口证据，工具验证原始文件哈希后再判定是否过滤。已完成响应复用封存的正文和完成状态。未提供所需证据会中止重算。

输出含 `terrain/`、`classification/` 两份结果包，以及可静态托管的 `site/` 统一页面。审计目录保存逐模型旧分数、新分数、逐条状态变化、原始响应哈希和代码快照。重算及构建不调用模型 API。
