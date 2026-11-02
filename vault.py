"""Local prototype document storage, independent of Streamlit and Gemini."""
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from uuid import uuid4

REPO_ROOT = "lex_repo"


class VaultManager:
    DOC_TYPES = ("raw", "anonymized", "analysis", "email")

    def __init__(self, root_dir: str = REPO_ROOT):
        self.root = Path(root_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _inside(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("Document path must remain inside the vault")
        return resolved

    def _client_path(self, client_name: str) -> Path:
        if (not client_name or client_name in {".", ".."}
                or "/" in client_name or "\\" in client_name):
            raise ValueError("Client name must be a single directory name")
        return self._inside(self.root / client_name)

    def get_clients(self) -> List[str]:
        return [d.name for d in self.root.iterdir() if d.is_dir() and not d.is_symlink()]

    def ensure_client_folder(self, client_name: str) -> Path:
        client_path = self._client_path(client_name)
        client_path.mkdir(exist_ok=True)
        for sub in self.DOC_TYPES:
            self._inside(client_path / sub).mkdir(exist_ok=True)
        return client_path

    def save_document(
        self,
        client_name: str,
        doc_type: str,
        content: str,
        extension: str = "txt",
        label: str = "",
    ) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", doc_type):
            raise ValueError("Invalid document type")
        if not re.fullmatch(r"[a-zA-Z0-9]+", extension):
            raise ValueError("Invalid extension")
        client_path = self.ensure_client_folder(client_name)

        if doc_type not in self.DOC_TYPES:
            subdir = client_path / "other"
            subdir.mkdir(exist_ok=True)
        else:
            subdir = client_path / doc_type

        subdir = self._inside(subdir)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex

        if label:
            slug = label.lower().replace(" ", "-")
            slug = re.sub(r"[^a-z0-9_-]", "", slug)
            slug = slug.strip("-")
            if slug:
                filename = f"{timestamp}_{doc_type}_{slug}.{extension}"
            else:
                filename = f"{timestamp}_{doc_type}.{extension}"
        else:
            filename = f"{timestamp}_{doc_type}.{extension}"

        file_path = subdir / filename

        with open(file_path, "x", encoding="utf-8") as f:
            f.write(content)

        return str(file_path)

    def get_client_files(self, client_name: str) -> Dict[str, List[str]]:
        client_path = self._client_path(client_name)
        if not client_path.exists():
            return {}

        result: Dict[str, List[str]] = {}
        for sub in client_path.iterdir():
            if sub.is_dir() and not sub.is_symlink():
                files = sorted([f.name for f in sub.iterdir() if f.is_file() and not f.is_symlink()])
                if files:
                    result[sub.name] = files
        return result

    def read_document(self, client_name: str, doc_type: str, filename: str) -> str:
        client_path = self._client_path(client_name)
        if doc_type in self.DOC_TYPES:
            subdir = client_path / doc_type
        else:
            subdir = client_path / "other"
        if not filename or Path(filename).name != filename or "\\" in filename:
            raise ValueError("Invalid document filename")
        path = self._inside(subdir / filename)
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
        return ""

    def list_recent_documents(self, client_name: str, limit: int = 10) -> List[Dict[str, str]]:
        client_path = self._client_path(client_name)
        if not client_path.exists():
            return []

        entries = []
        for doc_type in self.DOC_TYPES:
            subdir = client_path / doc_type
            if subdir.exists() and subdir.is_dir() and not subdir.is_symlink():
                for file in subdir.iterdir():
                    if file.is_file() and not file.is_symlink():
                        name = file.name
                        parts = name.split("_", 1)
                        timestamp_str = parts[0] if parts else ""
                        dt = None
                        try:
                            dt = datetime.strptime(timestamp_str, "%Y%m%d")
                        except ValueError:
                            pass
                        entries.append({
                            "doc_type": doc_type,
                            "filename": name,
                            "path": str(file),
                            "timestamp": timestamp_str,
                            "dt": dt,
                        })

        entries.sort(key=lambda x: (x["dt"] is None, x["dt"]), reverse=True)
        entries.sort(key=lambda x: x["dt"] is None)
        entries_with_dt = [e for e in entries if e["dt"] is not None]
        entries_without_dt = [e for e in entries if e["dt"] is None]
        entries_with_dt.sort(key=lambda x: x["dt"], reverse=True)
        sorted_entries = entries_with_dt + entries_without_dt

        result = []
        for e in sorted_entries[:limit]:
            result.append({
                "doc_type": e["doc_type"],
                "filename": e["filename"],
                "path": e["path"],
                "timestamp": e["timestamp"],
            })
        return result

