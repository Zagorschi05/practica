import unittest
from pathlib import Path

from app import ScheduleEntry, WEEK_ODD, parse_pdf


PDF_PATH = Path(r"C:\Users\Zagorschi Cristi\Downloads\anul_iii_semestrul_v-7.pdf")


class ScheduleEntryTests(unittest.TestCase):
    def test_single_odd_entry_can_stay_odd_week(self):
        entry = ScheduleEntry("Luni", "08:00-09:30", "TI-241", "Pereche", WEEK_ODD)

        self.assertEqual(entry.week, WEEK_ODD)


@unittest.skipUnless(PDF_PATH.exists(), "PDF-ul de test nu este disponibil")
class RealPdfTests(unittest.TestCase):
    def test_real_timetable_is_parsed(self):
        entries = parse_pdf(PDF_PATH)
        self.assertGreater(len(entries), 100)
        self.assertIn("TI-241", {entry.group for entry in entries})
        self.assertIn("Luni", {entry.day for entry in entries})

    def test_duplicate_cell_content_is_split_by_week(self):
        entries = parse_pdf(PDF_PATH)
        weekly_entries = [entry for entry in entries if entry.week != "Toate"]
        self.assertTrue(weekly_entries)
        self.assertIn("Impară", {entry.week for entry in weekly_entries})
        self.assertIn("Pară", {entry.week for entry in weekly_entries})

    def test_shared_cell_is_visible_for_both_groups(self):
        entries = parse_pdf(PDF_PATH)
        ti242 = {
            (entry.interval, entry.week, entry.content)
            for entry in entries
            if entry.group == "TI-242"
        }
        ti243 = {
            (entry.interval, entry.week, entry.content)
            for entry in entries
            if entry.group == "TI-243"
        }
        self.assertTrue(ti242.intersection(ti243))

    def test_common_courses_are_available_for_all_groups(self):
        entries = parse_pdf(PDF_PATH)
        courses = [
            entry for entry in entries
            if entry.content.lower().startswith("c.")
            and entry.day == "Marți"
            and entry.interval == "13:30-15:00"
        ]
        self.assertTrue(courses)
        self.assertIn("TI-243", {entry.group for entry in courses})
        self.assertIn("TI-244", {entry.group for entry in courses})

    def test_full_height_course_is_not_marked_odd_week(self):
        entries = parse_pdf(PDF_PATH)
        course_entries = [
            entry for entry in entries
            if entry.day == "Marți"
            and entry.interval == "15:15-16:45"
            and entry.group == "TI-243"
            and entry.content.lower().startswith("c.")
        ]
        self.assertTrue(course_entries)
        self.assertEqual({entry.week for entry in course_entries}, {"Toate"})

    def test_pd_is_only_in_ti242(self):
        entries = parse_pdf(PDF_PATH)
        pd_groups = {
            entry.group for entry in entries
            if entry.day == "Marți"
            and entry.interval == "17:00-18:30"
            and entry.content.startswith("PD")
        }
        self.assertEqual(pd_groups, {"TI-242"})


if __name__ == "__main__":
    unittest.main()
