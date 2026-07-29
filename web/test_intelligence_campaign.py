import unittest
from unittest.mock import patch

from core.intelligence_campaign import apply_saved_worker_intent


class IntelligenceCampaignTests(unittest.TestCase):
    def test_changed_saved_intent_updates_workers_without_rewriting_settings(self):
        previous = {
            "embedding_scan_enabled": False,
            "people_scan_enabled": True,
            "caption_scan_enabled": False,
        }
        current = {
            "embedding_scan_enabled": True,
            "people_scan_enabled": False,
            "caption_scan_enabled": True,
        }

        with (
            patch("embedding_worker.resume_embedding_worker") as resume_embeddings,
            patch("face_worker.pause_face_worker") as pause_people,
            patch("caption_worker.resume_caption_worker") as resume_captions,
        ):
            changed = apply_saved_worker_intent(previous, current)

        self.assertEqual(
            changed,
            (
                "embedding_scan_enabled",
                "people_scan_enabled",
                "caption_scan_enabled",
            ),
        )
        resume_embeddings.assert_called_once_with(persist=False)
        pause_people.assert_called_once_with(persist=False)
        resume_captions.assert_called_once_with(persist=False)

    def test_unchanged_intent_does_not_touch_workers(self):
        current = {
            "embedding_scan_enabled": True,
            "people_scan_enabled": True,
            "caption_scan_enabled": True,
        }
        self.assertEqual(apply_saved_worker_intent(current, current), ())
