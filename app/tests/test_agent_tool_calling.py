"""Tests for native LLM-first tool routing without a live Ollama service."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _studybuddy_test_support import PROJECT_ROOT

from agent import ToolOrchestrator, build_builtin_tool_registry
from agent.tool_registry import ToolRegistry, ToolSpec
from memory import Memory


class FakeToolLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_completion(self, messages, tools=None):
        self.calls.append({"messages": messages, "tools": tools})
        if not self.responses:
            return {"message": {"role": "assistant", "content": "done"}}
        return self.responses.pop(0)



class FakeOrchestrator:
    def __init__(self):
        self.calls = []
    def run(self, messages, on_token=None):
        self.calls.append(messages)
        return "Tool-backed answer"

class CoreStub:
    def __init__(self, memory):
        self.memory = memory
        self.pending_reminder_seconds = None
        self.timer_callback = None
        self.last_created_task_id = None
        self.last_task_snapshot = []
        self.last_listed_task_ids = []
        self.visual_context_provider = None


class TestChatCoreIntegration(unittest.TestCase):
    def test_native_orchestrator_handles_capability_and_productivity_requests(self):
        from chat_core import ChatCore
        core = ChatCore(str(PROJECT_ROOT), llm=FakeToolLLM([]))
        fake_agent = FakeOrchestrator()
        core.tool_orchestrator = fake_agent
        answer = core.handle_message("What capabilities do you have?")
        self.assertEqual(answer["reply"], "Tool-backed answer")
        self.assertEqual(len(fake_agent.calls), 1)
        self.assertIn("get_capabilities", fake_agent.calls[0][0]["content"])

    def test_current_desktop_context_goes_to_llm_without_requerying_desktop_tool(self):
        from chat_core import ChatCore
        fake_llm = FakeToolLLM([])
        core = ChatCore(str(PROJECT_ROOT), llm=fake_llm)
        fake_agent = FakeOrchestrator()
        core.tool_orchestrator = fake_agent
        core.desktop_context_provider = lambda: '{"app_name":"chrome","app_category":"Browser","window_title":"Physics lecture"}'
        answer = core.handle_message("what I am doing right now, can you tell me by viewing my tab")
        self.assertIn("Physics lecture", answer["reply"])
        self.assertIn("can't read the page itself", answer["reply"])
        self.assertEqual(fake_agent.calls, [])
        self.assertEqual(len(fake_llm.calls), 1)
        sent_messages = fake_llm.calls[0]["messages"]
        self.assertTrue(any(
            '"window_title":"Physics lecture"' in message.get("content", "")
            for message in sent_messages if message.get("role") == "system"
        ))

    def test_exact_task_command_uses_local_fast_path_with_native_agent(self):
        from chat_core import ChatCore
        memory = Memory(":memory:")
        try:
            fake_llm = FakeToolLLM([])
            core = ChatCore(str(PROJECT_ROOT), llm=fake_llm, memory=memory)
            registry = build_builtin_tool_registry(core)
            core.tool_registry = registry
            core.tool_orchestrator = ToolOrchestrator(fake_llm, registry)
            answer = core.handle_message("Add task: finish CPU optimization tests")
            self.assertIn("Task added", answer["reply"])
            self.assertEqual(memory.list_tasks()[0]["title"], "finish CPU optimization tests")
            self.assertEqual(fake_llm.calls, [])
        finally:
            memory.close()

class TestRegistry(unittest.TestCase):
    def test_unknown_tool_never_executes(self):
        registry = ToolRegistry()
        registry.register(ToolSpec("safe", "safe", {"type": "object", "properties": {}, "required": []}, lambda: "ok"))
        with self.assertRaises(KeyError):
            registry.execute("os_system", {})

    def test_schema_rejects_unexpected_arguments(self):
        registry = ToolRegistry()
        registry.register(ToolSpec("safe", "safe", {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}, lambda x: x))
        with self.assertRaises(ValueError):
            registry.execute("safe", {"x": "a", "command": "whoami"})


class TestOrchestrator(unittest.TestCase):
    def test_desktop_and_appearance_phrasing_select_the_correct_tools(self):
        from chat_core import ChatCore
        from agent import build_builtin_tool_registry
        memory = Memory(":memory:")
        try:
            fake = FakeToolLLM([])
            core = ChatCore(str(PROJECT_ROOT), llm=fake, memory=memory)
            registry = build_builtin_tool_registry(core)
            agent = ToolOrchestrator(fake, registry)
            self.assertIn("get_desktop_context", agent.tools_for_text(
                "You have desktop awareness right? what am I doing?"))
            self.assertIn("get_desktop_context", agent.tools_for_text(
                "what am I doing right now by viewing my tab"))
            self.assertIn("get_camera_status", agent.tools_for_text("how am I look like"))
        finally:
            memory.close()


    def test_real_builtin_catalogue_retrieves_natural_language_intents(self):
        from chat_core import ChatCore
        from agent import build_builtin_tool_registry
        memory = Memory(":memory:")
        try:
            fake = FakeToolLLM([])
            core = ChatCore(str(PROJECT_ROOT), llm=fake, memory=memory)
            agent = ToolOrchestrator(fake, build_builtin_tool_registry(core))
            self.assertIn("search_memory", agent.tools_for_text("please tell me about our previous chats"))
            self.assertIn("list_tasks", agent.tools_for_text("what is in my completed tasks list"))
            self.assertIn("add_task", agent.tools_for_text("can you add the task: have tp pee"))
            self.assertIn("get_desktop_context", agent.tools_for_text("You have desktop awareness right? what am I doing?"))
            self.assertIn("get_capabilities", agent.tools_for_text("what can you do"))
            self.assertEqual(agent.tools_for_text("Explain Newtons second law"), [])
        finally:
            memory.close()



    def test_all_retrieval_mode_exposes_full_registered_catalogue_to_the_llm(self):
        fake = FakeToolLLM([{"message": {"role": "assistant", "content": "Okay."}}])
        registry = ToolRegistry()
        for name in ("search_memory", "list_tasks", "get_desktop_context"):
            registry.register(ToolSpec(name, name, {"type": "object", "properties": {}, "required": []}, lambda: {}))
        agent = ToolOrchestrator(fake, registry, retrieval_mode="all")
        self.assertEqual(agent.tools_for_text("Explain Newton's second law"), registry.names())

    def test_all_mode_never_calls_separate_embedding_model(self):
        class NoEmbeddingLLM(FakeToolLLM):
            def __init__(self):
                super().__init__([])
                self.embed_calls = 0
            def embed(self, inputs):
                self.embed_calls += 1
                raise AssertionError("Embedding is not part of the direct-tool architecture")
        fake = NoEmbeddingLLM()
        registry = ToolRegistry()
        registry.register(ToolSpec("search_memory", "Search memory", {
            "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]
        }, lambda query: {"query": query}))
        agent = ToolOrchestrator(fake, registry, retrieval_mode="all")
        self.assertEqual(agent.tools_for_text("bring back our last conversation"), ["search_memory"])
        self.assertEqual(fake.embed_calls, 0)

    def test_semantic_retrieval_handles_a_paraphrase_without_keyword_overlap(self):
        class SemanticFakeLLM(FakeToolLLM):
            def __init__(self):
                super().__init__([])
                self.embed_calls = []
            def embed(self, inputs):
                self.embed_calls.append(list(inputs))
                vectors = []
                for text in inputs:
                    low = text.casefold()
                    # Deterministic test vectors: paraphrase aligns with memory/history,
                    # while task documents align to the other axis.
                    memory_like = any(word in low for word in ("spoke about last time", "memory", "conversation", "past chat"))
                    vectors.append([1.0, 0.0] if memory_like else [0.0, 1.0])
                return vectors
        fake = SemanticFakeLLM()
        registry = ToolRegistry()
        registry.register(ToolSpec("search_memory", "Search saved memory and past conversation history", {
            "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]
        }, lambda query: {"ok": True}))
        registry.register(ToolSpec("list_tasks", "Read the user's structured task list and completed items", {
            "type": "object", "properties": {}, "required": []
        }, lambda: {"tasks": []}))
        agent = ToolOrchestrator(fake, registry, retrieval_mode="hybrid")
        query = "bring back what we spoke about last time"
        self.assertEqual(agent.tools_for_text(query), ["search_memory"])
        self.assertEqual(agent.tools_for_text(query), ["search_memory"])
        self.assertEqual(len(fake.embed_calls), 1)  # route and schema assembly share the cache
        self.assertTrue(fake.embed_calls[0][0].startswith("search_query: "))
        self.assertTrue(all(item.startswith("search_document: ") for item in fake.embed_calls[0][1:]))

    def test_missing_embedding_model_uses_cooldown_before_retry(self):
        class UnavailableEmbeddingLLM(FakeToolLLM):
            def __init__(self):
                super().__init__([])
                self.embed_calls = 0
            def embed(self, inputs):
                self.embed_calls += 1
                raise RuntimeError("embedding model missing")
        fake = UnavailableEmbeddingLLM()
        registry = ToolRegistry()
        registry.register(ToolSpec("search_memory", "Search saved memory and conversations", {
            "type": "object", "properties": {}, "required": []
        }, lambda: {}))
        agent = ToolOrchestrator(fake, registry, retrieval_mode="hybrid")
        agent.tools_for_text("bring back what we spoke about last time")
        agent.tools_for_text("recall our past conversation")
        self.assertEqual(fake.embed_calls, 1)

    def test_executes_tool_and_returns_result_to_model(self):
        fake = FakeToolLLM([
            {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "search_memory", "arguments": {"query": "hello", "scope": "conversations"}}}
            ]}},
            {"message": {"role": "assistant", "content": "I found hello."}},
        ])
        registry = ToolRegistry()
        registry.register(ToolSpec("search_memory", "search memory", {
            "type": "object", "properties": {"query": {"type": "string"}, "scope": {"type": "string"}},
            "required": ["query"]
        }, lambda query, scope="conversations": {"echo": query}))
        result = ToolOrchestrator(fake, registry).run([
            {"role": "user", "content": "Do you remember what I told you about hello?"}
        ])
        self.assertEqual(result, "I found hello.")
        self.assertEqual(len(fake.calls), 2)
        second_messages = fake.calls[1]["messages"]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertIn('"echo": "hello"', second_messages[-1]["content"])
        self.assertEqual(fake.calls[0]["tools"][0]["function"]["name"], "search_memory")
        self.assertEqual(len(fake.calls[0]["tools"]), 1)

    def test_model_cannot_claim_action_success_without_tool_execution(self):
        fake = FakeToolLLM([
            {"message": {"role": "assistant", "content": "Task added: study mathematics."}},
        ])
        registry = ToolRegistry()
        result = ToolOrchestrator(fake, registry).run([
            {"role": "user", "content": "Add a task: study mathematics"}
        ])
        self.assertIn("haven't confirmed that action", result)
        self.assertIn("haven't changed anything", result)

    def test_camera_status_reply_is_authoritative_and_skips_final_model_pass(self):
        fake = FakeToolLLM([
            {"message": {"role": "assistant", "content": "I can see you just fine.", "tool_calls": [
                {"function": {"name": "get_camera_status", "arguments": {}}}
            ]}},
        ])
        registry = ToolRegistry()
        registry.register(ToolSpec("get_camera_status", "camera status; check whether the camera is on and whether you can see me or how I look", {
            "type": "object", "properties": {}, "required": []
        }, lambda: {"preview_active": False, "latest_expression_sample": None,
                    "analysis_available": False, "raw_frames_sent_to_llm": False}))
        result = ToolOrchestrator(fake, registry).run([
            {"role": "user", "content": "Can you see me?"}
        ])
        self.assertIn("camera preview is off", result.lower())
        self.assertNotIn("I can see you just fine", result)
        self.assertEqual(len(fake.calls), 1)

    def test_desktop_context_reply_distinguishes_window_title_from_screen_access(self):
        fake = FakeToolLLM([
            {"message": {"role": "assistant", "content": "I can see exactly what you're doing.", "tool_calls": [
                {"function": {"name": "get_desktop_context", "arguments": {}}}
            ]}},
            {"message": {"role": "assistant", "content": "Looks like you may be reviewing video notes in Google Keep. That is only a guess from the active-window title; I cannot see screen pixels or page contents."}},
        ])
        registry = ToolRegistry()
        registry.register(ToolSpec("get_desktop_context", "context", {
            "type": "object", "properties": {}, "required": []
        }, lambda: {"awareness_enabled": True, "available": True,
                    "snapshot": {"app_name": "chrome", "app_category": "Browser",
                                 "window_title": "Google Keep — Video notes", "idle_seconds": 5}}))
        result = ToolOrchestrator(fake, registry).run([
            {"role": "user", "content": "You have desktop awareness right? What am I doing?"}
        ])
        self.assertIn("Google Keep", result)
        self.assertIn("only a guess", result)
        self.assertIn("cannot see screen pixels", result)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(fake.calls[1]["tools"], [])


    def test_general_chat_does_not_receive_tool_schemas(self):
        fake = FakeToolLLM([{"message": {"role": "assistant", "content": "The answer is 42."}}])
        registry = ToolRegistry()
        registry.register(ToolSpec("search_memory", "search memory", {
            "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]
        }, lambda query: []))
        result = ToolOrchestrator(fake, registry).run([{"role": "user", "content": "Explain the speed of light."}])
        self.assertEqual(result, "The answer is 42.")
        self.assertEqual(fake.calls[0]["tools"], [])

    def test_action_tool_selection_is_narrow(self):
        fake = FakeToolLLM([{"message": {"role": "assistant", "content": "Okay."}}])
        registry = ToolRegistry()
        for name in ("add_task", "list_tasks", "complete_task", "delete_task", "start_timer", "search_memory"):
            registry.register(ToolSpec(name, name, {"type": "object", "properties": {}, "required": []}, lambda: {}))
        ToolOrchestrator(fake, registry).run([{"role": "user", "content": "Show my tasks."}])
        selected = [item["function"]["name"] for item in fake.calls[0]["tools"]]
        self.assertEqual(selected, ["add_task", "list_tasks", "complete_task", "delete_task"])

    def test_task_router_catches_noncanonical_complete_syntax(self):
        fake = FakeToolLLM([{"message": {"role": "assistant", "content": "Which task?"}}])
        registry = ToolRegistry()
        registry.register(ToolSpec("complete_task", "complete task", {
            "type": "object", "properties": {"task_title": {"type": "string"}},
            "required": ["task_title"]
        }, lambda task_title: {"completed": False}))
        ToolOrchestrator(fake, registry).run([{
            "role": "user", "content": "mark as complete - have to pee"
        }])
        selected = [item["function"]["name"] for item in fake.calls[0]["tools"]]
        self.assertEqual(selected, ["complete_task"])

    def test_verified_task_result_skips_second_inference(self):
        fake = FakeToolLLM([{"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "add_task", "arguments": {"title": "study biology"}}}
        ]}}])
        registry = ToolRegistry()
        registry.register(ToolSpec("add_task", "add task", {
            "type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]
        }, lambda title: {"created": True, "task": {"title": title}}))
        result = ToolOrchestrator(fake, registry).run([{"role": "user", "content": "Please add a task called study biology."}])
        self.assertEqual(result, "Task added: study biology.")
        self.assertEqual(len(fake.calls), 1)

    def test_tool_error_is_returned_to_model_not_reported_as_success(self):
        fake = FakeToolLLM([
            {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "search_memory", "arguments": {"query": "hello", "scope": "conversations"}}}
            ]}},
            {"message": {"role": "assistant", "content": "That tool was unavailable."}},
        ])
        registry = ToolRegistry()
        def fail_lookup(query, scope="conversations"):
            raise RuntimeError("private details omitted")
        registry.register(ToolSpec("search_memory", "search memory", {
            "type": "object", "properties": {"query": {"type": "string"}, "scope": {"type": "string"}},
            "required": ["query"]
        }, fail_lookup))
        result = ToolOrchestrator(fake, registry).run([
            {"role": "user", "content": "Do you remember what I told you?"}
        ])
        self.assertEqual(result, "That tool was unavailable.")
        self.assertIn('"ok": false', fake.calls[1]["messages"][-1]["content"])


class TestBuiltins(unittest.TestCase):
    def setUp(self):
        self.memory = Memory(":memory:")
        self.core = CoreStub(self.memory)
        self.registry = build_builtin_tool_registry(self.core)

    def tearDown(self):
        self.memory.close()

    def test_add_and_complete_task_verified(self):
        created = self.registry.execute("add_task", {"title": "finish final year project"})
        self.assertTrue(created["created"])
        completed = self.registry.execute("complete_task", {"task_title": "this task"})
        self.assertTrue(completed["completed"])
        self.assertEqual(self.memory.list_tasks(), [])

    def test_complete_task_normalizes_separator_punctuation(self):
        self.registry.execute("add_task", {"title": "have to pee"})
        completed = self.registry.execute("complete_task", {"task_title": ": have to pee"})
        self.assertTrue(completed["completed"])
        self.assertEqual(self.memory.list_tasks(), [])

    def test_complete_whatever_in_tasks_resolves_only_single_open_task(self):
        self.registry.execute("add_task", {"title": "have to pee"})
        completed = self.registry.execute("complete_task", {"task_title": "whatever in my tasks"})
        self.assertTrue(completed["completed"])
        self.assertEqual(self.memory.list_tasks(), [])

    def test_complete_whatever_in_tasks_is_ambiguous_for_multiple_tasks(self):
        self.registry.execute("add_task", {"title": "have to pee"})
        self.registry.execute("add_task", {"title": "drink water"})
        completed = self.registry.execute("complete_task", {"task_title": "whatever in my tasks"})
        self.assertFalse(completed["completed"])
        self.assertTrue(completed["ambiguous"])
        self.assertEqual(len(self.memory.list_tasks()), 2)

    def test_ambiguous_this_task_does_not_complete_anything(self):
        self.registry.execute("add_task", {"title": "task one"})
        self.registry.execute("add_task", {"title": "task two"})
        self.registry.execute("list_tasks", {})
        result = self.registry.execute("complete_task", {"task_title": "this task"})
        self.assertFalse(result["completed"])
        self.assertTrue(result["ambiguous"])
        self.assertEqual(len(self.memory.list_tasks()), 2)

    def test_completed_task_stays_out_of_open_list(self):
        created = self.registry.execute("add_task", {"title": "cut the rope"})
        tid = created["task"]["id"]
        result = self.registry.execute("complete_task", {"task_title": "cut the rope"})
        self.assertTrue(result["completed"])
        self.assertNotIn(tid, [t["id"] for t in self.memory.list_tasks()])
        self.assertEqual(self.memory.list_tasks(include_done=True)[0]["status"], "done")

    def test_memory_search_finds_previous_conversation(self):
        self.memory.add_turn("user", "I watched a Netflix documentary about the founding of Netflix")
        found = self.registry.execute("search_memory", {"query": "Netflix documentary", "scope": "conversations"})
        self.assertTrue(found["conversation_matches"])
        self.assertIn("founding of Netflix", found["conversation_matches"][0]["text"])



    def test_completed_tasks_query_returns_structured_completed_items(self):
        self.registry.execute("add_task", {"title": "open task"})
        created = self.registry.execute("add_task", {"title": "finished task"})
        self.registry.execute("complete_task", {"task_title": "finished task"})
        completed = self.registry.execute("list_tasks", {"status": "completed"})
        self.assertEqual(completed["filter"], "completed_only")
        self.assertEqual([item["title"] for item in completed["tasks"]], ["finished task"])
        all_items = self.registry.execute("list_tasks", {"status": "all"})
        self.assertEqual(all_items["filter"], "open_and_completed")
        self.assertEqual({item["title"] for item in all_items["tasks"]}, {"open task", "finished task"})

    def test_generic_previous_chat_query_returns_recent_turns(self):
        self.memory.add_turn("user", "I have a physics exam on Friday")
        result = self.registry.execute("search_memory", {
            "query": "tell me about our previous chats", "scope": "all"
        })
        self.assertEqual(result["scope"], "conversations")
        self.assertEqual(result["query"], "")
        self.assertTrue(result["conversation_matches"])
        self.assertIn("physics exam", result["conversation_matches"][0]["text"])

    def test_create_reminder_uses_followup_delay(self):
        result = self.registry.execute("create_reminder", {"delay_seconds": 10})
        self.assertTrue(result["needs_followup"])
        self.assertEqual(self.core.pending_reminder_seconds, 10)
        saved = self.registry.execute("create_reminder", {"text": "play"})
        self.assertTrue(saved["created"])
        self.assertIsNone(self.core.pending_reminder_seconds)


if __name__ == "__main__":
    unittest.main(verbosity=2)
