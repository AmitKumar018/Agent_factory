import asyncio
import hashlib
import os
import shutil
from typing import Any

from app.config import get_settings

try:
    import aiofiles
except ModuleNotFoundError:
    class _AsyncFile:
        def __init__(self, path: str, mode: str, encoding: str | None = None):
            self.path = path
            self.mode = mode
            self.encoding = encoding
            self._handle = None

        async def __aenter__(self):
            def _open():
                return open(self.path, self.mode, encoding=self.encoding)

            self._handle = await asyncio.to_thread(_open)
            return self

        async def __aexit__(self, exc_type, exc, tb):
            if self._handle:
                await asyncio.to_thread(self._handle.close)

        async def read(self) -> Any:
            return await asyncio.to_thread(self._handle.read)

        async def write(self, content: Any) -> int:
            return await asyncio.to_thread(self._handle.write, content)

    class _AiofilesFallback:
        @staticmethod
        def open(path: str, mode: str = "r", encoding: str | None = None):
            return _AsyncFile(path, mode, encoding)

    aiofiles = _AiofilesFallback()

settings = get_settings()


class FileStore:
    """Local filesystem abstraction for project-scoped uploads, artifacts, and bundles."""

    def __init__(self, data_root: str | None = None):
        self.root = data_root or settings.DATA_ROOT

    def project_root(self, project_id: str) -> str:
        return os.path.join(self.root, project_id)

    def uploads_dir(self, project_id: str) -> str:
        return os.path.join(self.project_root(project_id), "uploads")

    def run_dir(self, project_id: str, run_id: str) -> str:
        return os.path.join(self.project_root(project_id), "runs", run_id)

    def workspace_dir(self, project_id: str, run_id: str) -> str:
        return os.path.join(self.run_dir(project_id, run_id), "workspace")

    def artifacts_dir(self, project_id: str, run_id: str) -> str:
        return os.path.join(self.run_dir(project_id, run_id), "artifacts")

    def graphs_dir(self) -> str:
        return os.path.join(self.root, "_graphs")

    def ensure_project_dirs(self, project_id: str) -> None:
        os.makedirs(self.uploads_dir(project_id), exist_ok=True)

    def ensure_run_dirs(self, project_id: str, run_id: str) -> None:
        os.makedirs(self.workspace_dir(project_id, run_id), exist_ok=True)
        os.makedirs(self.artifacts_dir(project_id, run_id), exist_ok=True)

    def ensure_graphs_dir(self) -> None:
        os.makedirs(self.graphs_dir(), exist_ok=True)

    async def save_upload(
        self,
        project_id: str,
        document_id: str,
        extension: str,
        content: bytes,
    ) -> str:
        self.ensure_project_dirs(project_id)
        path = os.path.join(self.uploads_dir(project_id), f"{document_id}.{extension}")
        async with aiofiles.open(path, "wb") as file:
            await file.write(content)
        return path

    async def save_artifact(
        self,
        project_id: str,
        run_id: str,
        filename: str,
        content: str | bytes,
        mode: str = "w",
    ) -> str:
        self.ensure_run_dirs(project_id, run_id)
        path = os.path.join(self.artifacts_dir(project_id, run_id), filename)
        encoding = "utf-8" if "b" not in mode else None
        async with aiofiles.open(path, mode, encoding=encoding) as file:
            await file.write(content)
        return path

    async def save_workspace_file(
        self,
        project_id: str,
        run_id: str,
        relative_path: str,
        content: str,
    ) -> str:
        full_path = os.path.join(self.workspace_dir(project_id, run_id), relative_path)
        os.makedirs(os.path.dirname(full_path) or self.workspace_dir(project_id, run_id), exist_ok=True)
        async with aiofiles.open(full_path, "w", encoding="utf-8") as file:
            await file.write(content)
        return full_path

    async def read_file(self, path: str) -> str:
        async with aiofiles.open(path, "r", encoding="utf-8") as file:
            return await file.read()

    async def read_bytes(self, path: str) -> bytes:
        async with aiofiles.open(path, "rb") as file:
            return await file.read()

    def create_bundle(self, project_id: str, run_id: str) -> str:
        self.ensure_run_dirs(project_id, run_id)
        workspace = self.workspace_dir(project_id, run_id)
        bundle_path = os.path.join(self.artifacts_dir(project_id, run_id), "bundle")
        shutil.make_archive(base_name=bundle_path, format="zip", root_dir=workspace, base_dir=".")
        return f"{bundle_path}.zip"

    def save_graph_png(self, workflow_name: str, png_bytes: bytes) -> str:
        self.ensure_graphs_dir()
        path = os.path.join(self.graphs_dir(), f"{workflow_name}.png")
        with open(path, "wb") as file:
            file.write(png_bytes)
        return path

    def get_graph_png_path(self, workflow_name: str) -> str | None:
        path = os.path.join(self.graphs_dir(), f"{workflow_name}.png")
        return path if os.path.exists(path) else None

    @staticmethod
    def compute_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def file_exists(self, path: str) -> bool:
        return os.path.isfile(path)

    def list_workspace_files(self, project_id: str, run_id: str) -> list[str]:
        workspace = self.workspace_dir(project_id, run_id)
        relative_paths: list[str] = []
        if not os.path.isdir(workspace):
            return relative_paths

        for dirpath, _, filenames in os.walk(workspace):
            for filename in filenames:
                absolute_path = os.path.join(dirpath, filename)
                relative_paths.append(os.path.relpath(absolute_path, workspace))
        return sorted(relative_paths)


file_store = FileStore(data_root=settings.DATA_ROOT)