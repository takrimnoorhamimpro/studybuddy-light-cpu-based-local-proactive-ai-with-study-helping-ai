"""
test_identity.py  -  Name handling (user name vs Buddy's name).

    python app/test_identity.py
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _studybuddy_test_support import PROJECT_ROOT
BASE_DIR = str(PROJECT_ROOT)

from chat_core import ChatCore  # noqa: E402
from memory import Memory  # noqa: E402
import identity  # noqa: E402


class FakeLLM:
    def __init__(self, reply="ok"):
        self.reply = reply
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return self.reply


class TestExtract(unittest.TestCase):
    def test_real_messages_from_the_chat_log(self):
        f = identity.extract_names
        self.assertEqual(f("My name is Hamim"), ("Hamim", None))
        self.assertEqual(
            f("I am not sam, my name is Takrim Noor Hamim, you can call me Takrim"),
            ("Takrim", None))
        self.assertEqual(
            f("you are my friend, your name is buddy, and I am chatting with you I am takrim,,"),
            (None, None))
        self.assertEqual(f("not you me takrim"), (None, None))

    def test_not_is_never_a_name(self):
        self.assertEqual(identity.extract_names("your name is not sam"), (None, None))
        self.assertEqual(identity.extract_name_correction("I am Takrim, not Pixel"), ("Takrim", "Pixel"))
        self.assertIsNone(identity.extract_name_correction("My name is Pixel? When did I set it up haha"))


class TestCoreNames(unittest.TestCase):
    def make(self):
        m = Memory(":memory:")
        fake = FakeLLM()
        return ChatCore(BASE_DIR, llm=fake, memory=m), fake, m

    def test_learn_and_answer_without_model(self):
        core, fake, m = self.make()
        r = core.handle_message("my name is Takrim, call me Takrim")
        self.assertEqual(r["flag"], "identity")
        chosen_name = core.buddy_name
        core.handle_message("your name is buddy")
        self.assertEqual(core.buddy_name, chosen_name)
        r = core.handle_message("what is your name and what is my name?")
        self.assertEqual(r["reply"], "My name is " + chosen_name + ". Your name is Takrim.")
        self.assertEqual(len(fake.calls), 0)

    def test_greetings_never_hardcode_another_assistant_name(self):
        core, fake, memory = self.make()
        try:
            core.buddy_name = "Pixel"
            reply = core._fast_greeting_response("hey")
            self.assertNotIn("Nova", reply)
            self.assertNotIn("Milo", reply)
        finally:
            memory.close()

    def test_unknown_names_are_not_invented(self):
        core, fake, m = self.make()
        r = core.handle_message("what is my name")
        self.assertIn("don't know your name", r["reply"])

    def test_names_survive_restart(self):
        core, fake, m = self.make()
        core.handle_message("call me Takrim")
        chosen_name = core.buddy_name
        core.handle_message("your name is Buddy")
        core2 = ChatCore(BASE_DIR, llm=FakeLLM(), memory=m)
        self.assertEqual((core2.user_name, core2.buddy_name), ("Takrim", chosen_name))

    def test_names_reach_the_model_prompt(self):
        core, fake, m = self.make()
        core.handle_message("call me Takrim")
        chosen_name = core.buddy_name
        core.handle_message("your name is Buddy")
        core.handle_message("I failed my quiz today")
        system = fake.calls[0][0]["content"]
        self.assertIn("The USER's name is Takrim", system)
        self.assertIn("YOUR chosen name is " + chosen_name, system)
        self.assertNotIn("do not have a name yet", system)

    def test_safety_wins_over_name_handling(self):
        core, fake, m = self.make()
        r = core.handle_message("my name is Takrim and I want to die")
        self.assertEqual(r["flag"], "high")

    def test_clear_chat_keeps_names(self):
        core, fake, m = self.make()
        core.handle_message("call me Takrim")
        core.handle_message("hello")
        core.clear_chat()
        self.assertEqual(core.history, [])
        self.assertEqual(core.user_name, "Takrim")


class TestChatLogRegressions(unittest.TestCase):
    """Exact lines from the real chat that went wrong."""
    def make(self, reply="My name is Takrim."):
        m = Memory(":memory:")
        fake = FakeLLM(reply)
        return ChatCore(BASE_DIR, llm=fake, memory=m), fake

    def test_choose_your_name_and_call_me(self):
        core, fake = self.make()
        r = core.handle_message("good, choose your name and you can call me takrim")
        self.assertEqual(core.user_name, "Takrim")
        self.assertTrue(core.buddy_name)
        self.assertNotEqual(core.buddy_name, "Takrim")
        self.assertEqual(len(fake.calls), 0)
        self.assertIn(core.buddy_name, r["reply"])

    def test_do_you_have_a_name_never_uses_model(self):
        core, fake = self.make()
        core.handle_message("call me takrim")
        r = core.handle_message("nice to meet you too, do you have a name??")
        self.assertEqual(r["flag"], "identity")
        self.assertNotIn("Takrim", r["reply"].replace("Your name is Takrim", ""))
        self.assertEqual(len(fake.calls), 0)

    def test_pick_a_different_name(self):
        core, fake = self.make()
        core.handle_message("call me takrim")
        chosen_name = core.buddy_name
        core.handle_message("your name is Milo")
        r = core.handle_message("no pick a different name for you not Takrim Noor Hamim cause thats my name")
        self.assertEqual(core.buddy_name, chosen_name)
        self.assertIn(chosen_name, r["reply"])
        self.assertEqual(r["flag"], "identity")

    def test_say_my_fucking_name(self):
        core, fake = self.make()
        core.handle_message("call me takrim")
        r = core.handle_message("say my fucking name")
        self.assertEqual(r["reply"], "Your name is Takrim.")

    def test_nickname_does_not_change_chosen_name(self):
        core, fake = self.make()
        m = core.memory
        chosen = core.buddy_name
        r = core.handle_message("I'll call you Buddy")
        self.assertEqual(core.buddy_name, chosen)
        self.assertEqual(core.buddy_nickname, "Buddy")
        self.assertIn("nickname", r["reply"].lower())
        self.assertEqual(m.get_profile("buddy_nickname"), "Buddy")

    def test_model_claiming_users_name_is_replaced(self):
        core, fake = self.make("My name is Takrim, nice to meet you!")
        core.handle_message("call me Takrim")
        r = core.handle_message("how is the weather")
        self.assertNotIn("My name is Takrim", r["reply"])
        self.assertIn("You are Takrim", r["reply"])

    def test_old_name_facts_are_not_sent_to_model(self):
        core, fake = self.make("ok")
        core.memory.add_fact("My name is Hamim")
        core.memory.add_fact("I have an exam on 20 Nov")
        core.handle_message("how is the weather")
        system = fake.calls[0][0]["content"]
        self.assertNotIn("My name is Hamim", system)
        self.assertNotIn("exam on 20 Nov", system)  # unrelated saved facts aren't injected into small talk


if __name__ == "__main__":
    unittest.main(verbosity=2)
