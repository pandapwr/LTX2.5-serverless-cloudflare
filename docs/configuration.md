# Configuration

The container is configured through environment variables. The model profile baked into an image is only a startup default; runtime variables can override it.

## Runtime

| Variable | Description | Default |
| --- | --- | --- |
| `RUN_MODE` | `worker`, `local-api`, or `pod`. | `worker` |
| `SERVE_API_LOCALLY` | Legacy fallback to `local-api` when `RUN_MODE` is unset. | `false` |
| `LTX_FRONTEND_ENABLED` | Start the bundled frontend on port `7777`. | `true` |
| `PUBLIC_KEY` | Optional SSH public key. When set, SSH starts in the container. | unset |
| `COMFY_LOG_LEVEL` | ComfyUI log level. | `DEBUG` |
| `LTX25_MODEL_DISCOVERY_CHECK` | Fail startup unless preloaded LTX models are visible through ComfyUI's live model API. | `true` |
| `COMFY_MODEL_DISCOVERY_TIMEOUT_SECONDS` | Maximum time to wait for live model discovery. | `120` |
| `COMFY_MODEL_DISCOVERY_URL` | Local ComfyUI URL used by the startup acceptance check. | `http://127.0.0.1:8188` |

`worker` starts ComfyUI, the frontend, and the RunPod serverless handler. `local-api` replaces the RunPod worker loop with a local compatible API on port `8000`. `pod` starts ComfyUI and the frontend without a handler.

## LTX 2.5 preload

| Variable | Description | Default |
| --- | --- | --- |
| `LTX25_PRELOAD_VARIANT` | Startup model profile. Supported value: `distilled-int8`; empty disables preload. | image default |
| `LTX25_PRELOAD_PROMPT_ENHANCER` | Download the Gemma 4 prompt enhancer when a profile is enabled. | `true` |
| `LTX25_DOWNLOAD_BACKEND` | `auto`, `hf_hub`, or `wget`. | `auto` |
| `LTX25_USE_RUNPOD_CACHE` | Prefer Runpod's mounted Hugging Face cache before existing assets or downloads. | `true` |
| `RUNPOD_HF_CACHE_ROOT` | Root of the mounted Hugging Face hub cache; independent of `HF_HOME`. | `/runpod-volume/huggingface-cache/hub` |
| `HUGGINGFACE_ACCESS_TOKEN` | Hugging Face read token used for gated downloads. `HF_TOKEN` and `HUGGINGFACE_TOKEN` are accepted aliases. | unset |

Accept the [LTX 2.5 model terms](https://huggingface.co/Lightricks/LTX-2.5) before first boot. A token is required while the gated files are missing; an already-populated persistent volume does not need to redownload them.

Recommended serverless values:

```env
PERSIST_WORKSPACE=true
RUN_MODE=worker
COMFY_NODES=127.0.0.1:8188
LTX25_PRELOAD_VARIANT=distilled-int8
LTX25_PRELOAD_PROMPT_ENHANCER=true
HUGGINGFACE_ACCESS_TOKEN=hf_xxx
```

### Runpod cached models

In the Serverless endpoint's **Model / cached model** field, select
`Lightricks/LTX-2.5` and provide a Hugging Face read token with accepted model
terms. Use an image rebuilt with this cache support.

Startup resolves the cache's `models--Lightricks--LTX-2.5/snapshots/<commit>/`
directory and symlinks the required weights into ComfyUI's model folders. It
prefers cached files even when downloaded copies already exist on a network
volume. No large model files are copied and the mounted cache is never modified.
Links are refreshed at startup for the mounted revision. Missing files fall back
to existing assets or the configured download backend, including recovery from
dangling cache links left by a previous worker.

With **no attached network volume**, keep the runtime workspace inside the image:

```env
RUN_MODE=worker
PERSIST_WORKSPACE=false
LTX25_PRELOAD_VARIANT=distilled-int8
LTX25_USE_RUNPOD_CACHE=true
LTX25_PRELOAD_PROMPT_ENHANCER=true
HUGGINGFACE_ACCESS_TOKEN=hf_xxx
```

With an attached network volume, leave `PERSIST_WORKSPACE=true` to retain ComfyUI,
the Python environment, workflows, and files downloaded on a cache miss. Cached
weights still load directly from the host cache. Selecting a cached model does
not create a persistent network volume, despite the shared `/runpod-volume` path.

The Gemma prompt enhancer is hosted separately in `Comfy-Org/gemma-4`, so it still
downloads when only the LTX repository is cached. Keep the worker's token
configured for any authenticated fallback downloads. `HF_HUB_OFFLINE` should
remain unset when these downloads are needed.

Runpod currently documents one cached repository per endpoint and downloads all
model variants in that repository. See [Runpod cached models](https://docs.runpod.io/serverless/endpoints/model-caching).
For an ambiguous cache with several snapshots and no ref, the worker uses the
download fallback rather than guessing a revision. Set
`LTX25_USE_RUNPOD_CACHE=false` to retain the existing asset/download behavior.

## Persistent workspace

| Variable | Description | Default |
| --- | --- | --- |
| `PERSIST_WORKSPACE` | Persist ComfyUI, the venv, caches, workflows, and models. | `true` |
| `WORKSPACE_ROOT` | Override the detected persistent root. | `/workspace` when available |
| `WORKSPACE_STATE_ROOT` | Persisted ComfyUI/venv/cache state directory. | `<WORKSPACE_ROOT>/worker-comfyui` |
| `COMFY_BOOTSTRAP_REFRESH_CUSTOM_NODES` | Comma-separated baked node directories refreshed during bootstrap. | `ComfyUI-Downloader` |
| `COMFY_BOOTSTRAP_WORKFLOWS` | Comma-separated editor-format workflows copied into the persisted ComfyUI user directory. | `video_ltx2_5_i2v.json` |
| `BOOTSTRAP_PROGRESS_HEARTBEAT_SECONDS` | Interval for long seed-operation progress logs. | `15` |
| `BOOTSTRAP_LOCK_TIMEOUT_SECONDS` | Maximum wait for the shared bootstrap lock. | `600` |
| `BOOTSTRAP_LOCK_POLL_SECONDS` | Shared-lock polling interval. | `2` |
| `BOOTSTRAP_LOCK_STALE_SECONDS` | Age at which an unrefreshed lock is considered stale. | `120` |
| `BOOTSTRAP_LOCK_HEARTBEAT_SECONDS` | Lock timestamp refresh interval. | `5` |

On Serverless, RunPod mounts a network volume at `/runpod-volume`; startup aliases it to `/workspace`. Multiple workers sharing a volume coordinate first-boot seeding with `/workspace/worker-comfyui/.bootstrap.lock`.

| Purpose | Path |
| --- | --- |
| ComfyUI code and user state | `/workspace/worker-comfyui/comfyui` |
| Python virtualenv | `/workspace/worker-comfyui/venv` |
| Download/compiler caches | `/workspace/worker-comfyui/cache` |
| Models | `/workspace/models`, linked at `/comfyui/models` |
| Handler input/output | `/comfyui/input`, `/comfyui/output` |
| Extra model path configuration | `/comfyui/extra_model_paths.yaml` |

ComfyUI Manager is forced to offline mode at every boot. Install custom nodes in the image; runtime Manager installs are intentionally unavailable.

The handler and bundled frontend execute `/video_ltx2_5_i2v_API.json`. ComfyUI's user workflow library receives the separate editor-format `/video_ltx2_5_i2v.json`; bootstrap removes the older API-format file from that library during migration.

## Handler and ComfyUI

| Variable | Description | Default |
| --- | --- | --- |
| `COMFY_NODES` | Comma-separated ComfyUI API hosts used by the handler. | `127.0.0.1:8188` |
| `LOCAL_COMFY_NODE` | ComfyUI host used by the bundled frontend. | `127.0.0.1:8188` |
| `COMFY_INPUT_DIR` | Uploaded workflow input staging directory. | `/comfyui/input` |
| `COMFY_OUTPUT_DIR` | Generated artifact pickup directory. | `/comfyui/output` |
| `COMFYUI_MANAGER_CONFIG` | Manager `config.ini` updated during startup. | `/comfyui/user/__manager/config.ini` |
| `REDIS_URL` | Local Redis only. Leave unset; external servers and URL options are rejected. Accepts `127.0.0.1` or `localhost` on port `6379`, with no path, `/`, or `/0`; always connects to `127.0.0.1`. | `redis://127.0.0.1:6379` |
| `CACHE_TTL_SECONDS` | Successful response cache lifetime in seconds. | `604800` |
| `MAX_INLINE_VIDEO_MB` | Maximum inline video response size before R2 or S3 uploads become mandatory. | `50` |
| `INDRO_API_KEY` | Authentication for the legacy `prompt` + `image_url` path only. | `dev_token_123` |

## Redis and cached results

Redis is the worker's temporary job notebook. In `worker` and `local-api` modes, startup launches it inside the container on `127.0.0.1:6379`, with protected mode enabled and disk persistence disabled. Leave `REDIS_URL` unset; no external Redis service is needed. External addresses, credentials, query options, alternate ports, and nonzero databases are rejected without printing the supplied URL. Direct handler startup, including Hub validation, also enforces the local address. `pod` mode skips Redis entirely.

The worker stores the following in Redis:

- Job status and progress, duplicate-job locks, and temporarily unavailable ComfyUI nodes.
- Completed responses for `CACHE_TTL_SECONDS` (seven days by default). These contain the actual base64-encoded artifacts when uploads are disabled, or download links when R2 or S3 is enabled. Cached links keep their original expiry; cache hits do not renew them.
- A request counter for the legacy API. Authentication happens before counting, and the counter name contains no API key.

This state belongs to one worker: caching, deduplication, and rate limits are not coordinated across workers. Restarting the bundled Redis process or replacing its container loses that state. This does not delete generated files, R2/S3 objects, or the model and compiler caches on persistent storage.

The local connection restriction prevents the worker from sending this state to an external Redis provider. Pod and host administrators can still inspect local processes. Data sent to an external Redis server by an older image remains there until separately removed; see [upgrading from external Redis](deployment.md#upgrading-from-external-redis).

## Artifact uploads

The serverless handler (`worker` and `local-api` modes) can upload generated images and videos to Cloudflare R2, AWS S3, or another S3-compatible endpoint using `boto3`. It returns presigned GET URLs valid for seven days. Uploads preserve the media `Content-Type` and use multipart transfers for large files. Upload failures fail the job after retries instead of silently returning inline data.

When no artifact storage is configured, responses contain base64 data. Partial R2 configuration and endpoint settings without a bucket fail before rendering. The interactive pod frontend and direct ComfyUI jobs serve local files; they do not pass through the handler's artifact uploader.

### Choose a provider during RunPod setup

The RunPod template includes separate AWS S3 and Cloudflare R2 fields under advanced settings. Provider selection follows the supplied environment variables; no separate provider selector is required.

| Provider | Fields to fill in |
| --- | --- |
| AWS S3 | `AWS_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_DEFAULT_REGION`. Leave all `R2_*` fields blank. |
| Cloudflare R2 | `R2_BUCKET_NAME`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and either `R2_ACCOUNT_ID` or `R2_ENDPOINT_URL`. Leave the AWS artifact fields blank. |
| Inline responses | Leave both providers' bucket, endpoint, and credential fields blank. |

S3 takes precedence if `AWS_BUCKET_NAME` or the legacy `BUCKET_NAME` is set, even when R2 settings are also present. To use the dedicated R2 fields, leave both S3 bucket fields blank. These fields require the updated template and a container image built with this storage support; existing deployed images and templates must be updated separately.

### Cloudflare R2

1. Create an R2 bucket, such as `generated-artifacts`.
2. Create an [R2 API token](https://developers.cloudflare.com/r2/api/tokens/) with **Object Read & Write** permission limited to that bucket. Copy the generated S3 **Access Key ID** and **Secret Access Key**; these are not the Cloudflare bearer API token.
3. Use a container image built with R2 support. For a custom build, build for `linux/amd64`, publish it to your registry, and replace the image on your RunPod template.
4. Set these worker environment variables, storing the credentials as RunPod secrets:

```env
R2_ACCOUNT_ID=<cloudflare-account-id>
R2_BUCKET_NAME=generated-artifacts
R2_ACCESS_KEY_ID=<r2-s3-access-key-id>
R2_SECRET_ACCESS_KEY=<r2-s3-secret-access-key>
```

| Variable | Description |
| --- | --- |
| `R2_BUCKET_NAME` | Private R2 bucket used for generated artifacts. |
| `R2_ACCOUNT_ID` | Constructs `https://<account-id>.r2.cloudflarestorage.com`. Required unless `R2_ENDPOINT_URL` is set. |
| `R2_ENDPOINT_URL` | Optional full S3 API endpoint from the R2 dashboard. Overrides `R2_ACCOUNT_ID`; use this for jurisdiction-specific endpoints such as `https://<account-id>.eu.r2.cloudflarestorage.com`. |
| `R2_ACCESS_KEY_ID` | R2 S3 access key ID with bucket-scoped read/write access. |
| `R2_SECRET_ACCESS_KEY` | Matching R2 S3 secret access key. |

When both `AWS_BUCKET_NAME` and `BUCKET_NAME` are unset, any nonempty `R2_*` setting selects R2 mode; the bucket, endpoint/account ID, and both R2 keys must be supplied. S3 bucket settings take precedence over R2 settings, including incomplete R2 settings. The R2 client uses region `auto`, Signature Version 4, and path-style addressing. Unrelated AWS session credentials are ignored in R2 mode. Blank template defaults do not enable R2.

Use the S3 API endpoint, not an `r2.dev` URL or public custom domain. [R2 presigned URLs](https://developers.cloudflare.com/r2/api/s3/presigned-urls/) authorize access through the S3 endpoint; making the bucket public is unnecessary. Anyone holding a signed URL can read that object until it expires. Keep credentials on the RunPod worker and any signing backend, never in browser code or job input.

### Existing AWS and RunPod template fields

You can also keep the AWS fields in an existing RunPod template and add an endpoint override:

```env
AWS_BUCKET_NAME=generated-artifacts
AWS_ACCESS_KEY_ID=<r2-s3-access-key-id>
AWS_SECRET_ACCESS_KEY=<r2-s3-secret-access-key>
AWS_ENDPOINT_URL=https://<cloudflare-account-id>.r2.cloudflarestorage.com
```

The original RunPod `BUCKET_NAME`, `BUCKET_ENDPOINT_URL`, `BUCKET_ACCESS_KEY_ID`, and `BUCKET_SECRET_ACCESS_KEY` fields are supported too. `AWS_BUCKET_NAME` takes precedence over `BUCKET_NAME`, and `AWS_ENDPOINT_URL` over `BUCKET_ENDPOINT_URL`. A nonempty legacy key pair is passed explicitly; otherwise boto3 resolves the usual AWS credentials. Always provide both legacy keys together. For R2 through these aliases, clear any unrelated `AWS_SESSION_TOKEN` when using the AWS credential fields.

R2 endpoint hosts select region `auto`, even if the existing template defaults to `us-east-1`. Other custom endpoints use `AWS_DEFAULT_REGION`, `AWS_REGION`, or `BUCKET_REGION`, defaulting to `us-east-1`. When no custom endpoint is set, AWS retains boto3's normal endpoint, credential, and region resolution.

### AWS S3

| Variable | Description |
| --- | --- |
| `AWS_BUCKET_NAME` | Bucket used for generated artifacts. Enables S3 mode. |
| `AWS_ACCESS_KEY_ID` | AWS access key ID with `s3:PutObject` and `s3:GetObject` access. |
| `AWS_SECRET_ACCESS_KEY` | Matching secret access key. |
| `AWS_DEFAULT_REGION` | Bucket region. |
| `AWS_ENDPOINT_URL` | Optional S3-compatible API endpoint. Leave unset for ordinary AWS S3. |

### Using artifact results in an application

An application's backend submits a job to RunPod, then receives an artifact URL in each `output.images[]` or `output.videos[]` entry's `data` field with `type: "url"`. The legacy single-video route returns `video_url`. The response shape is unchanged.

Workflow object keys are `renders/<job-id>/<two-digit-index>-<filename>`; the legacy route uses `renders/<job-id>.mp4`. Objects remain stored after the returned URL expires. For access beyond seven days, retain the bucket/object key and let the application's backend generate a fresh URL or serve the object through a Cloudflare Worker with an [R2 binding](https://developers.cloudflare.com/r2/get-started/workers-api/) to the same bucket.

If browser code fetches the signed URL directly, configure [R2 CORS](https://developers.cloudflare.com/r2/buckets/cors/) for the application's actual origin. For example, replace the origin below in the bucket's CORS policy:

```json
[
  {
    "AllowedOrigins": ["https://app.example.com"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["Range"],
    "ExposeHeaders": ["ETag", "Accept-Ranges", "Content-Range"],
    "MaxAgeSeconds": 3600
  }
]
```

R2 stores generated artifacts; the GPU still runs on RunPod and the attached network volume still holds models and runtime caches.

Example workflow response:

```json
{
  "status": "success",
  "output": {
    "videos": [
      {
        "filename": "LTX-2.5_i2v.mp4",
        "type": "url",
        "data": "https://<account-id>.r2.cloudflarestorage.com/generated-artifacts/renders/job-123/00-LTX-2.5_i2v.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&...",
        "media_type": "video/mp4"
      }
    ]
  },
  "metadata": {
    "render_time_sec": 42.1,
    "node_used": "127.0.0.1:8188"
  }
}
```
