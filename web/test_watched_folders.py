import os
import sqlite3
import time
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_support import *  # noqa: F401,F403
from features.develop import virtual_copies
from features.library import watched_folders, watched_routes


class WatchedFolderTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.api = FastAPI()
        watched_routes.configure(db_path=lambda: db.DB_PATH)
        self.api.include_router(watched_routes.router)

    async def asyncTearDown(self):
        watched_routes.configure(db_path=lambda: db.DB_PATH)
        await super().asyncTearDown()

    async def test_manual_scan_registers_only_new_images_and_status_is_honest(self):
        inbox = os.path.join(self.tempdir.name, 'watched')
        os.makedirs(inbox)
        first = os.path.join(inbox, 'first.jpg')
        with open(first, 'wb') as handle:
            handle.write(b'first image')

        with TestClient(self.api) as client:
            added = client.post('/api/watched-folders', json={'path': inbox, 'recursive': True})
            self.assertEqual(added.status_code, 200, added.text)
            folder = added.json()['folder']
            self.assertTrue(folder['online'])
            self.assertIsNone(folder['last_scan_at'])

            first_scan = client.post(f"/api/watched-folders/{folder['id']}/scan")
            self.assertEqual(first_scan.status_code, 200, first_scan.text)
            self.assertEqual(first_scan.json()['registered'], 1)
            self.assertIsNotNone(first_scan.json()['folder']['last_scan_at'])

            no_change = client.post(f"/api/watched-folders/{folder['id']}/scan")
            self.assertEqual(no_change.status_code, 200, no_change.text)
            self.assertEqual(no_change.json()['registered'], 0)

            second = os.path.join(inbox, 'second.jpg')
            with open(second, 'wb') as handle:
                handle.write(b'second image')
            os.utime(second, (time.time() + 2, time.time() + 2))
            second_scan = client.post(f"/api/watched-folders/{folder['id']}/scan")
            self.assertEqual(second_scan.status_code, 200, second_scan.text)
            self.assertEqual(second_scan.json()['registered'], 1)

            disabled = client.patch(f"/api/watched-folders/{folder['id']}", json={'enabled': False})
            self.assertEqual(disabled.status_code, 200, disabled.text)
            skipped = client.post(f"/api/watched-folders/{folder['id']}/scan")
            self.assertEqual(skipped.status_code, 200, skipped.text)
            self.assertEqual(skipped.json()['skipped'], 'disabled')

            removed = client.delete(f"/api/watched-folders/{folder['id']}")
            self.assertEqual(removed.status_code, 200, removed.text)

        conn = sqlite3.connect(db.DB_PATH)
        try:
            count = conn.execute('SELECT COUNT(*) FROM images').fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 2)

    async def test_non_recursive_watch_ignores_nested_files(self):
        inbox = os.path.join(self.tempdir.name, 'flat-watch')
        nested = os.path.join(inbox, 'nested')
        os.makedirs(nested)
        for path in (os.path.join(inbox, 'root.jpg'), os.path.join(nested, 'nested.jpg')):
            with open(path, 'wb') as handle:
                handle.write(b'image')

        folder = await watched_folders.add_folder(db.DB_PATH, inbox, recursive=False)
        result = await watched_folders.scan_folder(db.DB_PATH, folder['id'])
        self.assertTrue(result['ok'])
        self.assertEqual(result['registered'], 1)

    async def test_scan_offloads_disk_walk_from_event_loop(self):
        inbox = os.path.join(self.tempdir.name, 'off-loop-watch')
        os.makedirs(inbox)
        with open(os.path.join(inbox, 'photo.jpg'), 'wb') as handle:
            handle.write(b'image')

        folder = await watched_folders.add_folder(db.DB_PATH, inbox)
        real_to_thread = asyncio.to_thread
        with mock.patch.object(
            watched_folders.asyncio,
            'to_thread',
            wraps=real_to_thread,
        ) as to_thread:
            result = await watched_folders.scan_folder(db.DB_PATH, folder['id'])

        self.assertTrue(result['ok'])
        self.assertEqual(result['registered'], 1)
        to_thread.assert_awaited_once()

    async def test_incremental_scan_finds_new_file_with_preserved_old_mtime(self):
        inbox = os.path.join(self.tempdir.name, 'preserved-mtime')
        os.makedirs(inbox)
        old_mtime = time.time() - 86400
        first = os.path.join(inbox, 'first.jpg')
        with open(first, 'wb') as handle:
            handle.write(b'first image')
        os.utime(first, (old_mtime, old_mtime))

        folder = await watched_folders.add_folder(db.DB_PATH, inbox)
        initial = await watched_folders.scan_folder(db.DB_PATH, folder['id'])
        self.assertEqual(initial['registered'], 1)

        preserved = os.path.join(inbox, 'copied-with-old-time.jpg')
        with open(preserved, 'wb') as handle:
            handle.write(b'preserved image')
        os.utime(preserved, (old_mtime, old_mtime))

        incremental = await watched_folders.scan_folder(db.DB_PATH, folder['id'])

        self.assertEqual(incremental['registered'], 1)
        conn = sqlite3.connect(db.DB_PATH)
        try:
            paths = {row[0] for row in conn.execute('SELECT filepath FROM images')}
        finally:
            conn.close()
        self.assertIn(preserved, paths)

    async def test_watched_folder_rematch_restores_virtual_copy_availability(self):
        inbox = os.path.join(self.tempdir.name, 'vc-rematch')
        os.makedirs(inbox)
        original = os.path.join(inbox, 'photo.jpg')
        with open(original, 'wb') as handle:
            handle.write(b'virtual copy source')

        folder = await watched_folders.add_folder(db.DB_PATH, inbox, recursive=True)
        initial = await watched_folders.scan_folder(db.DB_PATH, folder['id'])
        self.assertEqual(initial['registered'], 1)

        conn = await db.get_db()
        try:
            master = await (await conn.execute(
                'SELECT id FROM images WHERE filepath = ? AND vc_of IS NULL', (original,)
            )).fetchone()
            copy = await virtual_copies.create_virtual_copy(conn, int(master['id']))
            await conn.commit()
            master_id = int(master['id'])
            copy_id = int(copy['id'])
        finally:
            await conn.close()

        moved_dir = os.path.join(inbox, 'moved')
        os.makedirs(moved_dir)
        moved = os.path.join(moved_dir, 'photo.jpg')
        os.rename(original, moved)
        conn = await db.get_db()
        try:
            await conn.execute(
                'UPDATE images SET missing_at = 100 WHERE id IN (?, ?)',
                (master_id, copy_id),
            )
            await conn.commit()
        finally:
            await conn.close()

        rematch = await watched_folders.scan_folder(db.DB_PATH, folder['id'])
        self.assertEqual(rematch['registered'], 1)
        conn = await db.get_db()
        try:
            rows = await (await conn.execute(
                'SELECT id, filepath, missing_at FROM images WHERE id IN (?, ?) ORDER BY id',
                (master_id, copy_id),
            )).fetchall()
        finally:
            await conn.close()

        self.assertEqual([row['filepath'] for row in rows], [moved, moved])
        self.assertTrue(all(row['missing_at'] is None for row in rows))

    async def test_watched_folder_ui_contract(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, 'static', 'js', 'desktop', 'watched_folders.js'), encoding='utf-8') as handle:
            watched_js = handle.read()
        self.assertIn('Scan now', watched_js)
        self.assertIn('data-watched-enabled', watched_js)
        self.assertIn("'/api/watched-folders'", watched_js)
