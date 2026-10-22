# FrameDrop

Self-hosted, mobile-first uploader for an Android picture frame running
[Slideshow](https://slideshow.digital/) (`sk.mimac.slideshow`). Photos are auto-rotated,
fitted to the frame's resolution (blur-fill or crop), saved as JPEG and pushed over WebDAV.

## Install

```sh
git clone https://github.com/jerbanpls/framedrop.git
cd framedrop
cp .env.example .env    # set FRAME_URL / FRAME_PASS / resolution
docker compose up -d --build
```

Open `http://<host>:8000` on your phone and use "Add to Home Screen" to install it as an app.

On the frame: Slideshow → Settings → Web server → enable it and set a username and password.
WebDAV is served at `{FRAME_URL}/webdav/`.

To update later, run `git pull` and then `docker compose up -d --build`. Your `.env` and `./data` are kept.

If you only need album syncing with no UI, use
[immich-frame-sync](https://github.com/jerbanpls/immich-frame-sync), a headless worker.
Don't point both at the same Immich album: each uploads under its own file names, so every photo would show up twice.

## Layout

| Path | Purpose |
|---|---|
| `app/main.py` | FastAPI routes and the Immich background loop |
| `app/processor.py` | Decode (JPG/PNG/WEBP/HEIC), EXIF rotate, blur-fill / crop, JPEG + thumbnail |
| `app/frame_client.py` | WebDAV client (`PUT` / `DELETE` / `PROPFIND` / `GET`) |
| `app/library.py` | Local mirror (`/data/photos`, `/data/thumbs`) + `state.json` |
| `app/sync.py` | Upload, delete, Sync / Repair and Immich album mirroring |
| `app/immich.py` | Immich API client |
| `app/static/` | Single-page PWA (Tailwind + Alpine via CDN) |

## How sync behaves

- **Upload when the frame is offline:** the photo is kept locally and pushed on the next **Sync / Repair**.
- **Delete when the frame is offline:** it's removed locally and recorded in `state.json`. The next Sync deletes it on the frame.
- **Sync / Repair:** uploads local-only photos, imports images that exist only on the frame into the gallery, finishes pending deletes and rebuilds missing thumbnails. It never deletes photos that exist only on the frame.
- **Immich (optional):** every `SYNC_INTERVAL_MINUTES`, new photos in the album are processed and uploaded. Photos removed from the album are removed from the frame. Videos are skipped. Photos you uploaded by hand are never removed by Immich sync.

## API

`GET /api/status` · `GET /api/photos` · `POST /api/upload` (multipart `files`, `mode=blur|crop`) ·
`POST /api/delete` (`{"names": [...]}`) · `DELETE /api/photos/{name}` · `POST /api/sync` · `POST /api/immich/sync`
