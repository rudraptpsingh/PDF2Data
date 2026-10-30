"""Generate the complete PDF2Data command reference from argparse definitions."""
from pathlib import Path
import argparse,json
from local_vector_batch.cli import parser
p=parser();sub=next(a for a in p._actions if isinstance(a,argparse._SubParsersAction))
helps={a.dest:a.help for a in sub._choices_actions}
lines=['# PDF2Data CLI reference','','Generated from the actual command parser. All commands use `pdf2data`; `local-vector-batch` remains a compatibility alias.','','## Run from this checkout','','```bash','cd /Users/rp/.codex/worktrees/legal-l1-build/PDF2Data','export PDF2DATA_PYTHON=/Users/rp/Documents/GitHub/local-vector-batch/.venv/bin/python','./pdf2data --help','```','','After installing the package, use `pdf2data` without `./`. For Qwen use the MLX-VLM environment shown below. No database or Cloudflare operation is performed by the examples in this document.','','## Every exposed command','','| Command | Purpose |','|---|---|']
for name in sub.choices:lines.append(f'| `pdf2data {name}` | {helps.get(name, "")} |')
lines+=['','## Exact flags and runnable command templates','','Angle-bracket arguments below are placeholders. Use real source paths and new output directories. Optional model-dependent commands require their documented dependencies; listing a command does not mean it was executed in this run.']
for name,cmd in sub.choices.items():
 args=['pdf2data',name]
 for a in cmd._actions:
  if not a.required:continue
  args.append(a.option_strings[0])
  if a.nargs!=0:args.append(str(a.choices[0]) if a.choices else f'<{a.dest.replace("_","-")}>')
 lines+=['',f'### {name}','',helps.get(name,''),'','```bash',' '.join(args),'```','','```text',cmd.format_help().rstrip(),'```']
lines+=['','## Real local execution outputs','','These are outputs captured from this worktree, not proposed schemas. The validation snapshot may be partial while the model run is active.']
for file in ['gpu-doctor.json','verify-l1.json','qwen-validation-progress.json','qwen-reproof.json']:
 path=Path('docs/cli-examples')/file
 if path.exists():lines+=['',f'### {file}','','```json',path.read_text().strip(),'```']
lines+=['','## Current offline Qwen repair run','','```bash','export PDF2DATA_PYTHON=/Users/rp/Documents/GitHub/local-vector-batch/.venv/bin/python','export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1','./pdf2data l1-qwen-prepare --packages out/legal-l1-locally-validated/documents --output out/pdf2data-qwen-repair','PDF2DATA_PYTHON=/Users/rp/Documents/GitHub/local-vector-batch/.venv-mlx-vlm/bin/python ./pdf2data l1-qwen-run --device gpu --cases out/pdf2data-qwen-repair/cases.json --output out/pdf2data-qwen-repair/readings.json --resume --json','./pdf2data l1-qwen-validate --cases out/pdf2data-qwen-repair/cases.json --readings out/pdf2data-qwen-repair/readings.json --output out/pdf2data-qwen-repair/validation.json --json','./pdf2data l1-qwen-reproof --cases out/pdf2data-qwen-repair/cases.json --readings out/pdf2data-qwen-repair/readings.json --packages out/legal-l1-locally-validated/documents --output out/pdf2data-qwen-reproved --json','```','','The preparation command was already run; use a new workspace for another preparation. Resume only applies to the same cases, model, prompt and token contract. Reproof requires complete readings and a new output directory. It rerenders the original inputs, retains source text, uses only literal-agreement readings as additional witnesses, and reruns the original quality gates. It cannot clear unproven table structure or missing source ink merely because a model agrees. Publication/legal approval remains separate.','','## Output contracts for the next stage','','- `legal-l1`: captured original PDF, source receipt, extraction pages/tables, raw layout/glyphs, structured exact-span candidates, quality, readiness and repair queue.','- `l1-qwen-prepare`: 32 original-page rasters and hash-bound cases for this pilot.','- `l1-qwen-run`: pinned model/runtime, input hashes, literal readings, timings and token-cap state; resumable per-page checkpoints.','- `l1-qwen-validate`: missing/incomplete readings, exact and critical-token comparisons, source differences; no promotion.','- `l1-qwen-reproof`: new immutable package versions and a receipt reporting actual accepted/repair-required counts.','- DB consumers must check source quality and separate legal/publication gates. Text extraction alone does not establish applicability, legal effect or currentness.','']
Path('docs/cli-reference.md').write_text('\n'.join(lines))
Path('docs/cli-examples/commands.json').write_text(json.dumps({'tool':'PDF2Data','commands':[{'name':n,'purpose':helps.get(n,'')} for n in sub.choices]},indent=2)+'\n')
print(f'Generated reference for {len(sub.choices)} commands')
