# ADR 0099 — One install via `lights-out-gates`: ship the POSIX `lights-out` wrapper beside `lights-out-gates` in one wheel so `uv tool install lights-out-gates` puts both on PATH

- Status: proposed
- Date: 2026-11-23
- Milestone: M69
- Amends: ADR 0061 (amendment of 2026-11-09 §2: the package ships the POSIX `lights-out` wrapper alongside `lights-out-gates` via `script-files`); ADR 0045 (§2, §7: wrapper installation gains `uv tool install lights-out-gates` beside raw-URL `curl`, and the wrapper accepts `--version`); ADR 0075 (§1, §4: `package.yml`'s `dist` job verifies `lights-out` inside and from the wheel, and release notes carry the single install command)

## Context

Today installing lights-out without cloning the repository takes two paths: a raw-URL `curl` of `lights-out` into `~/.local/bin` for the container wrapper (ADR 0045), and `uv tool install lights-out-gates` for the containerless light tier (ADR 0061).

Re-checked on 2026-11-23 before choosing a package name:

- **PyPI name check.** `https://pypi.org/pypi/lights-out/json` returns HTTP 404, while `https://pypi.org/pypi/lightsout/json` returns HTTP 200 (`lightsout 1.2.0`). Under PEP 503 name normalization (collapsing runs of `-`, `_`, `.`), `lights-out` collides with `lightsout` — the exact collision that caused PyPI to reject `lights-out` with HTTP 400 on 2026-11-09 (ADR 0061, amendment). (`docs/PLAN.md` noted the 404 on `lights-out/json` without checking the separator-collapsed `lightsout/json`.)
- **Owner decision (2026-11-23).** Reuse the owned PyPI distribution `lights-out-gates`, which already has Trusted Publishing configured in `.github/workflows/package.yml` (ADR 0075 §2), to ship both `lights-out` and `lights-out-gates` in one install (`uv tool install lights-out-gates`).
- **Verified in `workbench` (`localhost/lights-out-workbench:4150781479`, `uv 0.12.13`, Python 3.13.5).** Adding `script-files = ["lights-out"]` under `[tool.setuptools]` in `pyproject.toml` puts the exact root POSIX `#!/bin/sh` wrapper into both the sdist (`lights_out_gates-<v>/lights-out`) and the wheel (`lights_out_gates-<v>.data/scripts/lights-out`), byte-identical to `./lights-out`. `uv tool install --from "$whl" lights-out-gates` installs two executables (`lights-out` and `lights-out-gates`) into the tool bin directory with the installed `lights-out` byte-identical to `./lights-out`, and `uv tool run --isolated --from "$whl" lights-out` executes it directly.

## Decision

1. **One package, two executables (`pyproject.toml`).** Add `script-files = ["lights-out"]` under `[tool.setuptools]`, and update `[project].description` to describe both `lights-out` (container wrapper) and `lights-out-gates` (containerless light tier). Keep `name = "lights-out-gates"`, `version = "0.8.1"` (bumped only on a release commit), `dependencies = []`, and `[project.scripts]` (`lights-out-gates = "lights_out.__main__:gates_main"`).
2. **`lights-out --version`.** The POSIX wrapper `lights-out` accepts `--version` as an alias of `version`, printing the identical `lights-out <version> (gates image ..., full image ...)` line and exiting 0 without touching any container engine. `shellcheck -s sh` remains clean, and the eight subcommands and `KEY=VALUE` allowlist remain unchanged.
3. **Self-checks with planted defects and unit tests.**
   - `ci/package.sh`: verify `lights-out --version` runs without an engine, never invokes an engine, matches `lights-out version`, and agrees with both `version=` in `lights-out` and `[project].version` in `pyproject.toml`, with planted-defect self-tests.
   - `ci/gates-light.sh`: update `pyproject_flaw` to assert `tool.setuptools.script-files == ["lights-out"]` alongside `project.scripts == {"lights-out-gates": "lights_out.__main__:gates_main"}`, with planted-defect self-tests.
   - `ci/release.sh`: update `wf.py` to verify `package.yml`'s `dist` job checks `lights_out_gates-$VERSION.data/scripts/lights-out` inside the wheel and runs both `lights-out --version` and `lights-out-gates version` on the wheel, with planted-defect self-tests.
   - Unit tests in `core/tests/test_one_install.py` (wrapper `--version`, `pyproject.toml` metadata, `uv build` sdist/wheel inspection, and `uv tool install` verification) and `ci/tests/test_one_install_workflows.py` (workflow wiring in `package.yml` and `stranger.yml`).
4. **Workflows.**
   - `.github/workflows/package.yml` (`dist` job): check that `lights_out_gates-$VERSION.data/scripts/lights-out` is inside the wheel and byte-identical to `./lights-out`, and run `uv tool run --isolated --from "$whl" lights-out --version` alongside `lights-out-gates version`, checking `lights-out $VERSION ` prefix. The `pypi` job continues to run only on `v*` tags (no upload until the next release with the owner's go).
   - `.github/workflows/stranger.yml` (`package` job): fetch `uv` at `workbench/Containerfile`'s `UV_VERSION`/`UV_SHA256_AMD64` pin via `workbench/fetch.sh`, build the wheel with `uv build --out-dir "$RUNNER_TEMP/dist"`, install with `uv tool install --from "$RUNNER_TEMP/dist"/lights_out_gates-*.whl lights-out-gates`, add `uv tool dir --bin` to `GITHUB_PATH`, verify `lights-out --version` and `lights-out-gates version`, and run `lights-out doctor`, `lights-out init`, `lights-out gates`, and `lights-out-gates --light` on a fresh copy of `workloads/minimal/`.
5. **Documentation and release notes.** Update `README.md`, `docs/use-on-your-repo.md`, `docs/tutorial.md` (Level 2 and the packaged-wrapper section), `docs/reference.md`, `site/index.html`, and `package/release-notes.sh` to show `uv tool install lights-out-gates` as the primary one-command install giving both `lights-out` and `lights-out-gates`, keeping `curl` as the documented alternative. Explicitly note that `lights-out` joins the PyPI `lights-out-gates` package at the next release with the owner's go (today PyPI `0.8.1` ships `lights-out-gates` only).

## Alternatives rejected

- **Publish a second PyPI package (`lights-out-cli` or `lights-out-harness`).** Keeps two install commands/packages, requires a separate PyPI project and Trusted Publisher setup, and splits the wrapper from the light tier that already ships from the same repository.
- **Rewrite `lights-out` in Python as a `[project.scripts]` entry point.** Breaks the zero-Python `curl` fallback and creates a second wrapper implementation that can drift from `./lights-out`. `script-files` ships the single POSIX `#!/bin/sh` script verbatim.

## Consequences

- `uv tool install lights-out-gates` becomes the one-command install for both the container wrapper and the light tier once the next `v*` release tag runs `package.yml`. Until that release, PyPI `0.8.1` serves `lights-out-gates` only; users wanting `lights-out` from `uv` today can install from the repository URL or use the `curl` alternative.
- Setuptools installs `script-files` into `<dist>.data/scripts/`, which PyPA wheel installers (`uv`, `pip`, `pipx`) place onto the environment's `bin/` directory preserving the `#!/bin/sh` shebang.
- `uv` build and install behavior relies on standard PyPA wheel `data/scripts` semantics; `ci/release.sh`, `ci/package.sh`, `core/tests/test_one_install.py`, and `stranger.yml`'s `package` job continuously verify it.

## Golden-rule check

`./go demo` is unchanged: no new stage, image, or service sits on its path. `./go ci` remains engine-free; `ci/package.sh`, `ci/gates-light.sh`, and `ci/release.sh` gain engine-free checks with planted-defect self-tests, and the `stranger` `package` job proves the real built wheel on a clean runner.
