# Publishing Static Galleries

photoArchive publishes website galleries by writing static files into a folder you control. It does not know about git, npm, Wrangler, Hugo, Next, nginx, or any other site toolchain.

Configure three settings in System -> Publishing:

- `publish_dir`: folder where gallery bundles are written. Empty disables publishing and shows a setup prompt.
- `publish_hook`: optional shell command to run after every publish or unpublish.
- `publish_site_base_url`: optional base URL used only to render live links in the app.

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

The images are generated from photoArchive's preview cache. Originals are not copied into the publish folder.

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

- `slug`: folder name and stable gallery URL segment.
- `title`: display title.
- `photo_count`: number of images in the gallery.
- `date_range`: human-readable range derived from image dates, or empty.
- `cover`: root-relative cover image path.
- `published_at`: Unix timestamp used for newest-first sorting.

The manifest is regenerated from the publish database and gallery bundle metadata after every publish or unpublish.

## Hook Model

If `publish_hook` is set, photoArchive runs it after writing files and regenerating `manifest.json`. The command runs with `publish_dir` as its working directory and has a 15 minute timeout.

Publishing still succeeds if the hook fails. The gallery has been written locally; photoArchive records the hook exit code and the last output lines, then surfaces that state as "published locally, hook failed."

Relative hook paths such as `scripts/deploy-galleries.sh` are resolved by walking up from `publish_dir`, so a repository-level script can be used even when galleries live in `app/public/g`.

## Examples

### Bare nginx Folder

Set:

```text
publish_dir=/srv/www/g
publish_hook=
publish_site_base_url=https://photos.example.com
```

Point nginx at `/srv/www` and serve `/g/<slug>/` directly. No site generator is required. The gallery HTML, thumbnails, previews, and manifest are already static files.

### Rsync To A VPS

Set:

```text
publish_dir=/home/sean/photoarchive-publish/g
publish_hook=rsync -az --delete ./ user@example.com:/var/www/photos/g/
publish_site_base_url=https://photos.example.com
```

photoArchive writes locally, then the hook mirrors the gallery folder to the server. If rsync fails, the local bundle remains available and the hook output is visible in photoArchive.

### Git And Cloudflare Pages

Sean's install uses:

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
