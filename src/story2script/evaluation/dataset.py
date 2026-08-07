"""评测数据集加载与跨 split 合并。"""

from __future__ import annotations

import json
from pathlib import Path

from .models import EvalDataset


def load_dataset(path: str | Path) -> EvalDataset:
    resolved = Path(path)
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"无法读取评测数据集 {resolved}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"评测数据集不是合法 JSON：{resolved}（{exc}）") from exc
    return EvalDataset.model_validate(payload)


def load_datasets(paths: list[str | Path]) -> list[EvalDataset]:
    if not paths:
        raise ValueError("至少需要一个评测数据集。")
    datasets = [load_dataset(path) for path in paths]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for dataset in datasets:
        for case in dataset.cases:
            if case.id in seen:
                duplicates.add(case.id)
            seen.add(case.id)
    if duplicates:
        raise ValueError(f"多个数据集存在重复 case id：{', '.join(sorted(duplicates))}")
    return datasets
