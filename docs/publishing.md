# Publishing Static Galleries

photoArchive publishes website galleries by writing static files into a folder you control. It does not know about git, npm, Wrangler, Hugo, Next, nginx, or any other site toolchain.

## Where to configure

Open **System → Publishing** (from the System button, or from the Publish dialog’s setup prompt). Three settings:

- `publish_dir` (**Gallery folder**): folder where gallery bundles are written. **Empty disables publishing** and shows the in-app setup prompt on Publish.
- `publish_hook` (**Hook command**): optional shell command to run after every publish or unpublish.
- `publish_site_base_url` (**Site base URL**): optional base URL used **only to render live links in the app** — not where files are served from.

Shared (left nav) lists every private link and website gallery so you can triage expiry, picks, and hook failures without opening each collection.

## First publish

1. System → Publishing → set **Gallery folder** → Save settings.
2. Open a collection → **Publish to website** → choose title/slug → Publish.
3. photoArchive writes `publish_dir/<slug>/` and regenerates `manifest.json`.
4. Live client URL shape (when Site base URL is set): `{site_base_url}/g/<slug>/`.
5. If a hook is configured, it runs next. Publish still succeeds if the hook fails — the row shows “published locally, hook failed.”

Without Site base URL, the gallery is written locally and Shared shows **Published locally** until you add the base URL for in-app Open/Copy links.

## Folder Layout

When collection `Selected Landscapes` is published with slug `selected-landscapes`, photoArchive writes:

```text
publish_dir/
  manifest.json
  selected-landscapes/
    index.html
    thumb/
      sm/
        101.jpg
    img/
      101.jpg
```

Each gallery folder is replaced atomically: photoArchive builds into a temporary sibling folder, then swaps it into place. Unpublishing removes `publish_dir/<slug>/` and regenerates `manifest.json`.

The images are generated from photoArchive's preview cache. Originals are not copied into the publish folder. If previews are still generating, wait for cache pregeneration before expecting a complete gallery bundle.

## Manifest Contract

`publish_dir/manifest.json` is the integration point for sites that want to list all published galleries.

```json
{
  "galleries": [
    {
      "slug": "selected-landscapes",
      "title": "Selected Landscapes",
      "photo_count": 42,
      "date_range": "2026-07-01 to 2026-07-03",
      "cover": "/g/selected-landscapes/thumb/sm/101.jpg",
      "published_at": 1783123456.0
    }
  ]
}
```

Fields:

- `slug`: folder name and stable gallery URL segment (`/g/<slug>/`).
- `title`: display title.
- `photo_count`: number of images in the gallery.
- `date_range`: human-readable range derived from image dates, or empty.
- `cover`: root-relative cover image path.
- `published_at`: Unix timestamp used for newest-first sorting.

The manifest is regenerated from the publish database and gallery bundle metadata after every publish or unpublish.

## Hook Model

If `publish_hook` is set, photoArchive runs it after writing files and regenerating `manifest.json`. The command runs with `publish_dir` as its working directory and has a 15 minute timeout.

Publishing still succeeds if the hook fails. The gallery has been written locally; photoArchive records the hook exit code and the last output lines, then surfaces that state as "published locally, hook failed."

Relative hook paths such as `scripts/deploy-galleries.sh` are resolved by walking up from `publish_dir`, so a repository-level script can be used even when galleries live in `app/public/g`. Example: with `publish_dir=/site/app/public/g` and `publish_hook=scripts/deploy-galleries.sh`, photoArchive finds `/site/scripts/deploy-galleries.sh`.

## Examples

### Bare nginx Folder

Set:

```text
publish_dir=/srv/www/g
publish_hook=
publish_site_base_url=https://photos.example.com
```

Point nginx at `/srv/www` and serve `/g/<slug>/` directly. The site must route `/g/` to that folder (and may read `manifest.json` to list galleries). No site generator is required — the gallery HTML, thumbnails, previews, and manifest are already static files.

### Rsync To A VPS

Set:

```text
publish_dir=/home/sean/photoarchive-publish/g
publish_hook=rsync -az --delete ./ user@example.com:/var/www/photos/g/
publish_site_base_url=https://photos.example.com
```

photoArchive writes locally, then the hook mirrors the gallery folder to the server. **`--delete` removes remote files that are gone locally** — including galleries you unpublished. If rsync fails, the local bundle remains available and the hook output is visible in photoArchive.

### Git And Cloudflare Pages (reference install)

One real install looks like this (paths and project name are Sean’s portfolio — treat as a pattern, not a requirement):

```text
publish_dir=/home/sean/Projects/sean-kenneth-doherty/app/public/g
publish_hook=scripts/deploy-galleries.sh
publish_site_base_url=https://www.seankennethdoherty.com
```

The hook script belongs to the portfolio repo. Its pattern is:

```bash
git add app/public/g
git commit -m "Publish galleries update" # only when files changed
NEXT_PUBLIC_SITE_BASE_PATH="" npm run build # from app/
cp app/out to a temp folder and delete files over 25 MiB
npx wrangler pages deploy <temp> --project-name=seankennethdoherty --branch master
git push origin master
```

That deployment logic stays outside photoArchive, so other people can use any folder, static host, framework, or deployment provider they already trust.
