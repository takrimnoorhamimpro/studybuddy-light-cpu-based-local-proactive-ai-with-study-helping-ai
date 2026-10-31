"""Tests for recognizing explicit chat-based camera permission questions."""
import os
import sys
import unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from desktop_app import StudyBuddyApp


class CameraChatPermissionTests(unittest.TestCase):
    def test_detects_the_standard_permission_question(self):
        self.assertTrue(StudyBuddyApp._reply_requests_camera_permission(
            "May I turn on the camera briefly to check facial-expression cues?"))

    def test_detects_a_direct_permission_question(self):
        self.assertTrue(StudyBuddyApp._reply_requests_camera_permission(
            "Would you like me to turn on the camera now?"))

    def test_does_not_treat_false_claim_as_permission_request(self):
        self.assertFalse(StudyBuddyApp._reply_requests_camera_permission(
            "I can see you through the camera."))

    def test_does_not_trigger_without_question(self):
        self.assertFalse(StudyBuddyApp._reply_requests_camera_permission(
            "May I turn on the camera briefly."))

    def test_recognizes_explicit_webcam_consent_in_natural_language(self):
        self.assertTrue(StudyBuddyApp._is_explicit_camera_consent(
            "I am giving you permission to see me using webcam"))
        self.assertTrue(StudyBuddyApp._is_explicit_camera_consent(
            "You have my permission to turn on the camera"))

    def test_does_not_mistake_camera_status_question_for_consent(self):
        self.assertFalse(StudyBuddyApp._is_explicit_camera_consent(
            "Can you see me using my webcam access?"))
        self.assertTrue(StudyBuddyApp._is_camera_status_question(
            "Can you see me using my webcam access?"))

    def test_mood_questions_are_routed_separately(self):
        self.assertTrue(StudyBuddyApp._is_mood_question("Can you explain my mood?"))
        self.assertFalse(StudyBuddyApp._is_camera_status_question("Can you explain my mood?"))

    def test_expression_requests_are_distinct_from_camera_status(self):
        self.assertTrue(StudyBuddyApp._is_expression_question("Hi, can you read my expression?"))
        self.assertTrue(StudyBuddyApp._is_expression_question("Read my expression"))
        self.assertFalse(StudyBuddyApp._is_camera_status_question("Read my expression"))

    def test_realistic_expression_questions_are_recognized(self):
        self.assertTrue(StudyBuddyApp._is_expression_question("judge my facial expression"))
        self.assertTrue(StudyBuddyApp._is_mood_question("ok what do you think, I am happy or sad"))
        self.assertTrue(StudyBuddyApp._is_mood_question("am I happy?"))

    def test_short_now_followups_are_recognized(self):
        for phrase in ("now?", "and now?", "what about now?", "how about now?"):
            self.assertTrue(StudyBuddyApp._is_camera_followup(phrase), phrase)
        self.assertFalse(StudyBuddyApp._is_camera_followup("now start my timer"))

    def test_visual_summary_is_short_and_does_not_repeat_caveat(self):
        raw = ("Recent local facial-movement observations (not a reliable diagnosis of emotion): "
               "smile-related movement: low; brow-lowering movement: low; eye-blink closure: moderate; "
               "jaw-open movement: low. These signals may reflect expression, lighting, pose, or individual differences; "
               "ask the user rather than asserting a mood.")
        result = StudyBuddyApp._summarize_visual_observations(raw)
        self.assertIn("smile-related movement: low", result)
        self.assertIn("eye-blink closure: moderate", result)
        self.assertNotIn("These signals may reflect expression, lighting", result)

    def test_visual_summary_preserves_rough_expression_pattern(self):
        raw = ("Recent local facial-movement observations averaged over 5 samples: "
               "smile-related movement: moderate; brow-lowering movement: low; "
               "eye-blink closure: low; jaw-open movement: low; "
               "estimated visible expression pattern: smiling expression cues (heuristic strength: moderate). "
               "The expression pattern is a rough visual heuristic.")
        result = StudyBuddyApp._summarize_visual_observations(raw)
        self.assertIn("smiling expression cues", result)

    def test_ferplus_summary_is_preserved_for_chat(self):
        raw = ("Offline FER+ CNN prediction smoothed across 5 recent frames: "
               "visible expression most resembles happy in the FER+ training labels "
               "(model score 78%); next scores: happy 78%, neutral 8%, surprised 5%. "
               "This is an uncertain facial-image classification, not a claim about actual feelings.")
        result = StudyBuddyApp._summarize_visual_observations(raw)
        self.assertIn("most resembles happy", result)
        self.assertIn("78%", result)
        self.assertIn("cannot know your actual feelings", result)

    def test_no_face_summary_does_not_infer_mood(self):
        result = StudyBuddyApp._summarize_visual_observations("No face detected in the recent camera sample; do not infer a mood from visual data.")
        self.assertIn("didn't detect a face", result)
        self.assertIn("won't guess your expression", result)



    def test_active_camera_status_question_uses_local_features_without_llm(self):
        from types import SimpleNamespace
        app = object.__new__(StudyBuddyApp)
        app.camera_active = True
        app.camera_consent_pending = False
        app.camera_declined_this_chat = False
        app.last_camera_topic_ts = 0.0
        app.settings = {"webcam_enabled": True}
        app.core = SimpleNamespace(camera_declined_this_chat=False)
        app._visual_context = lambda: "Recent local facial-movement observations: smile-related movement: low."
        replies = []
        app._append = lambda *args, **kwargs: replies.append(args[1])
        handled = app._handle_camera_chat_command("Can you see me now?")
        self.assertTrue(handled)
        self.assertTrue(replies)
        self.assertIn("camera preview is active", replies[0].lower())

    def test_see_me_now_request_while_active_uses_local_features(self):
        from types import SimpleNamespace
        app = object.__new__(StudyBuddyApp)
        app.camera_active = True
        app.camera_consent_pending = False
        app.camera_declined_this_chat = False
        app.last_camera_topic_ts = 0.0
        app.settings = {"webcam_enabled": True}
        app.core = SimpleNamespace(camera_declined_this_chat=False)
        app._visual_context = lambda: "Recent local facial-movement observations: brow-lowering movement: moderate."
        replies = []
        app._append = lambda *args, **kwargs: replies.append(args[1])
        handled = app._handle_camera_chat_command("now again see me")
        self.assertTrue(handled)
        self.assertTrue(replies)

    def test_expression_question_with_fresh_features_uses_local_features(self):
        from types import SimpleNamespace
        app = object.__new__(StudyBuddyApp)
        app.camera_active = True
        app.camera_consent_pending = False
        app.camera_declined_this_chat = False
        app.last_camera_topic_ts = 0.0
        app.settings = {"webcam_enabled": True}
        app.core = SimpleNamespace(camera_declined_this_chat=False)
        app._visual_context = lambda: "Recent local facial-movement observations: smile-related movement: low."
        replies = []
        app._append = lambda *args, **kwargs: replies.append(args[1])
        handled = app._handle_camera_chat_command("Hi, can you read my expression?")
        self.assertTrue(handled)
        self.assertTrue(replies)
        self.assertIn("recent local facial-movement cues", replies[0].lower())

    def test_mood_question_with_fresh_features_uses_local_features(self):
        from types import SimpleNamespace
        app = object.__new__(StudyBuddyApp)
        app.camera_active = True
        app.camera_consent_pending = False
        app.camera_declined_this_chat = False
        app.last_camera_topic_ts = 0.0
        app.settings = {"webcam_enabled": True}
        app.core = SimpleNamespace(camera_declined_this_chat=False)
        app._visual_context = lambda: "Recent local facial-movement observations: brow-lowering movement: moderate."
        replies = []
        app._append = lambda *args, **kwargs: replies.append(args[1])
        handled = app._handle_camera_chat_command("Can you explain my mood?")
        self.assertTrue(handled)
        self.assertTrue(replies)
        self.assertIn("can't reliably infer your mood", replies[0].lower())

    def test_chat_request_waits_for_chat_yes_before_opening_camera(self):
        from types import SimpleNamespace
        app = object.__new__(StudyBuddyApp)
        app.camera_active = False
        app.camera_consent_pending = False
        app.camera_declined_this_chat = False
        app.last_camera_topic_ts = 0.0
        app.settings = {"webcam_enabled": True}
        app.core = SimpleNamespace(camera_declined_this_chat=False)
        app._append = lambda *args, **kwargs: None
        calls = []
        app.open_camera = lambda **kwargs: calls.append(kwargs)
        handled = app._handle_camera_chat_command("you can see me using camera")
        self.assertTrue(handled)
        self.assertTrue(app.camera_consent_pending)
        self.assertEqual(calls, [], "camera must not open before the user replies yes")


    def test_what_can_you_see_reports_camera_and_raw_image_boundary(self):
        from types import SimpleNamespace
        app = object.__new__(StudyBuddyApp)
        app.camera_active = True
        app.camera_consent_pending = False
        app.camera_declined_this_chat = False
        app.last_camera_topic_ts = 0.0
        app.expression_analyzer = SimpleNamespace(available=False)
        app._visual_context = lambda: ""
        replies = []
        app._append = lambda *args, **kwargs: replies.append(args[1])
        handled = app._handle_camera_chat_command("what can you see")
        self.assertTrue(handled)
        self.assertIn("preview is active", replies[0].lower())
        self.assertIn("no camera image is sent to llama", replies[0].lower())

    def test_appearance_query_does_not_claim_visibility_when_fer_model_is_missing(self):
        from types import SimpleNamespace
        app = object.__new__(StudyBuddyApp)
        app.camera_active = True
        app.camera_consent_pending = False
        app.camera_declined_this_chat = False
        app.last_camera_topic_ts = 0.0
        app.settings = {"webcam_enabled": True}
        app.expression_analyzer = SimpleNamespace(
            available=False, last_error="The offline FER+ ONNX model is missing")
        app.core = SimpleNamespace(camera_declined_this_chat=False, tool_orchestrator=object())
        app._visual_context = lambda: ""
        replies = []
        app._append = lambda *args, **kwargs: replies.append(args[1])
        handled = app._handle_camera_chat_command("how am i look like")
        self.assertTrue(handled)
        self.assertIn("analysis isn't available", replies[0].lower())
        self.assertNotIn("i can see you", replies[0].lower())

    def test_screen_question_never_claims_screen_access(self):
        app = object.__new__(StudyBuddyApp)
        app.camera_active = True
        app.awareness_enabled = True
        replies = []
        app._append = lambda *args, **kwargs: replies.append(args[1])
        handled = app._handle_camera_chat_command("what can you see on my screen?")
        self.assertTrue(handled)
        self.assertIn("cannot view or read your screen", replies[0].lower())
        self.assertIn("sanitized active-window title", replies[0].lower())


if __name__ == "__main__":
    unittest.main()
