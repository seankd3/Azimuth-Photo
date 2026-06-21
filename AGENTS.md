Use `/home/sean/Projects/mobile-app-toolkit/bin/mobile-app photo-archive ...`
for shared Omarchy/Tailscale/Android phone work. Keep photoArchive-specific app
logic in the existing `scripts/photoarchive-*` commands, and put cross-project
phone/server coordination in the shared toolkit instead of duplicating it here.

Useful commands:

```bash
/home/sean/Projects/mobile-app-toolkit/bin/mobile-app photo-archive url
/home/sean/Projects/mobile-app-toolkit/bin/mobile-app photo-archive server start
/home/sean/Projects/mobile-app-toolkit/bin/mobile-app photo-archive android build
/home/sean/Projects/mobile-app-toolkit/bin/mobile-app photo-archive android install
/home/sean/Projects/mobile-app-toolkit/bin/mobile-app photo-archive android e2e
```

For phone testing, never use `localhost`; use the Tailscale URL printed by the
shared toolkit.
