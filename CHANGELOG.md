# Changelog

## v1.1.0 — 2026-11-26

### Added
- `pre-restart`: a safety copy of Herdr's saved layout (`session.json`) before a computer
  restart or Herdr shutdown. Writes a 0600, sha256-verified copy to
  `<session dir>/pre-restart-backups/`, keeps the newest 10, and warns about agents
  mid-turn, disabled agent resume, or an old Claude integration. Needs no verified Herdr
  context and never stops Herdr.
- `scripts/grid.py`: a standard-library box-drawing table renderer for multi-session reports.

## v1.0.0 — 2026-11-24

- Initial public release.
