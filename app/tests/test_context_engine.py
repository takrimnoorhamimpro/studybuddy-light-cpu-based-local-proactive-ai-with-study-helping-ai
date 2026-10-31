import unittest
from context_engine import ContextEngine, ContextSnapshot

class ContextEngineTests(unittest.TestCase):
    def test_browser_research_category_is_context_not_claim(self):
        self.assertEqual(ContextEngine._categorize('chrome.exe', 'Claude - Research'), 'Browser research')

    def test_private_window_title_is_redacted(self):
        snap = ContextSnapshot(app_category='Browser', app_name='chrome', window_title='Sign in - personal account')
        self.assertEqual(snap.safe_summary()['window_title'], '[title hidden for privacy]')

    def test_meeting_suppresses_proactive_message(self):
        snap = ContextSnapshot(app_category='Meeting', idle_seconds=10)
        decision = ContextEngine().proactive_decision(snap, last_interaction_age=3600)
        self.assertFalse(decision['act'])
        self.assertEqual(decision['reason'], 'meeting')

    def test_recent_chat_only_briefly_suppresses_nudge(self):
        snap = ContextSnapshot(app_category='Browser research', window_title='Claude - Research about attention mechanisms', idle_seconds=10)
        self.assertFalse(ContextEngine().proactive_decision(snap, last_interaction_age=10)['act'])
        self.assertTrue(ContextEngine().proactive_decision(snap, last_interaction_age=180)['act'])

    def test_active_user_is_not_suppressed_for_being_active(self):
        snap = ContextSnapshot(app_category='Coding', window_title='study_buddy.py - context engine implementation', idle_seconds=5)
        self.assertTrue(ContextEngine().proactive_decision(snap, last_interaction_age=180)['act'])

    def test_only_long_idle_suppresses_nudge(self):
        snap = ContextSnapshot(app_category='Coding', idle_seconds=960)
        self.assertFalse(ContextEngine().proactive_decision(snap, last_interaction_age=3600)['act'])

if __name__ == '__main__':
    unittest.main()
