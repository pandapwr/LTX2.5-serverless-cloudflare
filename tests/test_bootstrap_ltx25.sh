#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_TO_TEST="${SCRIPT_TO_TEST:-${REPO_ROOT}/src/bootstrap_ltx25.sh}"

TEST_DIR="$(mktemp -d)"
trap 'rm -rf "${TEST_DIR}"' EXIT

BIN_DIR="${TEST_DIR}/bin"
WGET_LOG_FILE="${TEST_DIR}/wget.log"
MODEL_ROOT="${TEST_DIR}/models"
mkdir -p "${BIN_DIR}"
ln -s "$(command -v python3)" "${BIN_DIR}/python"

cat > "${BIN_DIR}/wget" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
output_path=""
url=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -O) output_path="$2"; shift 2 ;;
        --header=*) printf 'header:%s\n' "${1#--header=}" >> "${WGET_LOG_FILE}"; shift ;;
        -nv|-c|-q) shift ;;
        *) url="$1"; shift ;;
    esac
done
[ -n "${output_path}" ] && [ -n "${url}" ]
mkdir -p "$(dirname "${output_path}")"
printf 'url:%s\nout:%s\n' "${url}" "${output_path}" >> "${WGET_LOG_FILE}"
printf 'downloaded:%s\n' "${url}" > "${output_path}"
EOF
chmod +x "${BIN_DIR}/wget"

(
    export PATH="${BIN_DIR}:${PATH}"
    export WGET_LOG_FILE
    export COMFY_MODEL_ROOT="${MODEL_ROOT}"
    export RUNPOD_HF_CACHE_ROOT="${TEST_DIR}/hf-cache/hub"
    export LTX25_DOWNLOAD_BACKEND="wget"
    export HUGGINGFACE_ACCESS_TOKEN="hf-test-token"
    export LTX25_PRELOAD_VARIANT="distilled-int8"
    export LTX25_PRELOAD_PROMPT_ENHANCER=true
    source "${SCRIPT_TO_TEST}"
    bootstrap_ltx25
    bootstrap_ltx25
)

for expected_file in \
    "diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors" \
    "text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors" \
    "text_encoders/gemma4_e2b_it_bf16.safetensors" \
    "vae/ltx-2.5-video-vae-bf16.safetensors" \
    "vae/ltx-2.5-audio-vae-bf16.safetensors" \
    "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"; do
    [ -f "${MODEL_ROOT}/${expected_file}" ] || {
        echo "Expected ${MODEL_ROOT}/${expected_file} to exist"
        exit 1
    }
done

[ "$(grep -c '^url:' "${WGET_LOG_FILE}")" -eq 6 ]
[ "$(grep -c '^header:Authorization: Bearer hf-test-token$' "${WGET_LOG_FILE}")" -eq 6 ]

CACHE_ROOT="${TEST_DIR}/hf-cache/hub"
CACHED_MODEL_ROOT="${TEST_DIR}/cached-models"
CACHED_WGET_LOG="${TEST_DIR}/cached-wget.log"
mkdir -p "${CACHE_ROOT}"

# Match the mounted Hugging Face refs/snapshots/blobs layout. Only LTX is cached;
# the separately hosted Gemma prompt enhancer must use the download fallback.
python3 - "${CACHE_ROOT}" <<'PY'
from pathlib import Path
import sys

repo = Path(sys.argv[1]) / "models--Lightricks--LTX-2.5"
commit = "a" * 40
(repo / "refs").mkdir(parents=True)
(repo / "refs" / "main").write_text(commit)
for filename in (
    "diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
    "text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
    "vae/ltx-2.5-video-vae-bf16.safetensors",
    "vae/ltx-2.5-audio-vae-bf16.safetensors",
    "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
):
    blob = repo / "blobs" / Path(filename).name
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text("host-cached weights")
    cached = repo / "snapshots" / commit / filename
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.symlink_to(blob)
PY

run_cached_bootstrap() {
    (
        export PATH="${BIN_DIR}:${PATH}"
        export WGET_LOG_FILE="${CACHED_WGET_LOG}"
        export COMFY_MODEL_ROOT="${CACHED_MODEL_ROOT}"
        export RUNPOD_HF_CACHE_ROOT="${CACHE_ROOT}"
        export LTX25_USE_RUNPOD_CACHE=true
        export HF_HOME="${TEST_DIR}/unrelated-hf-home"
        export LTX25_DOWNLOAD_BACKEND=wget
        export LTX25_PRELOAD_VARIANT=distilled-int8
        export LTX25_PRELOAD_PROMPT_ENHANCER=true
        source "${SCRIPT_TO_TEST}"
        bootstrap_ltx25
    )
}

run_cached_bootstrap
run_cached_bootstrap
[ "$(grep -c '^url:' "${CACHED_WGET_LOG}")" -eq 1 ]
grep -q 'Comfy-Org/gemma-4' "${CACHED_WGET_LOG}"
for model_dir in diffusion_models vae latent_upscale_models; do
    for model_file in "${CACHED_MODEL_ROOT}/${model_dir}/"*.safetensors; do
        [ -L "${model_file}" ] && [ "$(cat "${model_file}")" = "host-cached weights" ]
    done
done
[ -L "${CACHED_MODEL_ROOT}/text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors" ]

# Opting out still downloads even while the host cache is mounted.
(
    export PATH="${BIN_DIR}:${PATH}"
    export WGET_LOG_FILE="${TEST_DIR}/cache-disabled-wget.log"
    export COMFY_MODEL_ROOT="${TEST_DIR}/cache-disabled-models"
    export RUNPOD_HF_CACHE_ROOT="${CACHE_ROOT}"
    export LTX25_USE_RUNPOD_CACHE=false
    export LTX25_DOWNLOAD_BACKEND=wget
    export LTX25_PRELOAD_VARIANT=distilled-int8
    export LTX25_PRELOAD_PROMPT_ENHANCER=true
    source "${SCRIPT_TO_TEST}"
    bootstrap_ltx25
    [ "$(grep -c '^url:' "${WGET_LOG_FILE}")" -eq 6 ]
)

# A replacement host without the cache must recover links saved on a volume.
mv "${CACHE_ROOT}" "${CACHE_ROOT}.previous-host"
run_cached_bootstrap
[ "$(grep -c '^url:' "${CACHED_WGET_LOG}")" -eq 6 ]
[ ! -L "${CACHED_MODEL_ROOT}/diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors" ]

# The hf_hub backend also replaces a dangling host-cache link on a cache miss.
mkdir -p "${TEST_DIR}/fake-hub"
cat > "${TEST_DIR}/fake-hub/huggingface_hub.py" <<'PY'
from pathlib import Path


def hf_hub_download(*, repo_id, filename, revision, token, local_dir):
    target = Path(local_dir) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hf_hub fallback weights")
    return str(target)
PY
(
    export PATH="${BIN_DIR}:${PATH}"
    export PYTHONPATH="${TEST_DIR}/fake-hub"
    export RUNPOD_HF_CACHE_ROOT="${CACHE_ROOT}"
    export LTX25_DOWNLOAD_BACKEND=hf_hub
    source "${SCRIPT_TO_TEST}"
    fallback_path="${TEST_DIR}/hf-fallback/model.safetensors"
    mkdir -p "$(dirname "${fallback_path}")"
    ln -s "${CACHE_ROOT}/missing-model" "${fallback_path}"
    ltx_download \
        "https://huggingface.co/Lightricks/LTX-2.5/resolve/main/diffusion_models/model.safetensors" \
        "${fallback_path}"
    [ ! -L "${fallback_path}" ]
    [ "$(cat "${fallback_path}")" = "hf_hub fallback weights" ]
)

if (
    export LTX25_PRELOAD_VARIANT="made-up-variant"
    source "${SCRIPT_TO_TEST}"
    bootstrap_ltx25 >/dev/null 2>&1
); then
    echo "Expected unsupported LTX variant to fail"
    exit 1
fi

echo "✅ bootstrap_ltx25 preload behavior verified"
