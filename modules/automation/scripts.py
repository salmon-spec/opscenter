"""Edit the explicitly configured local Git runbook repository."""
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from fastapi import HTTPException


class Scripts:
    def __init__(self, root, repository_id):
        self.root = Path(root).resolve()
        self.repository_id = int(repository_id)

    def path(self, name):
        parts = PurePosixPath(name).parts
        if (not parts or name.startswith(("/", "-")) or "\\" in name or ":" in name
                or any(p.startswith(".") for p in parts) or any(ord(c) < 32 for c in name)
                or Path(name).suffix not in {".py", ".sh", ".yml", ".yaml"}):
            raise HTTPException(422, "使用仓库内的 Python、Shell 或 Ansible 脚本路径")
        path = self.root.joinpath(*parts)
        if any(p.is_symlink() for p in [path, *path.parents] if p != self.root.parent):
            raise HTTPException(422, "不允许编辑符号链接")
        if not path.resolve().is_relative_to(self.root):
            raise HTTPException(422, "脚本路径越界")
        return path

    def git(self, *args):
        try:
            return subprocess.check_output(["git", "-c", "safe.directory=" + str(self.root),
                "-C", str(self.root), *args], stderr=subprocess.PIPE, timeout=15).decode().strip()
        except (OSError, subprocess.SubprocessError):
            raise HTTPException(503, "脚本仓库操作失败，请核对仓库状态") from None

    def read(self, name):
        path = self.path(name)
        try:
            if path.stat().st_size > 128000:
                raise HTTPException(413, "脚本超过 128 KB")
            content = path.read_bytes()
            return {"path": name, "content": content.decode("utf-8"),
                    "revision": hashlib.sha256(content).hexdigest()}
        except FileNotFoundError:
            raise HTTPException(404, "脚本不存在") from None
        except (OSError, UnicodeError):
            raise HTTPException(422, "脚本必须是 UTF-8 文本") from None

    def files(self):
        return [name for name in self.git("ls-files").splitlines()
                if Path(name).suffix in {".py", ".sh", ".yml", ".yaml"}]

    def save(self, name, content, revision, actor, parameter_edit=None):
        try:
            lock = os.open(self.root / ".opscenter-edit.lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise HTTPException(409, "另一个脚本正在保存，请稍后重试") from None
        originals = {}
        try:
            changes = {name: (content, revision)}
            if parameter_edit is not None:
                from .parameters import with_parameters
                playbook, parameters, expected = parameter_edit
                original = self.read(playbook)
                if original['revision'] != expected:
                    raise HTTPException(409, '参数或主脚本已变化，请重新打开编辑')
                body = content if name == playbook else original['content']
                changes[playbook] = (with_parameters(body, parameters), expected)
            if any(len(body.encode('utf-8')) > 128000 for body, _ in changes.values()):
                raise HTTPException(413, '脚本与参数合计超过128KB')
            for filename, (body, expected) in changes.items():
                originals[filename] = self.read(filename)
                if originals[filename]['revision'] != expected:
                    raise HTTPException(409, "脚本已被其他人修改，请重新打开后编辑")
            if self.git("status", "--porcelain", "--untracked-files=no"):
                raise HTTPException(409, "脚本仓库有未提交修改，请先处理")
            def replace(filename, value):
                path = self.path(filename)
                fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".save-")
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(value.encode("utf-8")); stream.flush(); os.fsync(stream.fileno())
                    os.chmod(temporary, path.stat().st_mode & 0o777)
                    os.replace(temporary, path)
                finally:
                    if os.path.exists(temporary): os.unlink(temporary)
            changed = [n for n, (body, _) in changes.items() if body != originals[n]['content']]
            try:
                for filename in changed: replace(filename, changes[filename][0])
                if changed:
                    self.git("add", "--", *changed)
                    self.git("-c", "user.name=OpsCenter", "-c", "user.email=automation@localhost",
                             "commit", "-m", "Update runbook by " + actor.replace("\n", " ")[:100], "--", *changed)
            except (HTTPException, OSError):
                for filename in changed: replace(filename, originals[filename]['content'])
                self.git("reset", "--", *changed)
                raise HTTPException(503, '脚本与参数保存失败，已恢复原内容') from None
            return {**self.read(name), "commit": self.git("rev-parse", "HEAD")}
        finally:
            os.close(lock)
            (self.root / ".opscenter-edit.lock").unlink()
