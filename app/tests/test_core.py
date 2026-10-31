"""
test_core.py  -  Tests for the chat core, using a FAKE model (no Ollama needed).

    python app/test_core.py
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _studybuddy_test_support import PROJECT_ROOT
BASE_DIR = str(PROJECT_ROOT)

from chat_core import ChatCore  # noqa: E402
from llm import LLMError  # noqa: E402
from prompt_builder import build_system_prompt, build_messages  # noqa: E402
from memory import Memory  # noqa: E402


class FakeLLM:
    def __init__(self, reply="Hello friend!", fail=False):
        self.reply = reply
        self.fail = fail
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise LLMError("fake failure")
        return self.reply


class TestCore(unittest.TestCase):
    def test_normal_message_uses_model(self):
        fake = FakeLLM("Hi there!")
        core = ChatCore(BASE_DIR, llm=fake)
        r = core.handle_message("Could you explain gravity?")
        self.assertEqual(r["flag"], "none")
        self.assertEqual(r["reply"], "Hi there!")
        self.assertEqual(len(fake.calls), 1)

    def test_current_visual_observations_reach_llama_as_text(self):
        fake = FakeLLM("I can make only a tentative guess.")
        core = ChatCore(BASE_DIR, llm=fake)
        core.settings["webcam_enabled"] = True
        core.visual_context_provider = lambda: (
            "Recent local facial-movement observations: smile-related movement low; "
            "brow-lowering movement moderate."
        )
        result = core.handle_message("Can you explain my mood?")
        self.assertEqual(result["reply"], "I can make only a tentative guess.")
        messages = fake.calls[0]
        self.assertIn("CURRENT CAMERA CONTEXT", messages[0]["content"])
        self.assertIn("brow-lowering movement moderate", messages[0]["content"])
        self.assertIn("brow-lowering movement moderate", messages[-1]["content"])
        self.assertIn("User's message: Can you explain my mood?", messages[-1]["content"])

    def test_simple_greeting_skips_model(self):
        fake = FakeLLM("This should not be used")
        core = ChatCore(BASE_DIR, llm=fake)
        for greeting in ("Hi", "hello!", "Hey Nova", "Good morning", "How are you?", "Hey, how are you?"):
            result = core.handle_message(greeting)
            self.assertEqual(result["flag"], "greeting", greeting)
            self.assertTrue(result["reply"], greeting)
        self.assertEqual(len(fake.calls), 0)

    def test_greeting_with_real_request_uses_model(self):
        fake = FakeLLM("Sure, I can help with your assignment.")
        core = ChatCore(BASE_DIR, llm=fake)
        result = core.handle_message("Hi, can you help me with my assignment?")
        self.assertEqual(result["flag"], "none")
        self.assertEqual(len(fake.calls), 1)

    def test_non_english_never_reaches_model(self):
        fake = FakeLLM()
        core = ChatCore(BASE_DIR, llm=fake)
        r = core.handle_message("আমি ভালো")
        self.assertEqual(r["flag"], "language")
        self.assertEqual(len(fake.calls), 0)

    def test_high_risk_never_reaches_model(self):
        fake = FakeLLM("anything")
        core = ChatCore(BASE_DIR, llm=fake)
        r = core.handle_message("I want to kill myself")
        self.assertEqual(r["flag"], "high")
        self.assertEqual(len(fake.calls), 0)
        self.assertIn("safe", r["reply"].lower())

    def test_medium_risk_has_opening_and_extra_care(self):
        fake = FakeLLM("I'm listening.")
        core = ChatCore(BASE_DIR, llm=fake)
        r = core.handle_message("I feel so hopeless")
        self.assertEqual(r["flag"], "medium")
        self.assertIn("I'm listening.", r["reply"])
        self.assertIn("safe", r["reply"].lower())
        system = fake.calls[0][0]["content"]
        self.assertIn("EXTRA CARE", system)

    def test_bad_model_reply_is_replaced(self):
        fake = FakeLLM("You should just kill yourself.")
        core = ChatCore(BASE_DIR, llm=fake)
        r = core.handle_message("I failed my quiz")
        self.assertEqual(r["reply"], core.safety.rules["output_fallback"])

    def test_model_failure_gives_error_reply(self):
        core = ChatCore(BASE_DIR, llm=FakeLLM(fail=True))
        r = core.handle_message("Explain gravity")
        self.assertEqual(r["flag"], "error")

    def test_history_is_trimmed(self):
        fake = FakeLLM("ok")
        core = ChatCore(BASE_DIR, llm=fake)
        core.settings["recent_turns"] = 2
        for i in range(6):
            core.handle_message("message number " + str(i))
        last = fake.calls[-1]
        # system + (2 turns * 2 messages) + new user message
        self.assertEqual(len(last), 1 + 4 + 1)

    def test_desktop_title_question_is_given_to_llm_and_grounded_in_fresh_provider(self):
        fake = FakeLLM("This should not be called")
        core = ChatCore(BASE_DIR, llm=fake)
        core.desktop_context_provider = lambda: (
            '{"app_name":"chrome","app_category":"Media",'
            '"window_title":"2PAC - LEGENDARY 2017 - YouTube",'
            '"private_context":false,"idle_seconds":8}'
        )
        result = core.handle_message("What am I doing right now, can you tell me by viewing my tab?")
        self.assertEqual(result["flag"], "desktop_context")
        self.assertIn("2PAC - LEGENDARY 2017", result["reply"])
        self.assertIn("can't read the page itself", result["reply"])
        self.assertEqual(len(fake.calls), 1)
        self.assertTrue(any(
            '"window_title":"2PAC - LEGENDARY 2017 - YouTube"' in message.get("content", "")
            for message in fake.calls[-1] if message.get("role") == "system"
        ))

    def test_desktop_question_does_not_fall_back_to_old_chat_when_snapshot_missing(self):
        fake = FakeLLM("Maybe you're watching God of War.")
        core = ChatCore(BASE_DIR, llm=fake)
        core.history = [{"role": "assistant", "content": "Looks like God of War is open."}]
        core.desktop_context_provider = lambda: ""
        result = core.handle_message("What is the title of my current tab?")
        self.assertIn("won't guess", result["reply"])
        self.assertNotIn("God of War", result["reply"])
        self.assertEqual(len(fake.calls), 0)

    def test_unsupported_window_claim_is_repaired_even_when_title_words_match(self):
        fake = FakeLLM("You were watching a movie trailer about The Matrix Reloaded on YouTube.")
        core = ChatCore(BASE_DIR, llm=fake)
        core.desktop_context_provider = lambda: (
            '{"app_name":"Yandex Browser","app_category":"Media",'
            '"window_title":"The Chase: Enter the Trinity | The Matrix Reloaded [Open Matte] - YouTube",'
            '"private_context":false,"idle_seconds":8}'
        )
        result = core.handle_message("What do you think I was watching?")
        self.assertNotIn("trailer", result["reply"].casefold())
        self.assertIn("Matrix Reloaded", result["reply"])
        self.assertIn("can't read the page itself", result["reply"])

    def test_private_desktop_title_is_not_repeated(self):
        fake = FakeLLM("This should not be called")
        core = ChatCore(BASE_DIR, llm=fake)
        core.desktop_context_provider = lambda: (
            '{"app_name":"chrome","app_category":"Private",'
            '"window_title":"[title hidden for privacy]", "private_context":true}'
        )
        result = core.handle_message("What am I doing right now?")
        self.assertIn("privacy-sensitive", result["reply"])
        self.assertNotIn("password", result["reply"].casefold())
        self.assertEqual(len(fake.calls), 0)

    def test_proactive_without_fresh_snapshot_skips_without_llm(self):
        fake = FakeLLM("Hey, how has your afternoon been?")
        core = ChatCore(BASE_DIR, llm=fake)
        reply = core.generate_proactive_message("The user has been active for a while")
        self.assertEqual(reply, "")
        self.assertEqual(len(fake.calls), 0)

    def test_proactive_uses_current_title_only_not_old_chat_or_reminders(self):
        fake = FakeLLM("watching a physics lecture")
        memory = Memory(":memory:")
        memory.add_turn("user", "I am not watching God of War; stop repeating old guesses")
        memory.add_turn("assistant", "Reminder delivered: drink blood")
        memory.add_task("Drink blood", "old test task")
        core = ChatCore(BASE_DIR, llm=fake, memory=memory)
        core.history = [
            {"role": "user", "content": "I am not watching God of War; stop repeating old guesses"},
            {"role": "assistant", "content": "Looks like epic gaming action with God of War"},
            {"role": "assistant", "content": "Reminder delivered: drink blood"},
        ]
        situation = (
            'PROACTIVE DESKTOP CONTEXT (limited, local signal only): '
            '{"app_name":"chrome","app_category":"Browser research",'
            '"window_title":"YouTube - Newtons laws physics lecture",'
            '"private_context":false,"idle_seconds":10}'
        )
        before = len(memory.recent_turns(20))
        reply = core.generate_proactive_message(situation)
        self.assertIn("physics lecture", reply)
        self.assertIn("watching a physics lecture", reply)
        self.assertNotIn("free for a quick chat", reply)
        self.assertNotIn("pause", reply)
        prompt_text = "\n".join(message.get("content", "") for message in fake.calls[0])
        self.assertIn("Newtons laws physics lecture", prompt_text)
        self.assertNotIn("God of War", prompt_text)
        self.assertNotIn("drink blood", prompt_text)
        self.assertNotIn("POTENTIALLY RELATED OPEN TASKS", prompt_text)
        self.assertEqual(len(memory.recent_turns(20)), before)

    def test_proactive_rejects_unsupported_title_claims(self):
        fake = FakeLLM("enjoying epic gaming action with God of War")
        core = ChatCore(BASE_DIR, llm=fake)
        situation = (
            'PROACTIVE DESKTOP CONTEXT (limited, local signal only): '
            '{"app_name":"chrome","app_category":"Media",'
            '"window_title":"2PAC - LEGENDARY 2017 (SABIMIXX) - YouTube",'
            '"private_context":false,"idle_seconds":10}'
        )
        self.assertEqual(core.generate_proactive_message(situation), "")

    def test_proactive_skips_generic_app_title_without_content_clue(self):
        fake = FakeLLM("Looks like you are browsing inspiring photos on Instagram.")
        core = ChatCore(BASE_DIR, llm=fake)
        situation = (
            'PROACTIVE DESKTOP CONTEXT (limited, local signal only): '
            '{"app_name":"chrome","app_category":"Browser",'
            '"window_title":"Instagram", "private_context":false,"idle_seconds":10}'
        )
        self.assertEqual(core.generate_proactive_message(situation), "")
        self.assertEqual(len(fake.calls), 0)

    def test_empty_message_does_nothing(self):
        fake = FakeLLM()
        core = ChatCore(BASE_DIR, llm=fake)
        r = core.handle_message("   ")
        self.assertEqual(r["reply"], "")
        self.assertEqual(len(fake.calls), 0)


class TestPrompt(unittest.TestCase):
    def test_safety_core_is_first_and_name_rule(self):
        core = ChatCore(BASE_DIR, llm=FakeLLM())
        system = build_system_prompt(core.safety.prompt_core(), core.character)
        self.assertTrue(system.startswith("SAFETY RULES"))
        self.assertIn("English only", system)
        self.assertIn("do not have a name yet", system)

    def test_optional_parts_appear_in_order(self):
        core = ChatCore(BASE_DIR, llm=FakeLLM())
        system = build_system_prompt(
            core.safety.prompt_core(), core.character,
            owner_facts=["Name is Hamim"], retrieved_notes=["Had an exam"],
            situation_line="Time: 02:40",
        )
        a = system.index("Things you know")
        b = system.index("Relevant memories")
        c = system.index("Situation:")
        self.assertTrue(a < b < c)

    def test_messages_end_with_user_text(self):
        msgs = build_messages("sys", [], "hi")
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[-1], {"role": "user", "content": "hi"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
