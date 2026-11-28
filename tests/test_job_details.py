"""Job-detail parsing copies explicit wording and rejects unrelated numeric facts."""

import unittest

from applydesk.job_details import parse_job_details


class JobSectionTests(unittest.TestCase):
    def test_preferred_parenthetical_and_mixed_qualifications_are_separate(self):
        for text in ("Required Qualifications\nPython is required.\nPreferred Qualifications (Nice to Have)\nRust experience is preferred.",
                     "Qualifications\nPython is required.\nRust experience is preferred."):
            sections = parse_job_details(text)["detail_sections"]
            self.assertEqual(sections["required_qualifications"], "Python is required.")
            self.assertEqual(sections["preferred_qualifications"], "Rust experience is preferred.")

    def test_missing_text_has_stable_empty_fields(self):
        for text in (None, "", "Unlabelled opening prose about an interesting role."):
            result = parse_job_details(text)
            self.assertEqual(set(result["detail_sections"]), {
                "responsibilities", "about", "required_qualifications", "preferred_qualifications", "why_join_us"})
            self.assertFalse(any(result["detail_sections"].values()))
            self.assertEqual(result["compensation"], "")

    def test_nested_required_preferred_and_later_responsibilities(self):
        text = "Qualifications\n\nRequired\n\nPython\n\nPreferred\n\nSQL\n\nResponsibilities\n\nBuild reliable services."
        sections = parse_job_details(text)["detail_sections"]
        self.assertEqual(sections["required_qualifications"], "Python")
        self.assertEqual(sections["preferred_qualifications"], "SQL")
        self.assertEqual(sections["responsibilities"], "Build reliable services.")

    def test_nested_inline_labels(self):
        sections = parse_job_details("What you bring: Required: Python Preferred: SQL")["detail_sections"]
        self.assertEqual(sections["required_qualifications"], "Python")
        self.assertEqual(sections["preferred_qualifications"], "SQL")

    def test_repeated_sections_preserve_words_and_paragraphs_after_marker_removal(self):
        first = "• Build systems.\n\nDocument decisions,  exactly as written."
        text = "Responsibilities:\n\n" + first + "\n\nResponsibilities: Review changes."
        expected = "Build systems.\n\nDocument decisions,  exactly as written.\n\nReview changes."
        self.assertEqual(parse_job_details(text)["detail_sections"]["responsibilities"], expected)

    def test_tiktok_nested_about_and_job_information_boundaries(self):
        text = ("About the job\nResponsibilities\nAbout the Team\n\nWe support our customers.\n\n"
                "Responsibilities:\n\nBuild dashboards.\n\nQualifications\nMinimum Qualifications:\n\nSQL skills.\n\n"
                "Preferred Qualifications:\n\nPython skills.\n\nJob Information\n\nUnrelated legal details.\n\n"
                "About TikTok\n\nOur mission is to inspire creativity.\n\nWhy Join Us\n\nLearn together.\n\n"
                "Diversity & Inclusion\n\nLegal statement.\n\nTikTok Accommodation\n\nContact instructions.")
        sections = parse_job_details(text)["detail_sections"]
        self.assertEqual(sections["about"], "We support our customers.\n\nOur mission is to inspire creativity.")
        self.assertEqual(sections["required_qualifications"], "SQL skills.")
        self.assertEqual(sections["preferred_qualifications"], "Python skills.")
        self.assertEqual(sections["why_join_us"], "Learn together.")
        self.assertEqual(sections["responsibilities"], "Build dashboards.")

    def test_application_and_privacy_paragraphs_stop_sections(self):
        text = ("Preferred Qualifications\nPython.\n\nBy submitting an application for this role, you agree to our privacy policy.\n"
                "Uncategorized paragraph.\n\nBenefits\nInsurance.\n\nWe are an equal opportunity employer.\n"
                "Privacy Notice\nDo not copy this.\nHow to apply\nEmail us.")
        sections = parse_job_details(text)["detail_sections"]
        self.assertEqual(sections["preferred_qualifications"], "Python.")
        self.assertEqual(sections["why_join_us"], "Insurance.")

    def test_unknown_clear_heading_ends_section_without_guessing(self):
        text = "Responsibilities\nBuild tools.\n\nInterview process\nThree rounds.\n\nAbout us\nWe make tools."
        sections = parse_job_details(text)["detail_sections"]
        self.assertEqual(sections["responsibilities"], "Build tools.")
        self.assertEqual(sections["about"], "We make tools.")

    def test_title_cased_responsibility_is_not_discarded_as_unknown_heading(self):
        body = "Build Reliable Systems\n• Support Product Development\nDocument Team Decisions"
        expected = "Build Reliable Systems\nSupport Product Development\nDocument Team Decisions"
        self.assertEqual(parse_job_details("Responsibilities\n" + body)["detail_sections"]["responsibilities"], expected)

    def test_urban_heading_variants_and_unrelated_tail(self):
        text = ("Company Description: We are a small business.\n\nJob Summary:\nSupport engineers.\n\n"
                "Job duties consist of but are not limited to:\n• Deploy equipment.\n\nRequirements and Qualifications:\n"
                "• Engineering degree.\n\nUrban Culture:\nWork together.\n\nBenefits you will enjoy at Urban:\nInsurance.\n\n"
                "Benefits found in job post\nDuplicated badges.\n\nRequirements added by the job poster\nExtra badge.")
        sections = parse_job_details(text)["detail_sections"]
        self.assertEqual(sections["about"], "We are a small business.\n\nSupport engineers.")
        self.assertEqual(sections["responsibilities"], "Deploy equipment.")
        self.assertEqual(sections["required_qualifications"], "Engineering degree.")
        self.assertEqual(sections["why_join_us"], "Work together.\n\nInsurance.")

    def test_metadata_and_policy_are_not_preferred_qualifications(self):
        text = ("Qualifications & Experience\nRequired\nEngineering degree.\nPreferred\nSolar experience.\n\n"
                "Travel: Up to 20%\n\nWe are a proud equal opportunity employer.\n\nPlease fill out the form below to apply.")
        sections = parse_job_details(text)["detail_sections"]
        self.assertEqual(sections["preferred_qualifications"], "Solar experience.")
        self.assertEqual(sections["required_qualifications"], "Engineering degree.")


class JobBulletTests(unittest.TestCase):
    def test_list_prefixes_removed_before_heading_and_body_parsing(self):
        prefixes = ("• ", "· ", "∙ ", "● ", "▪ ", "◦ ", "‣ ", "\uf0b7 ", "\uf0a7 ",
                    "- ", "* ", "1. ", "2) ", "• · ", "2) • ")
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                source = f"  {prefix}Responsibilities\n\t{prefix}Build tools.\n\n  {prefix}Review code."
                actual = parse_job_details(source)["detail_sections"]["responsibilities"]
                self.assertEqual(actual, "Build tools.\n\nReview code.")

    def test_negative_values_decimals_and_internal_punctuation_remain(self):
        body = ("-5°C calibration tolerance.\n-$500 accounting adjustment.\n1.5 years of experience.\n"
                "Use C++ and A*; x - y and 2 * 3 stay literal.\n*Important* wording remains.")
        self.assertEqual(parse_job_details("Responsibilities\n" + body)["detail_sections"]["responsibilities"], body)

    def test_pay_prefixes_removed_without_changing_wording_or_ranges(self):
        for prefix in ("· ", "\uf0b7 ", "- ", "* ", "1. ", "• · "):
            with self.subTest(prefix=prefix):
                first = "Salary: $80,000–$105,000 per year."
                context = "Total compensation also includes bonus and equity."
                source = f"{prefix}{first}\n{prefix}{context}"
                self.assertEqual(parse_job_details(source)["compensation"], first + "\n" + context)


class JobFormatTests(unittest.TestCase):
    def test_heading_punctuation_and_sentence_case_preserve_body(self):
        cases = (
            ("1. Responsibilities\nBuild tools.", "responsibilities", "Build tools."),
            ("2) Minimum Qualifications\nPython.", "required_qualifications", "Python."),
            ("**Responsibilities**\nBuild tools.", "responsibilities", "Build tools."),
            ("**Requirements:** Python.", "required_qualifications", "Python."),
            ("**Preferred Qualifications**: SQL.", "preferred_qualifications", "SQL."),
            ("Responsibilities：Build tools.", "responsibilities", "Build tools."),
            ("Required Qualifications — Python.", "required_qualifications", "Python."),
            ("Preferred Qualifications – SQL.", "preferred_qualifications", "SQL."),
            ("What you'll do:\nBuild tools.", "responsibilities", "Build tools."),
            ("What you’ll bring:\nPython.", "required_qualifications", "Python."),
            ("Nice-to-haves:\nSQL.", "preferred_qualifications", "SQL."),
            ("Company overview:\nWe build tools.", "about", "We build tools."),
        )
        for source, section, body in cases:
            with self.subTest(source=source):
                self.assertEqual(parse_job_details(source)["detail_sections"][section], body)

    def test_multiple_inline_labels_remain_separate(self):
        cases = (
            "Responsibilities: Build tools. Required Qualifications: Python. Preferred Qualifications: SQL.",
            "Responsibilities：Build tools. Required Qualifications：Python. Preferred Qualifications：SQL.",
            "Responsibilities — Build tools. Required Qualifications – Python. Preferred Qualifications — SQL.",
            "**Responsibilities:** Build tools. **Required:** Python. **Preferred:** SQL.",
        )
        for source in cases:
            with self.subTest(source=source):
                sections = parse_job_details(source)["detail_sections"]
                self.assertEqual(sections["responsibilities"], "Build tools.")
                self.assertEqual(sections["required_qualifications"], "Python.")
                self.assertEqual(sections["preferred_qualifications"], "SQL.")

    def test_unknown_explicit_heading_stops_previous_section(self):
        source = ("## Responsibilities\nBuild tools.\n## Selection timeline\nThree rounds.\n"
                  "## About us\nWe build tools.")
        sections = parse_job_details(source)["detail_sections"]
        self.assertEqual(sections["responsibilities"], "Build tools.")
        self.assertEqual(sections["about"], "We build tools.")
        self.assertFalse(any(parse_job_details("## Selection timeline\nThree rounds.")["detail_sections"].values()))


class JobCompensationTests(unittest.TestCase):
    def test_budget_duties_and_year_ranges_cannot_become_pay(self):
        self.assertEqual(parse_job_details("Responsibilities\nManage compensation budgets of $2 million.")["compensation"], "")
        text = "Compensation\nCompetitive pay.\n\nResearch history\nPublished in 2024–2025."
        self.assertEqual(parse_job_details(text)["compensation"], "Compensation\nCompetitive pay.")
        self.assertEqual(parse_job_details("Compensation\nPublished in 2024–2025.")["compensation"], "")
        self.assertEqual(parse_job_details("Pay: Unpaid")["compensation"], "Pay: Unpaid")

    def test_exact_total_compensation_keeps_base_benefits_explanation(self):
        pay = ("Total Compensation Package: $110,000-$150,000 annually. This estimate includes base compensation, "
               "median retirement fund contributions, and employer-paid health and related benefits. "
               "The actual total will vary based on individual enrollment choices.")
        text = "Qualifications:\nEngineering degree.\n\n" + pay + "\n\nBenefits: Generous benefits package includes:\n401(k) retirement plan with 5% match"
        result = parse_job_details(text)
        self.assertEqual(result["compensation"], pay)
        self.assertEqual(result["detail_sections"]["required_qualifications"], "Engineering degree.")

    def test_multiple_location_periods_are_not_averaged_or_annualized(self):
        text = "California pay: $35/hour.\n\nNew York annual salary: USD 80,000–105,000."
        self.assertEqual(parse_job_details(text)["compensation"], text)

    def test_salary_context_is_preserved_across_paragraphs(self):
        text = "Base salary\n\n80k–105k annually.\n\nBenefits\n401(k) match."
        self.assertEqual(parse_job_details(text)["compensation"], "Base salary\n\n80k–105k annually.")

    def test_compact_description_does_not_become_compensation(self):
        text = "Responsibilities\nBuild tools.\nRequired Qualifications\nPython.\nSalary: $95,000 per year."
        self.assertEqual(parse_job_details(text)["compensation"], "Salary: $95,000 per year.")
        self.assertEqual(parse_job_details(text)["detail_sections"]["required_qualifications"], "Python.")

    def test_unlabelled_pay_line_excludes_previous_compact_description_lines(self):
        text = "Responsibilities\nBuild tools.\nThe salary is $95,000 per year.\nTotal compensation includes bonus and equity."
        expected = "The salary is $95,000 per year.\nTotal compensation includes bonus and equity."
        self.assertEqual(parse_job_details(text)["compensation"], expected)

    def test_pay_paragraph_keeps_bonus_and_total_context(self):
        text = "Base salary is $80,000–$105,000 per year. Total compensation also includes bonus and equity; amounts vary by location."
        self.assertEqual(parse_job_details(text)["compensation"], text)

    def test_competitive_pay_requires_explicit_compensation_section(self):
        self.assertEqual(parse_job_details("We offer competitive pay.")["compensation"], "")
        self.assertEqual(parse_job_details("Compensation\n\nCompetitive pay.")["compensation"], "Compensation\n\nCompetitive pay.")

    def test_benefit_percentages_years_and_project_budgets_are_not_pay(self):
        text = ("Company Overview\nOur projects secured billions in financing.\n\nRequired\n3–5 years of experience.\n"
                "Manage a $2 million project budget.\n\nBenefits\n401(k) retirement plan with 6% match.\n"
                "10 paid holidays per year.\n\nFounded in 1995; hiring 2027 graduates.")
        self.assertEqual(parse_job_details(text)["compensation"], "")

    def test_pay_period_currency_and_zero_formats_remain_verbatim(self):
        cases = (
            "Annual Compensation Range: $80,000 - $105,000",
            "The hourly rate range for this position in the selected city is $35- $35.",
            "Pay: £45,000–£55,000 per year.", "Salary: EUR 5,000 per month.", "$20/hr - $30/hr",
            "Hourly pay: $0–$18/hour.",
            "Pay: £20–£25 per hour.",
            "Salary: USD 4,500–5,500 per month.",
            "Annual salary – AUD 90,000–110,000 per year.",
            "Compensation：€55,000–€65,000/year.",
            "Salary: $40/hour – $50/hour.",
            "Pay: 25–30 USD/hour.",
            "Monthly salary: ¥300,000–¥450,000.",
        )
        for pay in cases:
            with self.subTest(pay=pay):
                self.assertEqual(parse_job_details(pay)["compensation"], pay)

    def test_ambiguous_amounts_and_non_pay_ranges_stay_empty(self):
        cases = (
            "Target range: 80–105.",
            "Graduation years: 2026–2027.",
            "Benefits: 401(k) with 6% match.",
            "Annual revenue: USD 4,000,000.",
            "Client project budget: $2,000,000.",
            "## Selection timeline\n2026–2027 hiring cycle.",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(parse_job_details(source)["compensation"], "")


if __name__ == "__main__":
    unittest.main()
