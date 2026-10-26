#!/usr/bin/env bash
# OpsCenter CI build. Does not change the shared /root/ci/src worktree or K3s.
set -euo pipefail
umask 077

ref=${1:?usage: oc-ci-build.sh <branch-or-commit>}
[[ $ref =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,80}$ ]] || exit 2

repo=/root/ci/src
root=/root/ci/isolated-builds
mkdir -p "$root"
exec 9>/run/lock/opscenter-ci-build.lock
flock -n 9 || { echo 'another OpsCenter build is active' >&2; exit 1; }

free_kb=$(df -Pk / | awk 'NR==2 {print $4}')
(( free_kb >= 10 * 1024 * 1024 )) || { echo 'PVE root has under 10 GiB free' >&2; exit 1; }
pool_pct=$(lvs --noheadings -o data_percent pve/data 2>/dev/null | tr -d ' ')
awk -v pct="$pool_pct" 'BEGIN { exit !(pct < 90) }' || {
  echo "local-lvm is ${pool_pct}% full; stop the build" >&2
  exit 1
}

export GIT_SSH_COMMAND='ssh -i /root/.ssh/gitlab_deploy -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/root/.ssh/known_hosts'
git -C "$repo" fetch -q --tags --prune origin
if git -C "$repo" show-ref --verify --quiet "refs/remotes/origin/$ref"; then
  sha=$(git -C "$repo" rev-parse "refs/remotes/origin/$ref^{commit}")
else
  sha=$(git -C "$repo" rev-parse --verify "$ref^{commit}")
fi
tag=${sha:0:8}
work="$root/$sha"
if [[ ! -d $work ]]; then
  git -C "$repo" worktree add -q --detach "$work" "$sha"
fi
[[ $(git -C "$work" rev-parse HEAD) == "$sha" ]] || exit 1
echo "CI_SOURCE sha=$sha tag=$tag work=$work"
cmp -s "$0" "$work/deploy/ci/build.sh" || {
  echo 'Installed CI gate differs from this source revision; install its verified build.sh first' >&2
  exit 1
}
echo "CI_GATE_SCRIPT sha256=$(sha256sum "$0" | awk '{print $1}')"

python3 -m compileall -q "$work/backend" "$work/modules"
docker build -q -t "opscenter/backend:$tag" -f "$work/deploy/docker/backend.Dockerfile" "$work"
test_log="$root/backend-tests-$tag.log"
chmod 700 "$root"
if docker run --rm --security-opt apparmor=unconfined --tmpfs /tmp:rw,size=512m \
  -e DATABASE_URL=sqlite:////tmp/opscenter-ci.db \
  -v "$work:/opt/opscenter:ro" \
  -w /opt/opscenter "opscenter/backend:$tag" \
  sh -ec 'pip install -q pytest pytest-asyncio aiohttp==3.13.3 apprise==2.0.1 websocket-client==1.8.0
    python -m pytest -q backend/tests agent/test_processes.py
    for module in alerts service_catalog host_inventory host_monitor k3s logs electricity sms runtime extension_worker automation opencode_agent life_common; do
      PYTHONPATH="/opt/opscenter/modules/$module:/opt/opscenter/modules:/opt/opscenter/backend" python -m pytest -q "modules/$module"
    done' >"$test_log" 2>&1; then
  grep -E '[0-9]+ passed' "$test_log"
else
  echo "backend tests failed; root-only log: $test_log" >&2
  exit 1
fi

(
  cd "$work/frontend-vite"
  pnpm install --frozen-lockfile --prefer-offline
  node --test tests/*.test.mjs
  node ../modules/opencode_agent/test_ui.cjs
  python3 - ../modules/electricity/page.py <<'PY'
import re, runpy, subprocess, sys
page = runpy.run_path(sys.argv[1])['render_page']()
script = re.search(r'<script>(.*?)</script>', page, re.S).group(1)
subprocess.run(['node', '--check'], input=script, text=True, check=True)
print('1 electricity rendered JavaScript check passed')
PY
  pnpm build
  node -e 'const fs=require("node:fs");const p=JSON.parse(fs.readFileSync("package.json"));fs.writeFileSync("dist/release.json",JSON.stringify({version:p.version,git_commit:process.argv[1]},null,2))' "$sha"
)
docker build -q -t "opscenter/frontend:$tag" -f "$work/deploy/docker/frontend.Dockerfile" "$work"

images=("opscenter/backend:$tag" "opscenter/frontend:$tag")
for module in alerts service_catalog host_inventory host_monitor k3s logs electricity sms; do
  image="opscenter/module-${module//_/-}:$tag"
  docker build -q -t "$image" -f "$work/modules/$module/Dockerfile" "$work"
  images+=("$image")
done

docker build -q -t "opscenter/module-automation:$tag" -f "$work/modules/automation/Dockerfile" "$work/modules"
docker build -q -t "opscenter/module-opencode-agent:$tag" -f "$work/modules/opencode_agent/Dockerfile" "$work"
docker build -q -t "opscenter/opencode-runner:$tag" -f "$work/deploy/docker/opencode-runner.Dockerfile" "$work"
images+=("opscenter/module-automation:$tag" "opscenter/module-opencode-agent:$tag" "opscenter/opencode-runner:$tag")
docker build -q --build-arg "BACKEND_IMAGE=opscenter/backend:$tag" \
  -t "opscenter/extension-worker:$tag" -f "$work/modules/extension_worker/Dockerfile" "$work"
images+=("opscenter/extension-worker:$tag")

for module in subscriptions home_inventory life_reminders; do
  image="opscenter/module-${module//_/-}:$tag"
  docker build -q --build-arg "BACKEND_IMAGE=opscenter/backend:$tag" --build-arg "MODULE_NAME=$module" \
    -t "$image" -f "$work/modules/life_common/Dockerfile" "$work"
  docker run --rm --security-opt apparmor=unconfined "$image" \
    python -c 'import os,importlib;assert os.geteuid()==10001;importlib.import_module(os.environ["LIFE_MODULE"]+".main");print("IMAGE_CHECK life readable")'
  images+=("$image")
done

# Use the image's own user: strict checkout permissions must not break runtime reads.
docker run --rm --security-opt apparmor=unconfined --tmpfs /tmp:rw,size=128m \
  "opscenter/module-opencode-agent:$tag" python -c 'import os,pathlib; assert os.geteuid()==10001; [p.read_bytes() for p in pathlib.Path("/opt/module").rglob("*") if p.is_file()]; import main,websocket; print("IMAGE_CHECK opencode readable")'
docker run --rm --security-opt apparmor=unconfined --tmpfs /tmp:rw,size=128m \
  "opscenter/module-automation:$tag" python -c 'import os,pathlib; assert os.geteuid()==10001; [p.read_bytes() for root in ["/app/modules","/app/runtime"] for p in pathlib.Path(root).rglob("*") if p.is_file()]; from modules.automation.main import create_app; create_app(data_dir="/tmp/check"); print("IMAGE_CHECK automation readable")'
docker run --rm --security-opt apparmor=unconfined --tmpfs /tmp:rw,size=128m \
  "opscenter/opencode-runner:$tag" sh -ec 'test -r /etc/opencode/config.json; test "$(opencode-runner --version)" = 1.19.3; echo "IMAGE_CHECK runner readable"'

archive="$root/opscenter-images-$tag.tar.gz"
docker save "${images[@]}" | gzip -1 > "$archive"
sha256sum "$archive" > "$archive.sha256"
python3 - "$sha" "${images[@]}" > "$root/opscenter-release-$tag.json" <<'PY'
import json, subprocess, sys
images = json.loads(subprocess.check_output(['docker', 'image', 'inspect', *sys.argv[2:]]))
print(json.dumps({'git_commit': sys.argv[1], 'images': [
    {'tags': image['RepoTags'], 'id': image['Id']} for image in images
]}, indent=2))
PY
echo "CI_OK sha=$sha tag=$tag archive=$archive"
