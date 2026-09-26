# Benchmark Data

The JSON files contain the raw prompt-level timing observations used by
[`docs/PERFORMANCE.md`](../docs/PERFORMANCE.md):

- `llm-baseline-24-gpu-layers.json`: initial partial CUDA offload;
- `llm-full-gpu.json`: the same workload with full CUDA offload;
- `llm-full-gpu-conversational.json`: the current conversational prompt.

Keep new runs immutable and include hardware, power mode, thermal state, model
hash, commit, warm-up count, and command. Summaries should report a distribution
or median, not only the fastest sample.
