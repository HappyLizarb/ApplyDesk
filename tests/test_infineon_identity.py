"""Bind Infineon's observed application routes without weakening other employer identities."""

import unittest

from applydesk.recommendation_checks import same_infineon_job, validate_employer_page


def captured_form(identity, title, full_company=False):
    """Model the employer’s posting and application routes with synthetic listing details."""
    source = {"company": "Infineon Technologies", "title": title,
              "employer_url": f"https://jobs.infineon.com/careers/job/{identity}?utm_source=linkedin&domain=infineon.com#!source=400"}
    company = "Infineon Technologies Americas Corp." if full_company else "Infineon"
    page = {"url": f"https://jobs.infineon.com/careers/apply?pid={identity}&utm_source=linkedin&domain=infineon.com",
            "title": "Submit application for " + title,
            "text": f"Application Form\nApplication for\n{title}\nOur career page is only for individuals seeking a job at {company}."}
    return source, page


class InfineonIdentityTests(unittest.TestCase):
    """A full employer name or its observed brand still needs the exact official requisition."""

    def test_both_forms_keep_their_exact_job_identity(self):
        for identity, title, full in [("100000000000001", "Fixture Design Engineer", True),
                                      ("100000000000002", "Senior Fixture Design Engineer", False)]:
            source, page = captured_form(identity, title, full)
            self.assertTrue(same_infineon_job(source["employer_url"], page["url"]))
            self.assertIs(validate_employer_page(source, page), page)

    def test_wrong_duplicate_or_missing_requisition_is_rejected(self):
        source, page = captured_form("100000000000001", "Fixture Design Engineer")
        for query in ("pid=100000000000003", "pid=100000000000001&pid=100000000000001", "pid=", ""):
            changed = {**page, "url": "https://jobs.infineon.com/careers/apply?" + query}
            with self.subTest(query=query), self.assertRaises(ValueError):
                validate_employer_page(source, changed)

    def test_host_path_scheme_credentials_and_tenant_cannot_change(self):
        source, page = captured_form("100000000000001", "Fixture Design Engineer")
        changes = [page["url"].replace("jobs.infineon.com", "jobs.example.com"),
                   page["url"].replace("https:", "http:"), page["url"].replace("/careers/apply", "/careers/other"),
                   page["url"].replace("https://", "https://user@"), page["url"].replace(".com/careers", ".com:8443/careers"),
                   page["url"].replace("domain=infineon.com", "domain=other.com"), page["url"] + "&domain=infineon.com"]
        for url in changes:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_employer_page(source, {**page, "url": url})
        self.assertFalse(same_infineon_job(source["employer_url"].replace("domain=infineon.com", "domain=other.com"), page["url"]))

    def test_brand_alias_does_not_accept_a_different_company_or_title(self):
        source, page = captured_form("100000000000001", "Fixture Design Engineer")
        for changes in ({"company": "Other Technologies"}, {"title": "Senior Fixture Design Engineer"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_employer_page({**source, **changes}, page)
        with self.assertRaises(ValueError):
            validate_employer_page(source, {**page, "text": "Application Form"})
        with self.assertRaises(ValueError):
            validate_employer_page(source, {**page, "title": "Single Position", "text": "Infineon"})


if __name__ == "__main__":
    unittest.main()
