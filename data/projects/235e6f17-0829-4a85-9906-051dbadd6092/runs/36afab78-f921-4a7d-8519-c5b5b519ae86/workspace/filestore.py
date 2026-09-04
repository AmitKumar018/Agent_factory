```python
"""File storage system for persisting artifacts."""

import os
from config import settings
from werkzeug.utils import secure_filename

class FileStore:
    """Class for managing file storage of artifacts."""

    def __init__(self, base_path: str = settings.artifact_store_path):
        self.base_path = base_path
        os.makedirs(self.base_path, exist_ok=True)

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize the filename to prevent path traversal."""
        return secure_filename(filename)

    def save_artifact(self, filename: str, content: str) -> None:
        """Save an artifact to the file store."""
        sanitized_filename = self._sanitize_filename(filename)
        file_path = os.path.join(self.base_path, sanitized_filename)
        with open(file_path, 'w') as file:
            file.write(content)

    def load_artifact(self, filename: str) -> str:
        """Load an artifact from the file store."""
        sanitized_filename = self._sanitize_filename(filename)
        file_path = os.path.join(self.base_path, sanitized_filename)
        with open(file_path, 'r') as file:
            return file.read()
```