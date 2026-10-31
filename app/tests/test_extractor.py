import os, sys, time, unittest
from datetime import datetime
sys.path.insert(0,os.path.dirname(__file__))
from companion.extractor import extract, parse_date

class ExtractorTests(unittest.TestCase):
    def setUp(self): self.now=datetime(2026,10,28,12).timestamp()
    def test_exam_date_candidate_is_neutral_and_keeps_exact_evidence(self):
        text="I'm preparing for my networking exam on Nov 20."
        items=extract(text,now=self.now)
        self.assertEqual(len(items),1); self.assertEqual(items[0].kind,'area')
        self.assertIn('their networking exam',items[0].label)
        self.assertEqual(items[0].evidence,text)
        self.assertIsNotNone(items[0].expires_ts)
    def test_goal_preference_schedule_and_interest_patterns(self):
        cases=[('My goal is to finish the physics simulator','goal'),('I prefer short explanations','preference'),('I usually study at the library','schedule'),('I enjoy astronomy documentaries','interest')]
        for text,kind in cases:
            with self.subTest(text=text): self.assertEqual(extract(text,now=self.now)[0].kind,kind)
    def test_questions_hypotheticals_commands_and_other_people_are_rejected(self):
        for text in ('Am I preparing for an exam?','If I study networking, I might pass','Remind me to study networking tomorrow','Add a task to study networking','My friend is studying networking for an exam'):
            with self.subTest(text=text): self.assertEqual(extract(text,now=self.now),[])
    def test_sensitive_content_is_rejected(self):
        for text in ('I have diabetes and I am preparing for an exam','I earn $5000 and prefer short answers','My email is takrim@example.com and I study networking','My religion is Christian and I enjoy astronomy','My phone number is +1 555 123 4567 and I study networking','My address is 123 Main Street and I study physics','My 16-year-old daughter enjoys astronomy','My student ID is ABCD12345 and I study networking'):
            with self.subTest(text=text): self.assertEqual(extract(text,now=self.now),[])
    def test_configured_ttl_is_used_for_non_deadline_facts(self):
        item=extract('My goal is to finish the physics simulator',now=self.now,ttl_days_by_kind={'goal':30})[0]
        self.assertEqual(item.ttl_days,30)
        self.assertAlmostEqual(item.expires_ts,self.now+30*86400,delta=1)
    def test_ambiguous_numeric_dates_are_ignored(self):
        self.assertIsNone(parse_date('My exam is on 03/04',self.now))
    def test_date_parser_supports_month_names_tomorrow_and_next_friday(self):
        self.assertEqual(parse_date('Nov 20',self.now).date(),datetime(2026,11,20).date())
        self.assertEqual(parse_date('20th November',self.now).date(),datetime(2026,11,20).date())
        self.assertEqual(parse_date('tomorrow',self.now).date(),datetime(2026,10,29).date())
        self.assertEqual(parse_date('next Friday',self.now).date(),datetime(2026,10,30).date())
    def test_extraction_is_under_five_ms(self):
        started=time.perf_counter(); extract("I'm preparing for my networking exam on Nov 20.",now=self.now)
        self.assertLess(time.perf_counter()-started,0.005)

if __name__=='__main__': unittest.main()
