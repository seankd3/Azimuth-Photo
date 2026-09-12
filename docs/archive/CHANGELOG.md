# Changelog

> Entries below describe V1 and prerelease work. V2 has not been released.

All notable changes to Azimuth Photo are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Lightroom Classic catalog migration: picks, ratings, develop settings, and collections import from .lrcat files with live progress.
- A staged import canvas modeled on Lightroom Classic: scan, review duplicates, route by destination, and commit with provenance.
- Owner authentication with device pairing, closing all previously known remote-access criticals.
- Star ratings on the phone that sync across devices and survive alongside Lightroom-imported ratings.
- A frozen, dependency-free server build for Windows and Linux (PyInstaller onedir) that boots to the first-run wizard with zero configuration.

### Changed

- One Surface: every foreground interaction is local-first — Empty Trash, culling, and edits never wait on the network; pending work is shown honestly and reconciles in the background.
- Version stacks, exports, and deletion now treat virtual copies strictly as renditions of their master: trash moves families together, exports join the master stack, and Reset restores the imported look.

### Fixed

- An empty rescan of an online folder can no longer mark an entire library missing.
- Sync operations arriving before their dependencies (collections, uploads) retry from a durable ledger instead of being lost.
- Publishing: revoked links stop being publicly cacheable, client-gallery links can be revoked, republish is atomic, deleted galleries no longer linger on disk, and hung publish hooks are reliably killed — on every platform.
- Full Windows parity: the complete test suite runs green on Windows, covering folder scoping, hub uploads, path handling, and console encodings that previously failed only outside Linux.

## [1.0.0-rc.1] - 2026-07-12

### Added

- A self-hosted, local-first home for serious Azimuth Photos: browse, filter, cull, rank, search, and share without handing originals to a cloud service.
- A fast desktop library with a virtualized grid, real folder tree, timeline, filters, and reversible Trash.
- Refine ranking with quick Mosaic and Duel choices, so the archive learns which photographs matter most to you.
- Private share galleries, static website publishing, and safe ZIP exports for getting work to clients and collaborators.
- An installable phone app for browsing, searching, reviewing, and sharing your own library over your network.
- Optional local AI for semantic search, captions, tags, and People—useful when wanted, never required for the core archive.
