from test_support import *  # noqa: F401,F403

import caption_worker


class StructuredUnderstandingTests(BackendTestCase):
    def test_caption_parser_preserves_searchable_structure(self):
        parsed = caption_worker.parse_caption_response(
            '{"caption":"A menu on a cafe table.","tags":["cafe","menu"],'
            '"visible_text":"Bluebird Cafe 8:30 AM",'
            '"entities":["menu","coffee cup"],'
            '"attributes":{"setting":"cafe","lighting":"window light"}}'
        )

        self.assertTrue(parsed["parsed"])
        self.assertEqual(parsed["understanding"]["visible_text"], "Bluebird Cafe 8:30 AM")
        self.assertEqual(parsed["understanding"]["entities"], ["menu", "coffee cup"])
        self.assertEqual(parsed["understanding"]["attributes"]["setting"], "cafe")

    async def test_visible_text_and_entities_are_searchable(self):
        source = await self._source("understanding")
        image_id = await self._image(source["id"], "opaque-name.jpg")
        config = settings.active_caption_config()
        await db.store_caption_result(
            image_id=image_id,
            caption_config=config,
            caption="A paper item rests on a wooden table.",
            tags=["paper"],
            understanding={
                "visible_text": "Bluebird Cafe",
                "entities": ["coffee cup", "breakfast menu"],
                "attributes": {"setting": "cafe", "time": "morning"},
            },
            status="done",
        )

        visible_text_matches = await db.caption_search_ranked_image_ids(
            "Bluebird", caption_config=config
        )
        entity_matches = await db.caption_search_ranked_image_ids(
            "breakfast menu", caption_config=config
        )
        saved = await db.get_image_caption(image_id, caption_config=config)

        self.assertEqual([row[0] for row in visible_text_matches], [image_id])
        self.assertEqual([row[0] for row in entity_matches], [image_id])
        self.assertEqual(saved["understanding"]["attributes"]["time"], "morning")
