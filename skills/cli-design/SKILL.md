---
name: cli-design
description: Command-line programs that behave: argument parsing, --help, exit codes, stdin/stdout, no prompts in tests.
---
# Command-line design

Apply when the program is run from a terminal.

- Use the standard parser (`argparse`, `commander`, `clap`, `flag`); every option has a help string; `--help` and `--version` work.
- Subcommands for distinct actions (`app add`, `app list`); flags for options; positional arguments only for the obvious main input.
- Read from files or stdin, write results to stdout and messages to stderr, so the program can be piped.
- Exit codes: 0 success, 1 usage or input error, 2 internal failure.
- Never prompt interactively unless a `--interactive` flag is given; tests and scripts must be able to run it unattended.
- `--json` for machine-readable output when the output is data.
- Sensible defaults; config file optional; environment variables for secrets.
- Expose the logic as a function `main(argv) -> int` so tests call it without a subprocess.
