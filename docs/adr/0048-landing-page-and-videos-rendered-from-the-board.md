# ADR 0048 — A landing page on GitHub Pages, with its videos rendered from the board in CI

- Status: accepted (amends ADR 0035 §6 [the explainer is also hosted on Pages, the same file, served alone]; ci/gitea-compat.sh [lifts two checks for the Pages workflow])
- Date: 2026-11-05
- Milestone: visibility plan, after v0.1.0

## Context

The first outside feedback, from a senior developer, asked for two things:
a site that explains lights-out simply, and a short video tutorial, made
from the animations the board already has. The board (`board/index.html`,
ADR 0035) plays nine scripted walks over the real code paths. It opens
without `board/serve.py`: on a 404 for `state.json` it drops LIVE and stays
an explainer (ADR 0035 §6), which is what any static host answers. The repo
is public, so GitHub Pages and Actions are free. The owner's machine runs
no container engine (proofs run on GitHub Actions), and the toolchain lives
in containers.

## Decision

1. **`site/index.html`**, one page, no build, nothing loaded from another
   origin: no font, script, tracker or analytics. Every figure on it is in
   README.md or docs/reference.md. It says which videos are scripted walks
   with example numbers, and links the full real/recorded list.
2. **The board, served as it is.** The workflow copies `board/index.html`
   to `board/` on the site. Pages answers 404 for `state.json`, so the
   board offers no live view: it never shows a plane it cannot see.
3. **Videos rendered from the board.** `site/render.py` serves the board
   alone, drives it through its own buttons with Playwright and records it
   with `recordVideo`; ffmpeg encodes a captioned overview, a hero loop
   cropped to the diagram (webm, mp4, gif) and one clip per scenario, the
   list of scenarios read from the page. Every frame carries the label
   "scripted walk, real code paths, example numbers". The overview's
   captions name the words the board says at each chapter's last step; if
   the board changes them, the render stops instead of captioning the wrong
   step. Step timing is a decision: about four and a half words a second,
   2.2 s to 5.5 s per step.
4. **`.github/workflows/site.yml`**, in
   `mcr.microsoft.com/playwright/python:v1.64.1-noble`, pinned by digest,
   with `playwright==1.64.1` from PyPI (the image has the browsers, not the
   package) and ffmpeg from the image's Ubuntu archive. `render` runs on a
   push to any branch that touches `site/`, `board/index.html`,
   `docs/assets/` or the workflow, and on dispatch; it uploads the Pages
   artifact and, for review, screenshots of the landing at desktop and
   phone width. `deploy` (`actions/deploy-pages`) runs on `main` only. The
   videos are build outputs and never enter git.
5. **Gitea compatibility.** Pages needs `environment:` and `id-token:
   write`, which `ci/gitea-compat.sh` refuses. Both checks are lifted for
   `site.yml` alone, and only while every job in it carries the github.com
   guard (ADR 0039), so the L0 Gitea runner skips the whole file.
6. **Turning Pages on is the owner's act**: Settings → Pages → Source:
   GitHub Actions.

## Alternatives rejected

- **Screen recordings made by hand.** They drift from the product the day a
  scenario or a station changes, and nobody re-records them. Rendering from
  the board in CI makes the video follow the page that `ci/board.sh` already
  keeps in step with `core/tower.sh`.
- **Remotion, or another video framework.** A new dependency and a second
  description of the pipeline to keep in step with the board. The board is
  already the animation; recording it is enough.
- **A separate repository for the site.** The site would drift from the
  board and the README it quotes, and a change to either would need a
  second pull request somewhere else.
- **A static-site generator** (Jekyll, Hugo, Astro). A toolchain and a build
  for one page, against "the smallest thing".
- **Committing the rendered videos.** Megabytes of binaries in history on
  every change to the board, and a copy that can be stale.
- **A `gh-pages` branch pushed by the workflow.** It needs `contents:
  write` and keeps every render in git history; the Pages artifact needs
  neither.

## Consequences

- A change to the board re-renders the videos on its branch, so a PR shows
  whether the walk still renders; it reaches the site when it lands on main.
- The render takes minutes of real time: each take records at playing
  speed, three at a time.
- `site/render.py` reaches into the board through its DOM ids
  (`#scenarios`, `#timeline`, `#b-next`, `#say`); renaming one breaks the
  render with a message, not the board.
- Not in `RISK_HIGH_PLATFORM`: the site decides nothing.
