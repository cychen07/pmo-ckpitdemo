"""本地 Artifact 文件存储。

生产化目标：
- 产物内容不再只存在前端 mock 文本中
- 后端把 Agent 产出的文本/JSON 内容落到磁盘
- 通过 artifact_id 提供稳定读取接口
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ArtifactStore:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def configure(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, artifact_id: str, *, content: str, content_type: str) -> Path:
        target = self.base_dir / f"{artifact_id}.json"
        target.write_text(
            json.dumps(
                {
                    "artifact_id": artifact_id,
                    "content": content,
                    "content_type": content_type,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return target

    def read(self, artifact_id: str) -> dict[str, Any]:
        target = self.base_dir / f"{artifact_id}.json"
        if not target.exists():
            raise FileNotFoundError(artifact_id)
        return json.loads(target.read_text(encoding="utf-8"))


artifact_store = ArtifactStore("./data/artifacts")
