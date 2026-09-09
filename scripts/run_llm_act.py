"""Edit the constants below, then run this file for an all-LLM act test.

Usage from the repository root::

    python scripts/run_llm_act.py
"""

from pathlib import Path

from live_selftest import main as run_live_selftest


# ---- Test selection (the only values normally edited) ---------------------
ACT_YAML = Path("scenarios/floodgate_dispatch.yaml")
MODEL = "pro"  # "flash" or "pro"

# Optional run settings.  None uses the act's max_rounds.
ROUNDS: int | None = None
RUN_CONFIG = Path("configs/llm.deepseek.yaml")
RUNS_DIR = Path("runs")


def main() -> int:
    if MODEL not in {"flash", "pro"}:
        raise ValueError('MODEL must be "flash" or "pro"')
    arguments = [
        "--scenario", str(ACT_YAML),
        "--run-config", str(RUN_CONFIG),
        "--profile", MODEL,
        "--runs-dir", str(RUNS_DIR / MODEL),
    ]
    if ROUNDS is not None:
        arguments.extend(("--rounds", str(ROUNDS)))
    return run_live_selftest(arguments)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Match live_selftest: do not print provider bodies or credentials.
        print(f"真实 API 验收未完成：{type(exc).__name__}")
        raise SystemExit(1) from None
