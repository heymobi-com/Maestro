"""A missing model variant must be confirmed, and must name what is installed."""

from __future__ import annotations

import os
import sys
import unittest


APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from services.model_downloads import describe_download, should_confirm_missing_variant  # noqa: E402


class DownloadConfirmationMessageTests(unittest.TestCase):
    def test_an_installed_variant_is_named_as_the_alternative(self):
        info = describe_download(
            model_name="Qwen Image 2.1 7B",
            download_filename="qwen_image_21_7B_bf16.safetensors",
            quantization="bf16",
            local_alternatives=[
                {"filename": "qwen_image_21_7B_int8_convrot.safetensors", "size_gb": 6.76},
            ],
        )

        self.assertIn("qwen_image_21_7B_bf16.safetensors", info["message"])
        self.assertIn("You already have", info["message"])
        self.assertIn("6.8 GB", info["message"])
        self.assertIn("Transformer Quantization", info["message"])
        self.assertEqual(info["local_alternatives"][0]["size_gb"], 6.76)
        self.assertEqual(info["quantization"], "bf16")

    def test_a_first_download_says_it_takes_minutes(self):
        info = describe_download(
            model_name="LTX-2.5 22B",
            download_filename="ltx2_25_int8.safetensors",
            quantization="int8",
        )

        self.assertIn("first download", info["message"])
        self.assertEqual(info["local_alternatives"], [])

    def test_malformed_alternatives_are_ignored(self):
        info = describe_download(
            model_name="Model",
            download_filename="wanted.safetensors",
            quantization="int8",
            local_alternatives=[{"size_gb": 4.0}, "nonsense", {"filename": ""}],
        )

        self.assertEqual(info["local_alternatives"], [])
        self.assertIn("first download", info["message"])

    def test_a_missing_variant_is_always_worth_asking(self):
        self.assertTrue(should_confirm_missing_variant([]))
        self.assertTrue(
            should_confirm_missing_variant(
                [{"filename": "other_int8.safetensors", "size_gb": 6.0}],
            )
        )


if __name__ == "__main__":
    unittest.main()
