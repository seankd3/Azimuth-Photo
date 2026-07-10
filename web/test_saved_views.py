from test_support import *  # noqa: F401,F403

from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.library.saved_views import router


class SavedViewsTests(BackendTestCase):
    async def test_saved_view_round_trip(self):
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            created = client.post("/api/saved-views", json={
                "name": "Winter selects",
                "query": '{"q":"snow","flag":"picked","sort":"date_taken"}',
            })
            self.assertEqual(created.status_code, 201)
            view = created.json()["view"]
            self.assertEqual(view["name"], "Winter selects")
            self.assertIn('"sort":"date_taken"', view["query"])
            self.assertTrue(view["created_at"])

            listed = client.get("/api/saved-views")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual([item["id"] for item in listed.json()["views"]], [view["id"]])

            updated = client.patch(f"/api/saved-views/{view['id']}", json={"name": "Winter keepers"})
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["view"]["name"], "Winter keepers")
            self.assertEqual(updated.json()["view"]["query"], view["query"])

            removed = client.delete(f"/api/saved-views/{view['id']}")
            self.assertEqual(removed.status_code, 200)
            self.assertTrue(removed.json()["ok"])
            self.assertEqual(client.get("/api/saved-views").json()["views"], [])
