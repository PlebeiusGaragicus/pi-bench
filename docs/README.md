# pi-bench Docs

These docs define the internal contracts for `pi-bench` as a controlled benchmarking system.

- `architecture.md`: repository components, config contract, matrix expansion, and high-level data flow.
- `model-inputs.md`: exact answer and judge model input contracts, including system/user prompt rules.
- `runner.md`: detailed `runner.py` behavior, phases, resume semantics, and failure handling.
- `artifacts.md`: run artifact layout and audit checklist.

The most important control rule is: every benchmark model call must use explicit, auditable model inputs. In practice, generated `args.json` files should show `--system-prompt`, not `--append-system-prompt`.
