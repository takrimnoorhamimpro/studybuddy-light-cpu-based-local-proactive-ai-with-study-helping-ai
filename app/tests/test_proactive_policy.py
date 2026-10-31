import unittest
from proactive_policy import (ProactivePauseFlow, classify_user_boundary,
                              clear_legacy_automatic_pause_state,
                              mentions_closed_task, parse_pause_duration)
from memory import Memory
from personal_companion import PersonalCompanion

class ProactivePolicyTests(unittest.TestCase):
    def test_busy_context_does_not_automatically_pause(self):
        self.assertEqual(classify_user_boundary("I'm watching YouTube now, I'll tell you later"), "busy")
        self.assertEqual(classify_user_boundary("I'm focused right now"), "busy")
        flow = ProactivePauseFlow()
        result = flow.user_turn("I'm focused right now", now=100, busy_reply=True)
        self.assertFalse(flow.is_paused(100))
        self.assertTrue(result["direct"])
        self.assertIn("leave you to focus", result["reply"])
    def test_explicit_pause_without_duration_asks_before_pausing(self):
        flow = ProactivePauseFlow()
        result = flow.user_turn("pause check-ins", now=100, pause_request=True)
        self.assertTrue(result["direct"])
        self.assertIn("until you message me again", result["reply"])
        self.assertTrue(flow.is_paused(100))
        self.assertFalse(flow.awaiting_duration)
    def test_pause_without_duration_does_not_wait_for_a_followup(self):
        flow = ProactivePauseFlow()
        result = flow.user_turn("please pause", now=100, pause_request=True)
        self.assertTrue(result["direct"])
        self.assertFalse(flow.awaiting_duration)
        self.assertTrue(flow.is_paused(221))
    def test_pause_duration_is_confirmed_clearly_and_new_message_resumes(self):
        flow = ProactivePauseFlow()
        result = flow.user_turn("pause for 30 minutes", now=100, pause_request=True)
        self.assertTrue(flow.is_paused(101))
        self.assertTrue(flow.is_paused(101))
        self.assertIn("paused for 30 minutes", result["reply"])
        self.assertIn("new message", result["reply"])
        resumed = flow.user_turn("Can you explain this?", now=110)
        self.assertFalse(flow.is_paused(110))
        self.assertIn("active again", resumed["notice"])
    def test_app_restart_starts_with_no_pause(self):
        flow = ProactivePauseFlow()
        flow.user_turn("pause for 2 hours", now=100, pause_request=True)
        self.assertTrue(flow.is_paused(101))
        self.assertFalse(ProactivePauseFlow().is_paused(101))
    def test_legacy_automatic_mutes_clear_but_explicit_settings_off_survives(self):
        memory = Memory(":memory:")
        try:
            pc = PersonalCompanion(memory)
            pc.set_setting("initiative_pause_until", "999999")
            pc.set_setting("trigger_mute_until_title_clue", "999999")
            pc.set_setting("trigger_enabled_title_clue", "0")
            pc.set_setting("trigger_enabled_usual_study_start", "0")
            with memory._db_lock:
                memory.db.execute(
                    "INSERT INTO consent_ledger(ts,scope,granted,note) VALUES(?,?,?,?)",
                    (100, "initiative:usual_study_start", 0, "trigger_toggle"))
                memory.db.commit()
            clear_legacy_automatic_pause_state(memory, pc)
            self.assertEqual(pc.setting("initiative_pause_until"), "0")
            self.assertEqual(pc.setting("trigger_mute_until_title_clue"), "0")
            self.assertEqual(pc.setting("trigger_enabled_title_clue"), "1")
            self.assertEqual(pc.setting("trigger_enabled_usual_study_start"), "0")
        finally:
            memory.close()
    def test_duration_parser_requires_pause_request_or_duration_answer(self):
        self.assertEqual(parse_pause_duration("pause for half an hour"), (1800, "30 minutes"))
        self.assertEqual(parse_pause_duration("pause 2 hours"), (7200, "2 hours"))
        self.assertEqual(classify_user_boundary("pause until I message you"), "pause")
        self.assertEqual(parse_pause_duration("pause 30m"), (1800, "30 minutes"))
        self.assertIsNone(parse_pause_duration("take a break in 30 minutes"))
    def test_pause_when_user_says_stop_asking(self):
        self.assertEqual(classify_user_boundary("I said don't ask me now"), "pause")
    def test_resume_when_user_returns(self):
        self.assertEqual(classify_user_boundary("I finished watching that; you can ask me now"), "resume")
    def test_closed_task_is_detected_even_with_pause(self):
        text = "I quit that project now, I'm watching YouTube and I'll tell you later"
        self.assertEqual(classify_user_boundary(text), "busy")
        self.assertTrue(mentions_closed_task(text))
    def test_normal_message_does_not_change_boundary(self):
        self.assertEqual(classify_user_boundary("I'm learning about Zeiss optics"), "none")
    def test_task_close(self):
        self.assertTrue(mentions_closed_task("I won't work on that project anymore"))

if __name__ == "__main__":
    unittest.main()
