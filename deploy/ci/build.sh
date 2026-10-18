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

python3 -m compileall -q "$work/backend" "$work/modules"
docker build -q -t "opscenter/backend:$tag" -f "$work/deploy/docker/backend.Dockerfile" "$work"
test_log="$root/backend-tests-$tag.log"
chmod 700 "$root"
if docker run --rm --security-opt apparmor=unconfined --tmpfs /tmp:rw,size=512m \
  -e DATABASE_URL=sqlite:////tmp/opscenter-ci.db \
  -v "$work:/opt/opscenter:ro" \
  -w /opt/opscenter/backend "opscenter/backend:$tag" \
  sh -c 'pip install -q pytest && python -m pytest -q tests/test_smoke.py tests/test_extension_modules.py::test_builtin_seed_and_lifecycle' >"$test_log" 2>&1; then
  grep -E '[0-9]+ passed' "$test_log" | tail -1
else
  echo "backend tests failed; root-only log: $test_log" >&2
  exit 1
fi

(
  cd "$work/frontend-vite"
  pnpm install --frozen-lockfile --prefer-offline
  pnpm build
)
docker build -q -t "opscenter/frontend:$tag" -f "$work/deploy/docker/frontend.Dockerfile" "$work"

archive="$root/opscenter-images-$tag.tar"
docker save "opscenter/backend:$tag" "opscenter/frontend:$tag" -o "$archive"
sha256sum "$archive" > "$archive.sha256"
echo "CI_OK sha=$sha tag=$tag archive=$archive"
