# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
"""Generate realistic-looking notebooks for performance benchmarks.

The goal is cells with *plausible* content and size distribution, not uniform
filler, so hashing/parse costs resemble a real large notebook rather than a
best case (tiny cells) or worst case (one giant cell).
"""
from __future__ import annotations

import random
import uuid
from typing import Any, Dict, List

# A few realistic code snippets of varying length. The factory samples and
# lightly mutates these so cells differ (distinct hashes) while staying
# representative of real notebook content.
_CODE_SNIPPETS = [
    "import numpy as np\nimport pandas as pd\n",
    "df = pd.read_csv('data/{name}.csv')\ndf.head()\n",
    (
        "def {fn}(x):\n"
        "    \"\"\"Compute a rolling feature for {fn}.\"\"\"\n"
        "    return (x - x.mean()) / (x.std() + 1e-9)\n"
    ),
    (
        "fig, ax = plt.subplots(figsize=(8, 4))\n"
        "ax.plot(df['{col}'], label='{col}')\n"
        "ax.set_title('{col} over time')\n"
        "ax.legend()\n"
    ),
    (
        "result = (\n"
        "    df.groupby('{col}')\n"
        "      .agg(total=('value', 'sum'), n=('value', 'count'))\n"
        "      .reset_index()\n"
        ")\n"
        "result\n"
    ),
    (
        "model = Pipeline([\n"
        "    ('scaler', StandardScaler()),\n"
        "    ('clf', LogisticRegression(max_iter=1000)),\n"
        "])\n"
        "model.fit(X_train, y_train)\n"
        "print('score', model.score(X_test, y_test))\n"
    ),
    "for i in range(10):\n    print(i, i ** 2)\n",
    "%%time\ntotals = [compute({fn}, row) for row in rows]\nsum(totals)\n",
]

_MARKDOWN_SNIPPETS = [
    "## {title}\n\nThis section explores **{col}** and its relationship to the target.\n",
    (
        "### {title}\n\n"
        "- We load the raw data.\n"
        "- We clean missing values.\n"
        "- We engineer the `{col}` feature.\n"
    ),
    "We observe that `{fn}` normalizes the input. See the plot below.\n",
    (
        "> Note: the {col} column contains outliers; we clip at the 99th\n"
        "> percentile before modeling.\n"
    ),
]

_WORDS = [
    "revenue", "latency", "signal", "cohort", "region", "channel", "score",
    "delta", "window", "feature", "target", "sample", "weight", "epoch",
]


def _word(rng: random.Random) -> str:
    return rng.choice(_WORDS)


def _fill(rng: random.Random, template: str) -> str:
    return template.format(
        name=_word(rng),
        fn=f"{_word(rng)}_{rng.randint(0, 999)}",
        col=_word(rng),
        title=_word(rng).title(),
    )


def make_cell(rng: random.Random, *, tags_prob: float = 0.15) -> Dict[str, Any]:
    """Build one realistic cell with a stable nbformat 4.5 id."""
    is_markdown = rng.random() < 0.3
    if is_markdown:
        base = _fill(rng, rng.choice(_MARKDOWN_SNIPPETS))
        # Occasionally make a longer prose cell.
        if rng.random() < 0.25:
            base += "\n" + " ".join(_word(rng) for _ in range(rng.randint(20, 60)))
        cell: Dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "cell_type": "markdown",
            "source": base,
            "metadata": {},
        }
    else:
        n_snippets = rng.randint(1, 3)
        base = "".join(
            _fill(rng, rng.choice(_CODE_SNIPPETS)) for _ in range(n_snippets)
        )
        cell = {
            "id": uuid.uuid4().hex[:12],
            "cell_type": "code",
            "source": base,
            "metadata": {},
            "outputs": [],
            "execution_count": None,
        }
    if rng.random() < tags_prob:
        cell["metadata"]["tags"] = [_word(rng)]
    return cell


def make_notebook(n_cells: int, *, seed: int = 0) -> Dict[str, Any]:
    """Build a realistic notebook dict with ``n_cells`` cells."""
    rng = random.Random(seed)
    cells: List[Dict[str, Any]] = [make_cell(rng) for _ in range(n_cells)]
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3 (ipykernel)",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.12.0"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
