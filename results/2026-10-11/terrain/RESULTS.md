# Global terrain results

Area-weighted joint accuracy requires the correct type and height within ±500 m.

| Model | Joint ±500 m | Invalid | Unresolved |
|---|---:|---:|---:|
| gpt-6-astra | 78.9855% | 14 | 0 |
| claude-opus-5-5 | 78.9841% | 0 | 0 |
| gpt-5.6-sol | 73.0216% | 0 | 0 |
| claude-sonnet-5-5 | 70.5578% | 0 | 0 |
| kimi-k3 | 69.6150% | 1 | 6 |
| seed-2.1-0915 | 68.2505% | 0 | 0 |
| gpt-5.6-terra | 63.6399% | 0 | 0 |
| mimo-v2.6-pro | 63.0168% | 11 | 717 |
| deepseek-v4.1-flash | 56.0274% | 195 | 0 |
| claude-haiku-5-5 | 53.6076% | 0 | 0 |
| step-5-preview | 52.5904% | 0 | 0 |
| qwen3.8-max | 51.6789% | 250 | 0 |

Reference coverage: 15,928 height-scorable points / 16,200 requested coordinates.
Invalid answers count as wrong; unresolved requests are excluded with coverage retained.
Saved recovery batches increased timeout attempts to eight in total; ordinary rate-limit retry budgets were retained.
