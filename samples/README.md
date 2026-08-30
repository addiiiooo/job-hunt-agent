# Samples

| File | What it is | Regenerate with |
|---|---|---|
| `demo.gif` | A recorded browser session: paste a job description, submit, scroll through the report, bullets and trace. | `python scripts/record_demo.py` |
| `scripted-run.html` | The result page as a standalone file, CSS inlined. | `python scripts/render_sample.py` |
| `live-run.html` | The same page from a **live** model run. Produced only when an API key is present. | `python scripts/smoke_live.py --html` |

`demo.gif` and `scripted-run.html` are produced with `LLM_CLIENT=scripted`:
retrieval, scoring assembly, the grounding validator, the trace and the metrics
all run for real, and only the model's replies are canned. Both carry the demo
banner the app renders in that mode.

Regenerate all of them after changing the corpus — the samples are only as
interesting as the documents behind them.
