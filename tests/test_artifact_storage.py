import asyncio
import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.stub import Stubber

from tests import test_handler_health


R2_ENV = {
    "R2_ACCOUNT_ID": "0123456789abcdef0123456789abcdef",
    "R2_BUCKET_NAME": "generated-artifacts",
    "R2_ACCESS_KEY_ID": "test-r2-access-key",
    "R2_SECRET_ACCESS_KEY": "test-r2-secret-key",
}
R2_ENDPOINT = f"https://{R2_ENV['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com"


class TestArtifactStorage(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.module, _ = test_handler_health.TestHandlerHealth().load_handler()
        self.module.boto3 = boto3
        self.module.Config = Config

    def assert_r2_download(self, url, key):
        parsed = urlsplit(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, urlsplit(R2_ENDPOINT).netloc)
        self.assertEqual(parsed.path, f"/generated-artifacts/{key}")
        params = parse_qs(parsed.query)
        self.assertEqual(params["X-Amz-Algorithm"], ["AWS4-HMAC-SHA256"])
        self.assertEqual(params["X-Amz-Expires"], ["604800"])
        self.assertIn("/auto/s3/aws4_request", params["X-Amz-Credential"][0])
        self.assertNotIn("X-Amz-Security-Token", params)
        self.assertNotIn(R2_ENV["R2_SECRET_ACCESS_KEY"], url)

    def test_r2_ignores_aws_credentials_when_s3_bucket_is_unset(self):
        with patch.dict(os.environ, {
            **R2_ENV, "AWS_BUCKET_NAME": "",
            "AWS_DEFAULT_REGION": "us-east-1", "AWS_SESSION_TOKEN": "unrelated-token",
        }):
            bucket, options = self.module.get_artifact_storage_config()
            self.assertEqual(bucket, "generated-artifacts")
            self.assertEqual(options["endpoint_url"], R2_ENDPOINT)
            self.assertEqual(options["region_name"], "auto")
            self.assertEqual(options["aws_access_key_id"], R2_ENV["R2_ACCESS_KEY_ID"])
            self.assertEqual(options["aws_secret_access_key"], R2_ENV["R2_SECRET_ACCESS_KEY"])
            self.assertEqual(options["aws_session_token"], "")
            self.assertEqual(options["config"].signature_version, "s3v4")
            self.assertEqual(options["config"].s3["addressing_style"], "path")
            self.assertEqual(options["config"].request_checksum_calculation, "when_required")
            self.assertEqual(options["config"].response_checksum_validation, "when_required")
            client = boto3.client("s3", **options)
            self.addCleanup(client.close)
            self.assert_r2_download(client.generate_presigned_url(
                "get_object", Params={"Bucket": bucket, "Key": "renders/job/clip.mp4"},
                ExpiresIn=604800,
            ), "renders/job/clip.mp4")

    def test_s3_bucket_settings_take_precedence_over_complete_or_partial_r2(self):
        for bucket_variable in ("AWS_BUCKET_NAME", "BUCKET_NAME"):
            for r2_settings in (R2_ENV, {"R2_BUCKET_NAME": "r2-renders"}):
                with self.subTest(
                    bucket=bucket_variable, r2=r2_settings
                ), patch.dict(os.environ, {
                    **r2_settings, bucket_variable: "s3-renders",
                }, clear=True):
                    bucket, options = self.module.get_artifact_storage_config()
                self.assertEqual(bucket, "s3-renders")
                # S3 keeps SDK credential/endpoint resolution, without R2 keys.
                self.assertEqual(set(options), {"config"})

    def test_explicit_r2_endpoint_supports_jurisdiction_buckets(self):
        endpoint = R2_ENDPOINT.replace(".r2.", ".eu.r2.")
        settings = {key: value for key, value in R2_ENV.items() if key != "R2_ACCOUNT_ID"}
        with patch.dict(os.environ, {**settings, "R2_ENDPOINT_URL": endpoint}):
            _, options = self.module.get_artifact_storage_config()
        self.assertEqual(options["endpoint_url"], endpoint)
        self.assertEqual(options["region_name"], "auto")

    def test_incomplete_r2_configuration_fails_before_rendering(self):
        for missing in R2_ENV:
            settings = {key: value for key, value in R2_ENV.items() if key != missing}
            with self.subTest(missing=missing), patch.dict(
                os.environ, settings, clear=True
            ):
                self.module.redis_client = AsyncMock()
                with patch.object(self.module, "handle_workflow_job", new=AsyncMock()) as render:
                    response = asyncio.run(self.module.handler({
                        "id": "job", "input": {"workflow": {}},
                    }))
                self.assertEqual(response["status"], "error")
                self.assertIn("R2 uploads require", response["error"])
                self.assertNotIn(R2_ENV["R2_SECRET_ACCESS_KEY"], response["error"])
                render.assert_not_awaited()

    def test_original_runpod_bucket_fields_target_r2(self):
        with patch.dict(os.environ, {
            "BUCKET_NAME": "generated-artifacts", "BUCKET_ENDPOINT_URL": R2_ENDPOINT,
            "BUCKET_ACCESS_KEY_ID": R2_ENV["R2_ACCESS_KEY_ID"],
            "BUCKET_SECRET_ACCESS_KEY": R2_ENV["R2_SECRET_ACCESS_KEY"],
            "AWS_DEFAULT_REGION": "us-east-1",
        }):
            bucket, options = self.module.get_artifact_storage_config()
        self.assertEqual(bucket, "generated-artifacts")
        self.assertEqual(options["endpoint_url"], R2_ENDPOINT)
        self.assertEqual(options["region_name"], "auto")
        self.assertEqual(options["aws_access_key_id"], R2_ENV["R2_ACCESS_KEY_ID"])

    def test_aws_template_fields_accept_r2_endpoint(self):
        with patch.dict(os.environ, {
            "AWS_BUCKET_NAME": "generated-artifacts", "AWS_ENDPOINT_URL": R2_ENDPOINT,
            "AWS_ACCESS_KEY_ID": R2_ENV["R2_ACCESS_KEY_ID"],
            "AWS_SECRET_ACCESS_KEY": R2_ENV["R2_SECRET_ACCESS_KEY"],
        }):
            bucket, options = self.module.get_artifact_storage_config()
            client = boto3.client("s3", **options)
            self.addCleanup(client.close)
            self.assert_r2_download(client.generate_presigned_url(
                "get_object", Params={"Bucket": bucket, "Key": "renders/job.mp4"},
                ExpiresIn=604800,
            ), "renders/job.mp4")

    def test_aws_uses_existing_sdk_credentials_and_region_resolution(self):
        with patch.dict(os.environ, {"AWS_BUCKET_NAME": "aws-renders"}):
            bucket, options = self.module.get_artifact_storage_config()
        self.assertEqual(bucket, "aws-renders")
        self.assertEqual(set(options), {"config"})
        self.assertEqual(options["config"].retries, {"max_attempts": 3, "mode": "standard"})

    def test_non_r2_endpoint_keeps_configured_region(self):
        with patch.dict(os.environ, {
            "AWS_BUCKET_NAME": "aws-renders", "AWS_ENDPOINT_URL": "https://s3.example.com",
            "AWS_DEFAULT_REGION": "eu-west-1",
        }):
            _, options = self.module.get_artifact_storage_config()
        self.assertEqual(options["region_name"], "eu-west-1")

    def test_partial_runpod_settings_do_not_silently_return_inline_artifacts(self):
        for settings in (
            {"BUCKET_ENDPOINT_URL": R2_ENDPOINT},
            {"BUCKET_NAME": "renders", "BUCKET_ACCESS_KEY_ID": "test-key"},
            {"BUCKET_NAME": "renders", "BUCKET_SECRET_ACCESS_KEY": "test-secret"},
        ):
            with self.subTest(settings=settings), patch.dict(os.environ, settings):
                with self.assertRaises(RuntimeError):
                    self.module.get_artifact_storage_config()

    def test_real_sdk_uploads_images_and_videos_and_signs_downloads(self):
        with patch.dict(os.environ, R2_ENV), tempfile.TemporaryDirectory() as tmp:
            bucket, options = self.module.get_artifact_storage_config()
            client = boto3.client("s3", **options)
            self.addCleanup(client.close)
            self.module.COMFY_OUTPUT_DIR = tmp
            # R2 uploads must bypass the inline video limit.
            self.module.MAX_INLINE_VIDEO_MB = 0
            (Path(tmp) / "clip.mp4").write_bytes(b"test-video")
            (Path(tmp) / "preview.png").write_bytes(b"test-image")
            history = {"outputs": {"save-video": {"videos": [{"filename": "clip.mp4"}]},
                                   "save-image": {"images": [{"filename": "preview.png"}]}}}
            with Stubber(client) as stubber, patch.object(
                self.module.boto3, "client", return_value=client
            ):
                stubber.add_response("put_object", {}, {
                    "Bucket": bucket, "Key": "renders/job/00-clip.mp4",
                    "Body": ANY, "ContentType": "video/mp4",
                })
                stubber.add_response("put_object", {}, {
                    "Bucket": bucket, "Key": "renders/job/01-preview.png",
                    "Body": ANY, "ContentType": "image/png",
                })
                output = asyncio.run(self.module.build_workflow_output_payload(history, "job"))
                stubber.assert_no_pending_responses()
            for kind, filename, key in (
                ("videos", "clip.mp4", "renders/job/00-clip.mp4"),
                ("images", "preview.png", "renders/job/01-preview.png"),
            ):
                self.assertEqual(output[kind][0]["type"], "url")
                self.assertEqual(output[kind][0]["filename"], filename)
                self.assert_r2_download(output[kind][0]["data"], key)

    def test_real_sdk_multipart_upload_for_legacy_video_response(self):
        with patch.dict(os.environ, R2_ENV), tempfile.TemporaryDirectory() as tmp:
            bucket, options = self.module.get_artifact_storage_config()
            client = boto3.client("s3", **options)
            self.addCleanup(client.close)
            filepath = Path(tmp) / "clip.mp4"
            filepath.write_bytes(b"v" * (9 * 1024 * 1024))
            self.module.MAX_INLINE_VIDEO_MB = 0
            upload_file = client.upload_file

            def sequential_upload(*args, **kwargs):
                return upload_file(*args, **kwargs, Config=TransferConfig(use_threads=False))

            with Stubber(client) as stubber, patch.object(
                self.module.boto3, "client", return_value=client
            ), patch.object(client, "upload_file", side_effect=sequential_upload):
                stubber.add_response("create_multipart_upload", {"UploadId": "upload-1"}, {
                    "Bucket": bucket, "Key": "renders/job.mp4", "ContentType": "video/mp4",
                })
                for number in (1, 2):
                    stubber.add_response("upload_part", {"ETag": f"etag-{number}"}, {
                        "Bucket": bucket, "Key": "renders/job.mp4", "UploadId": "upload-1",
                        "PartNumber": number, "Body": ANY,
                    })
                stubber.add_response("complete_multipart_upload", {}, {
                    "Bucket": bucket, "Key": "renders/job.mp4", "UploadId": "upload-1",
                    "MultipartUpload": {"Parts": [
                        {"ETag": "etag-1", "PartNumber": 1},
                        {"ETag": "etag-2", "PartNumber": 2},
                    ]},
                })
                result = asyncio.run(self.module.build_result_payload(str(filepath), "job"))
                stubber.assert_no_pending_responses()
            self.assertEqual(set(result), {"video_url"})
            self.assert_r2_download(result["video_url"], "renders/job.mp4")

    def test_failed_upload_retries_then_raises_without_inline_fallback(self):
        client = Mock()
        client.upload_file.side_effect = RuntimeError("Storage unavailable")
        with patch.dict(os.environ, R2_ENV), patch.object(
            self.module.boto3, "client", return_value=client
        ), patch.object(self.module.asyncio, "sleep", new=AsyncMock()) as sleep:
            with self.assertRaisesRegex(RuntimeError, "Storage unavailable"):
                asyncio.run(self.module.build_result_payload("clip.mp4", "job"))
        self.assertEqual(client.upload_file.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [1, 2])
        client.generate_presigned_url.assert_not_called()

    def test_no_storage_keeps_inline_responses_and_video_limit(self):
        self.assertIsNone(self.module.get_artifact_storage_config())
        with tempfile.TemporaryDirectory() as tmp:
            filepath = Path(tmp) / "clip.mp4"
            filepath.write_bytes(b"test-video")
            encoded = base64.b64encode(b"test-video").decode("utf-8")
            result = asyncio.run(self.module.build_result_payload(str(filepath), "job"))
            self.assertEqual(result, {"video_base64": encoded})
            entry = {"filename": "clip.mp4", "media_kind": "video"}
            result = asyncio.run(self.module.build_output_entry(str(filepath), "job", entry, 0))
            self.assertEqual(result["type"], "base64")
            self.assertEqual(result["data"], encoded)
            self.module.MAX_INLINE_VIDEO_MB = 0
            for operation in (
                lambda: self.module.build_result_payload(str(filepath), "job"),
                lambda: self.module.build_output_entry(str(filepath), "job", entry, 0),
            ):
                with self.assertRaisesRegex(RuntimeError, "Configure R2 or S3"):
                    asyncio.run(operation())


if __name__ == "__main__":
    unittest.main()
