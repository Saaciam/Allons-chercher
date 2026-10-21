"""Offline checks of truthful tailoring, usable PDFs, and local file protection."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

from pypdf import PdfReader


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from applications import (  # noqa: E402
    analyze_offer, load_offers, load_profile, make_texts, mentions,
    prepare_application, spontaneous_offers, validate_offer,
)
from prepare_applications import main as prepare_main  # noqa: E402


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extracted_pdf(path: Path) -> tuple[PdfReader, str]:
    reader = PdfReader(path)
    return reader, compact(" ".join(page.extract_text() for page in reader.pages))


class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile_path = PROJECT_DIR / "candidate_profile.json"
        cls.offers_path = PROJECT_DIR / "offres.example.json"
        cls.profile = load_profile(cls.profile_path)
        cls.offers = load_offers(cls.offers_path)
        cls.application_date = date(2026, 10, 8)

    def write_json(self, path: Path, value, *, bom: bool = False) -> Path:
        path.write_text(json.dumps(value, ensure_ascii=False),
                        encoding="utf-8-sig" if bom else "utf-8")
        return path

    def test_example_offers_reprioritize_verified_skills_and_letters(self):
        bi, ai = self.offers
        bi_analysis = analyze_offer(self.profile, bi)
        ai_analysis = analyze_offer(self.profile, ai)
        self.assertNotEqual(bi_analysis["ordered_skill_ids"], ai_analysis["ordered_skill_ids"])
        self.assertIn("power-bi", bi_analysis["matched_skill_ids"])
        self.assertNotIn("power-bi", ai_analysis["matched_skill_ids"])
        self.assertIn("cnn", ai_analysis["matched_skill_ids"])
        self.assertNotIn("cnn", bi_analysis["matched_skill_ids"])
        self.assertIn("aucun projet décrit", " ".join(bi_analysis["review_notes"]))
        letters = []
        for offer, analysis in ((bi, bi_analysis), (ai, ai_analysis)):
            letter, email = make_texts(self.profile, offer, analysis, self.application_date)
            self.assertIn(offer["entreprise"], letter)
            self.assertIn(offer["intitule"], letter)
            self.assertIn(offer["entreprise"], email)
            self.assertIn("8 octobre 2026", letter)
            self.assertIn("mars 2027", letter)
            self.assertIn("6 mois", letter)
            letters.append(letter)
        self.assertNotEqual(*letters)
        self.assertIn("Power BI", letters[0])
        self.assertIn("CNN", letters[1])
        self.assertNotIn(ai["entreprise"], letters[0])
        self.assertNotIn(bi["entreprise"], letters[1])

    def test_relevant_projects_change_between_web_and_ai_offers(self):
        web = validate_offer({
            "id": "exemple-web", "entreprise": "Entreprise fictive Web",
            "intitule": "Stage développement web et UI/UX",
            "description": "Figma, responsive design et recueil du besoin client.",
            "date_debut": "2027-03-01", "duree_mois": 6,
        })
        web_analysis = analyze_offer(self.profile, web)
        ai_analysis = analyze_offer(self.profile, self.offers[1])
        self.assertEqual("la-louisette", web_analysis["ordered_project_ids"][0])
        self.assertEqual("traffic-signs", ai_analysis["ordered_project_ids"][0])
        self.assertNotEqual(web_analysis["ordered_project_ids"], ai_analysis["ordered_project_ids"])
        web_letter, _ = make_texts(self.profile, web, web_analysis, self.application_date)
        ai_letter, _ = make_texts(self.profile, self.offers[1], ai_analysis, self.application_date)
        self.assertLess(web_letter.index(self.profile["projects"][3]["title"]),
                        web_letter.index(self.profile["projects"][2]["title"]))
        self.assertIn(self.profile["projects"][0]["title"], ai_letter)
        with tempfile.TemporaryDirectory() as temporary:
            for offer, analysis in ((web, web_analysis), (self.offers[1], ai_analysis)):
                folder = prepare_application(
                    self.profile, self.profile_path, offer, Path(temporary),
                    application_date=self.application_date,
                )
                _, cv_text = extracted_pdf(folder / "CV_CHRISTOPHER_KOUTOUA.pdf")
                titles = {project["id"]: project["title"] for project in self.profile["projects"]}
                positions = [cv_text.index(compact(titles[project_id]))
                             for project_id in analysis["ordered_project_ids"]]
                self.assertEqual(positions, sorted(positions))

    def test_unverified_technologies_are_reported_without_added_claims(self):
        offer = deepcopy(self.offers[1])
        offer["description"] += " Autres outils recherchés : LangChain, RAG, Docker et PyTorch."
        analysis = analyze_offer(self.profile, offer)
        for label in ("LLM / modèles de langage", "IA générative", "LangChain", "RAG", "Docker", "PyTorch"):
            self.assertIn(label, analysis["unverified_technologies"])
        letter, email = make_texts(self.profile, offer, analysis, self.application_date)
        for alias in ("LLM", "modèles de langage", "LangChain", "RAG", "Docker", "PyTorch"):
            self.assertFalse(mentions(letter, alias), alias)
            self.assertFalse(mentions(email, alias), alias)
        before = deepcopy(self.profile)
        with tempfile.TemporaryDirectory() as temporary:
            folder = prepare_application(
                self.profile, self.profile_path, offer, Path(temporary),
                application_date=self.application_date,
            )
            _, cv_text = extracted_pdf(folder / "CV_CHRISTOPHER_KOUTOUA.pdf")
            _, letter_text = extracted_pdf(folder / "Lettre_motivation.pdf")
            for alias in ("LLM", "modèles de langage", "LangChain", "RAG", "Docker", "PyTorch"):
                self.assertFalse(mentions(cv_text, alias), alias)
                self.assertFalse(mentions(letter_text, alias), alias)
        self.assertEqual(before, self.profile)

    def test_pdfs_preserve_contact_availability_and_all_verified_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            for offer in self.offers:
                with self.subTest(offer=offer["id"]):
                    folder = prepare_application(
                        self.profile, self.profile_path, offer, Path(temporary),
                        application_date=self.application_date,
                    )
                    reader, cv_text = extracted_pdf(folder / "CV_CHRISTOPHER_KOUTOUA.pdf")
                    letter_reader, letter_text = extracted_pdf(folder / "Lettre_motivation.pdf")
                    self.assertLessEqual(len(reader.pages), 2)
                    self.assertEqual(len(letter_reader.pages), 1)
                    for group, key in (("skills", "label"), ("projects", "title"),
                                       ("education", "title")):
                        for entry in self.profile[group]:
                            self.assertIn(compact(entry[key]), cv_text)
                    skill_section = cv_text.split("COMPÉTENCES", 1)[1].split("PROJETS ET EXPÉRIENCES", 1)[0]
                    analysis = analyze_offer(self.profile, offer)
                    labels = {skill["id"]: skill["label"] for skill in self.profile["skills"]}
                    priorities = [skill_id for skill_id in analysis["ordered_skill_ids"]
                                  if skill_id in analysis["matched_skill_ids"]]
                    positions = [skill_section.index(compact(labels[skill_id])) for skill_id in priorities]
                    self.assertEqual(positions, sorted(positions))
                    for value in (self.profile["identity"]["display_name"],
                                  self.profile["identity"]["email"],
                                  self.profile["identity"]["phone"],
                                  "Stage de 6 mois", "mars 2027", "Mobilité : France"):
                        self.assertIn(value, cv_text)
                    self.assertIn(self.profile["identity"]["email"], letter_text)
                    self.assertIn(self.profile["identity"]["phone"], letter_text)
                    for paragraph in re.split(r"\n[ \t]*\n", (folder / "lettre_motivation.txt").read_text(encoding="utf-8")):
                        if paragraph.strip():
                            self.assertIn(compact(paragraph), letter_text)
                    links = [annotation.get_object()["/A"]["/URI"]
                             for page in reader.pages for annotation in page.get("/Annots", [])
                             if annotation.get_object().get("/A", {}).get("/URI")]
                    self.assertIn(self.profile["identity"]["linkedin"], links)
                    self.assertIn(self.profile["identity"]["github"], links)

    def test_project_heading_stays_with_start_of_description(self):
        offer = validate_offer({
            "id": "exemple-spontane", "entreprise": "Entreprise fictive",
            "intitule": "Stage Data & Intelligence Artificielle",
            "description": "Candidature spontanée sans offre détaillée.",
            "spontaneous": True,
        })
        with tempfile.TemporaryDirectory() as temporary:
            folder = prepare_application(
                self.profile, self.profile_path, offer, Path(temporary),
                application_date=self.application_date,
            )
            pages = [compact(page.extract_text())
                     for page in PdfReader(folder / "CV_CHRISTOPHER_KOUTOUA.pdf").pages]
            for project in self.profile["projects"]:
                title = compact(project["title"])
                first_words = compact(" ".join(project["description"].split()[:7]))
                heading_page = next(text for text in pages if title in text)
                self.assertIn(first_words, heading_page, title)

    def test_word_boundaries_avoid_java_r_and_metric_false_matches(self):
        cases = (
            ("JavaScript et JavaFX", "Java", False),
            ("Java et SQL", "Java", True),
            ("Entreprise, requêtes et recrutement", "R", False),
            ("Python, R et SQL", "R", True),
            ("F1-score et PR-AUC", "F1-score", True),
            ("F1-score et PR-AUC", "PR-AUC", True),
            ("Diff1-score est une variable", "F1-score", False),
            ("Prétraitement d’images", "pretraitement", True),
        )
        for text, alias, expected in cases:
            with self.subTest(text=text, alias=alias):
                self.assertEqual(mentions(text, alias), expected)
        offer = validate_offer({
            "id": "exemple-lexique", "entreprise": "Entreprise fictive",
            "intitule": "Stage JavaScript",
            "description": "Entreprise recherchant JavaScript ; suivi F1-score et PR-AUC.",
        })
        matched = analyze_offer(self.profile, offer)["matched_skill_ids"]
        self.assertIn("javascript", matched)
        self.assertIn("f1-score", matched)
        self.assertIn("pr-auc", matched)
        self.assertNotIn("java", matched)
        self.assertNotIn("r", matched)

    def test_invalid_offer_ids_cannot_escape_output_directory(self):
        invalid_ids = ("../ailleurs", "..\\ailleurs", "D:\\ailleurs", "/ailleurs",
                       "", "deux mots", "A", "a" * 81)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            for offer_id in invalid_ids:
                with self.subTest(offer_id=offer_id):
                    offer = dict(self.offers[0], id=offer_id)
                    with self.assertRaises(ValueError):
                        prepare_application(self.profile, self.profile_path, offer, output)
            self.assertFalse(output.exists())

    def test_offers_reject_invalid_dates_durations_and_header_injection(self):
        invalid_values = (
            {"date_debut": "2027-02-30"}, {"date_debut": "20270301"},
            {"date_debut": "2027-W09-1"}, {"date_debut": False},
            {"date_debut": []}, {"date_debut": {}}, {"duree_mois": 0},
            {"duree_mois": -1}, {"duree_mois": True}, {"duree_mois": "6"},
            {"entreprise": "Entreprise\nInjected: header"},
            {"intitule": "Stage\r\nInjected: header"},
            {"contact": {"email": "test@example.invalid\r\nBcc: autre@example.invalid"}},
        )
        for overrides in invalid_values:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    validate_offer(dict(self.offers[0], **overrides))

    def test_malformed_profile_fields_fail_with_useful_validation_errors(self):
        replacements = (
            (("schema_version",), True),
            (("identity",), []),
            (("identity", "email"), None),
            (("internship",), None),
            (("internship", "available_from"), "20270301"),
            (("internship", "mobility"), []),
            (("skills", 0), "Python"),
            (("skills", 0, "label"), []),
            (("skills", 0, "aliases"), "python"),
            (("skills", 0, "aliases"), [None]),
            (("skills", 0, "aliases"), [""]),
            (("skills", 0, "aliases"), [" "]),
            (("projects", 0, "description"), None),
            (("projects", 0, "skills"), "python"),
            (("projects", 0, "skills"), [{}]),
            (("education", 0), []),
            (("languages", 0, "level"), False),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "profile.json"
            with self.assertRaises(ValueError):
                load_profile(self.write_json(path, []))
            for fields, replacement in replacements:
                with self.subTest(fields=fields, replacement=replacement):
                    profile = deepcopy(self.profile)
                    container = profile
                    for key in fields[:-1]:
                        container = container[key]
                    container[fields[-1]] = replacement
                    with self.assertRaises(ValueError):
                        load_profile(self.write_json(path, profile))

    def test_date_and_duration_mismatch_are_reported_without_changed_availability(self):
        offer = dict(self.offers[0], date_debut="2027-01-01", duree_mois=4)
        analysis = analyze_offer(self.profile, offer)
        notes = " ".join(analysis["review_notes"])
        self.assertIn("précède", notes)
        self.assertIn("mars 2027", notes)
        self.assertIn("4 mois", notes)
        self.assertIn("6 mois", notes)
        letter, _ = make_texts(self.profile, offer, analysis, self.application_date)
        self.assertIn("6 mois à partir de mars 2027", letter)
        self.assertNotIn("4 mois", letter)
        self.assertNotIn("janvier 2027", letter)

    def test_utf8_bom_loads_profile_and_offers_and_duplicate_offers_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = self.write_json(root / "profile.json", self.profile, bom=True)
            offers = self.write_json(root / "offers.json", self.offers, bom=True)
            self.assertEqual(load_profile(profile), self.profile)
            self.assertEqual(load_offers(offers), self.offers)
            duplicate = self.write_json(root / "duplicate.json", [self.offers[0], self.offers[0]])
            with self.assertRaises(ValueError):
                load_offers(duplicate)
            empty = self.write_json(root / "empty.json", [])
            with self.assertRaises(ValueError):
                load_offers(empty)

    def test_profile_rejects_unknown_project_skill_and_duplicate_entry_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "profile.json"
            unknown = deepcopy(self.profile)
            unknown["projects"][0]["skills"].append("invented-langchain-skill")
            with self.assertRaisesRegex(ValueError, "Compétence inconnue"):
                load_profile(self.write_json(path, unknown))
            for collection in ("skills", "projects"):
                with self.subTest(collection=collection):
                    duplicate = deepcopy(self.profile)
                    duplicate[collection].append(deepcopy(duplicate[collection][0]))
                    with self.assertRaisesRegex(ValueError, "dupliqués"):
                        load_profile(self.write_json(path, duplicate))

    def test_spontaneous_companies_preserve_name_deduplicate_and_reject_slug_collisions(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "companies.txt"
            path.write_text("# commentaire\nEntreprise fictive & Exemple\n\nEntreprise fictive & Exemple\n",
                            encoding="utf-8-sig")
            offers = spontaneous_offers(path)
            self.assertEqual(len(offers), 1)
            self.assertEqual(offers[0]["entreprise"], "Entreprise fictive & Exemple")
            self.assertTrue(offers[0]["spontaneous"])
            path.write_text("Société Exemple\nSociete Exemple\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                spontaneous_offers(path)

    def test_existing_application_is_preserved_until_explicit_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            folder = prepare_application(
                self.profile, self.profile_path, self.offers[0], output,
                application_date=self.application_date,
            )
            letter_path = folder / "lettre_motivation.txt"
            letter_path.write_text("Modification manuelle à conserver.", encoding="utf-8")
            before = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
            with self.assertRaises(ValueError):
                prepare_application(self.profile, self.profile_path, self.offers[0], output)
            self.assertEqual(before, {path.name: path.read_bytes()
                                      for path in folder.iterdir() if path.is_file()})
            prepare_application(
                self.profile, self.profile_path, self.offers[0], output,
                overwrite=True, application_date=self.application_date,
            )
            self.assertIn(self.offers[0]["entreprise"], letter_path.read_text(encoding="utf-8"))
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_profile_sha256"],
                             hashlib.sha256(self.profile_path.read_bytes()).hexdigest())
            self.assertEqual(manifest["source_offer_sha256"],
                             hashlib.sha256((folder / "offre.json").read_bytes()).hexdigest())

    def test_cli_preflights_all_destinations_before_creating_any_application(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "applications"
            existing = output / self.offers[1]["id"]
            existing.mkdir(parents=True)
            sentinel = existing / "manual.txt"
            sentinel.write_text("À préserver", encoding="utf-8")
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                result = prepare_main([
                    "--offers", str(self.offers_path), "--profile", str(self.profile_path),
                    "--output", str(output), "--date", "2026-10-08",
                ])
            self.assertEqual(result, 1)
            self.assertFalse((output / self.offers[0]["id"]).exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "À préserver")

    def test_cli_only_prepares_selected_example_and_rejects_unknown_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "applications"
            arguments = ["--offers", str(self.offers_path), "--profile", str(self.profile_path),
                         "--output", str(output), "--date", "2026-10-08"]
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(prepare_main(arguments + ["--only", "unknown-offer"]), 1)
                self.assertFalse(output.exists())
                self.assertEqual(prepare_main(arguments + ["--only", self.offers[1]["id"]]), 0)
            self.assertEqual([path.name for path in output.iterdir()], [self.offers[1]["id"]])


if __name__ == "__main__":
    unittest.main()
