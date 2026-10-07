"""Filesystem tests for linking Runpod's host cache into ComfyUI models."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest

from src.runpod_model_cache import find_cached_file, link_cached_model


class RunpodModelCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "huggingface-cache" / "hub"
        self.repo = self.cache / "models--Lightricks--LTX-2.5"
        self.filename = "diffusion_models/model.safetensors"
        self.url = (
            "https://huggingface.co/Lightricks/LTX-2.5/resolve/main/"
            + self.filename
        )
        self.output = self.root / "comfy-models" / self.filename
        self.commit = "a" * 40

    def cache_file(self, commit=None, filename=None):
        commit = commit or self.commit
        filename = filename or self.filename
        blob = self.repo / "blobs" / (commit + filename.replace("/", "--"))
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(b"cached weights: " + commit.encode())
        cached = self.repo / "snapshots" / commit / filename
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.symlink_to(blob)
        return cached

    def set_ref(self, commit=None):
        ref = self.repo / "refs" / "main"
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(commit or self.commit)

    def link(self):
        with redirect_stdout(io.StringIO()):
            return link_cached_model(self.url, self.output, self.cache)

    def test_ref_and_blob_layout_links_without_copying_weights(self):
        cached = self.cache_file()
        self.set_ref()
        original_bytes = cached.read_bytes()

        self.assertTrue(self.link())
        self.assertEqual(self.output.readlink(), cached)
        self.assertEqual(self.output.read_bytes(), original_bytes)
        self.assertEqual(self.output.stat().st_ino, cached.stat().st_ino)
        self.assertEqual(cached.read_bytes(), original_bytes)

    def test_cache_takes_precedence_over_existing_download(self):
        cached = self.cache_file()
        self.set_ref()
        self.output.parent.mkdir(parents=True)
        self.output.write_bytes(b"old network-volume download")

        self.assertTrue(self.link())
        self.assertEqual(self.output.readlink(), cached)

    def test_link_is_refreshed_when_cached_revision_changes(self):
        self.cache_file()
        self.set_ref()
        self.assertTrue(self.link())
        newer_commit = "b" * 40
        newer_file = self.cache_file(newer_commit)
        self.set_ref(newer_commit)

        self.assertTrue(self.link())
        self.assertEqual(self.output.readlink(), newer_file)

    def test_dangling_link_from_previous_host_is_replaced(self):
        cached = self.cache_file()
        self.set_ref()
        self.output.parent.mkdir(parents=True)
        self.output.symlink_to(self.root / "missing-host-model")

        self.assertTrue(self.link())
        self.assertEqual(self.output.readlink(), cached)

    def test_pinned_snapshot_without_refs_is_supported(self):
        cached = self.cache_file()
        self.assertEqual(find_cached_file(self.url, self.cache), cached)

    def test_multiple_snapshots_without_ref_are_not_guessed(self):
        self.cache_file()
        self.cache_file("b" * 40)
        self.assertFalse(self.link())
        self.assertFalse(self.output.exists())

    def test_file_missing_from_selected_ref_does_not_use_old_revision(self):
        self.cache_file()
        newer_commit = "b" * 40
        self.cache_file(newer_commit, "vae/another-model.safetensors")
        self.set_ref(newer_commit)
        self.assertFalse(self.link())

    def test_explicit_commit_does_not_follow_main_ref(self):
        cached = self.cache_file()
        self.set_ref("b" * 40)
        url = self.url.replace("/resolve/main/", f"/resolve/{self.commit}/")
        self.assertEqual(find_cached_file(url, self.cache), cached)

    def test_uncached_repository_is_a_miss(self):
        self.cache_file()
        self.set_ref()
        url = self.url.replace("Lightricks/LTX-2.5", "Comfy-Org/gemma-4")
        self.assertIsNone(find_cached_file(url, self.cache))

    def test_missing_cache_preserves_existing_download(self):
        self.output.parent.mkdir(parents=True)
        self.output.write_bytes(b"existing weights")
        self.assertFalse(self.link())
        self.assertEqual(self.output.read_bytes(), b"existing weights")

    def test_dangling_blob_and_empty_weights_are_misses(self):
        cached = self.cache_file()
        self.set_ref()
        cached.resolve().write_bytes(b"")
        self.assertFalse(self.link())
        cached.resolve().unlink()
        self.assertFalse(self.link())

    def test_repeated_startup_keeps_existing_link(self):
        self.cache_file()
        self.set_ref()
        self.assertTrue(self.link())
        link_inode = self.output.lstat().st_ino
        self.assertTrue(self.link())
        self.assertEqual(self.output.lstat().st_ino, link_inode)

    def test_invalid_ref_is_a_miss(self):
        self.cache_file()
        self.set_ref("../../outside-cache")
        self.assertFalse(self.link())


if __name__ == "__main__":
    unittest.main()
