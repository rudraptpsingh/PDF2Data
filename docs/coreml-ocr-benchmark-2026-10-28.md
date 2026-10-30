# CoreML OCR benchmark — 28 October 2026

Keep the current CPU OCR workers. CoreML did not meet the required threefold
speed improvement: the same English recognition crops took 6.44 times longer,
and the same Devanagari crops took 2.67 times longer. No OCR runtime defaults,
source packages, publication policy, or running batch jobs were changed.

The test used Apple M5 Max, 36 GiB RAM, macOS 27.0.1 arm64,
RapidOCR 3.10.0, and ONNX Runtime 1.31.1. Two original PDF pages were rendered
at 120 DPI. CPU detection/classification produced fixed ordered crop lists;
the correct existing PP-OCRv5 mobile language recognizers processed identical
crops three times per provider. CPU intra/inter-op threads were both one.
The existing parallel CPU batch continued running, and the separate Qwen GPU
pilot finished before CoreML started. No model downloads occurred.

| Recognition sample | Crops | CPU median | CoreML median | CoreML cold session | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| English | 29 | 4.734 s | 30.475 s | 45.866 s | 6.44× slower |
| Devanagari | 92 | 13.511 s | 36.022 s | 9.800 s | 2.67× slower |

The CPU full-page passes, including detection/classification, took 6.743 s
and 14.311 s respectively. Exact output SHA-256, normalized ordered words,
ordered numbers, and logical operators matched the CPU output in every
CoreML run for both samples.

Configured providers were CoreML followed by CPU fallback. CoreML options
were `ModelFormat=MLProgram`, `MLComputeUnits=CPUAndGPU`,
`RequireStaticInputShapes=0`, `EnableOnSubgraphs=0`,
`SpecializationStrategy=FastPrediction`, `ProfileComputePlan=1`, and
`AllowLowPrecisionAccumulationOnGPU=0`. Persistent compiled-model caches
were isolated by the exact model SHA under
`~/Library/Caches/local-vector-batch/coreml/`.

Provider assignment was measured rather than inferred from configuration.
ONNX Runtime recorded 975 CoreML partition executions and 150 CPU fallback
events for English, and 576 CoreML partition executions and 864 CPU fallback
events for Devanagari. CoreML's hardware compute-plan logs assigned 654
operations to CPU, two to GPU, and four reported no device across the two
models. Thus a CoreML provider entry did not establish broad GPU execution.
The [official ONNX Runtime CoreML documentation](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html)
describes these provider, profiling, and persistent-cache options.

Source bindings:

- English physical page 1: PDF SHA `079d0b19812c21c23588ebc78400811e7ea9be1f42732f3a8e3f9fbb2e85611b`;
  recognizer SHA `c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8`.
- Devanagari physical page 2: PDF SHA `d707199c264db6028fdd9f2daf4e2a2788246885aaa9ee84abcbef5b409f6297`;
  recognizer SHA `d6f0a906580e3fa6b324a318718f1f31f268b6ea8ef985f91c2012a37f52c91e`.

The bounded benchmark harness is `/tmp/powerlex_coreml_benchmark.py`.
Detailed timings, full output hashes, equality checks, compressed crop inputs,
and ONNX profiles are in `/tmp/powerlex-coreml-benchmark/`; CoreML hardware
dispatch logs are `/tmp/powerlex-coreml-gpu.log`. These temporary files are
diagnostics, not source-review receipts or publication approvals.
