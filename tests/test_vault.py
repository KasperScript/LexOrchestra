import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
from vault import VaultManager


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = VaultManager(str(Path(self.temp.name) / "vault"))

    def test_same_second_saves_preserve_both_contents(self):
        with patch("vault.datetime") as clock:
            clock.now.return_value = datetime(2026, 1, 1)
            paths = [self.vault.save_document("Client", "raw", text, label="draft")
                     for text in ("first", "second")]
        self.assertNotEqual(*paths)
        self.assertEqual([Path(p).read_text() for p in paths], ["first", "second"])

    def test_client_traversal_rejected_for_reads_and_writes(self):
        for client in ("../outside", "..", "/tmp/outside", "a/b", "a\\b", ""):
            for action in (self.vault.ensure_client_folder, self.vault.get_client_files,
                           self.vault.list_recent_documents):
                with self.subTest(client=client, action=action.__name__):
                    with self.assertRaises(ValueError):
                        action(client)

    def test_symlink_escape_rejected(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (self.vault.root / "linked").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.vault.save_document("linked", "raw", "secret")
        self.assertEqual(list(outside.iterdir()), [])

    def test_unsafe_document_components_rejected(self):
        for kwargs in ({"doc_type": "../raw"}, {"extension": "txt/../../escape"}):
            with self.assertRaises(ValueError):
                self.vault.save_document("Client", kwargs.get("doc_type", "raw"),
                                         "text", extension=kwargs.get("extension", "txt"))
        with self.assertRaises(ValueError):
            self.vault.read_document("Client", "raw", "../../outside")

    def test_normal_save_read_list(self):
        path = self.vault.save_document("Klient demo", "raw", "content")
        self.assertEqual(self.vault.read_document("Klient demo", "raw", Path(path).name), "content")
        self.assertIn(Path(path).name, self.vault.get_client_files("Klient demo")["raw"])
