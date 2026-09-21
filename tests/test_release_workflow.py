"""release ジョブの発火条件を YAML パースして検証する。

master への push（PR squash マージ）のたびに自動タグ採番＋GitHub Release が
走ってしまう事故を防ぐため、release ジョブは
  - タグ push (refs/tags/*)
  - workflow_dispatch（手動リリース）
のときだけ実行されるべき。
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML が必要")

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "build-binaries.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def release_if(workflow: dict) -> str:
    return workflow["jobs"]["release"]["if"]


def _clauses(condition: str) -> list[str]:
    """`||` 区切りの各条件節を返す（括弧のネストは考慮しない簡易分割）。"""
    return [c.strip() for c in condition.split("||")]


def test_release_runs_on_tag_push(release_if: str):
    assert any("refs/tags/" in c for c in _clauses(release_if))


def test_release_runs_on_workflow_dispatch(release_if: str):
    assert any(
        "workflow_dispatch" in c and "github.event_name" in c
        for c in _clauses(release_if)
    )


def test_release_not_triggered_by_branch_push(release_if: str):
    """main/master への push を条件に含む節が存在しないこと。"""
    for clause in _clauses(release_if):
        is_push_event = "push" in clause and "event_name" in clause
        targets_branch = "refs/heads/main" in clause or "refs/heads/master" in clause
        assert not (is_push_event and targets_branch), (
            f"ブランチ push による release 発火条件が残っている: {clause}"
        )


def test_release_keeps_manual_tag_step(workflow: dict):
    """workflow_dispatch を手動リリース導線とするため、
    タグ未指定時にタグを作成して push するステップが残っていること。"""
    step_names = [
        s.get("name", "") for s in workflow["jobs"]["release"]["steps"]
    ]
    assert any(
        "Create and push tag" in name for name in step_names
    ), "タグ自動採番ステップが削除されている"


def test_build_job_ungated(workflow: dict):
    """build ジョブは引き続き無条件（if なし）で実行されること。"""
    assert "if" not in workflow["jobs"]["build"]
