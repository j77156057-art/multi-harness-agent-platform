"""项目不再只是浏览器里的一个对象：它在平台上能建、能改名、能进回收站。

这一层钉住四件事（都属于"点错一次代价很大"的那类）：

1. **建：id 随机生成，名字只是标签**。同一个名字可以有两个项目，id 不会重；
2. **改名不动 id**：任务、群、事件、已合入的提交都挂在 id 上，改名字不该断掉任何一条；
3. **移除 = 进回收站**：列表里看不到，但数据一个字没动，一周内能找回；
4. **找回**：清掉时间戳即可，别的什么也没被改过。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.auth.tokens import TokenService
from app.config import settings
from tests.api_support import make_client

OPERATOR = "office-operator"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def new_project_id() -> str:
    """界面上"新建项目"用的那种 id：随机、够短、不参与任何语义。"""
    return f"proj-{uuid.uuid4().hex[:12]}"


def _env(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    client, database = make_client(tmp_path)
    token = TokenService(database).issue(subject_type="human", subject_id=OPERATOR)
    return client, database, token


def test_a_new_project_gets_a_random_id_and_the_name_is_only_a_label(tmp_path, monkeypatch):
    client, _database, token = _env(tmp_path, monkeypatch)
    first, second = new_project_id(), new_project_id()
    assert first != second, "两个项目的 id 不能撞"

    for project_id in (first, second):
        response = client.post(
            "/api/v1/projects",
            json={"project_id": project_id, "name": "同一个名字"},
            headers=bearer(token),
        )
        assert response.status_code == 200, response.text

    # 同名不同 id 是允许的：名字是给人看的，不是钥匙。
    listed = client.get("/api/v1/projects", headers=bearer(token)).json()["projects"]
    names = [row["name"] for row in listed if row["project_id"] in {first, second}]
    assert names == ["同一个名字", "同一个名字"]


def test_renaming_keeps_the_id_and_everything_hanging_off_it(tmp_path, monkeypatch):
    client, database, token = _env(tmp_path, monkeypatch)
    project_id = new_project_id()
    client.post(
        "/api/v1/projects",
        json={"project_id": project_id, "name": "旧名字"},
        headers=bearer(token),
    )
    client.post(
        f"/api/v1/projects/{project_id}/groups",
        json={"group_id": f"{project_id}-g1", "name": "群"},
        headers=bearer(token),
    )

    response = client.post(
        f"/api/v1/projects/{project_id}/rename",
        json={"name": "新名字"},
        headers=bearer(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "新名字"
    # id 没变，群还挂在它下面。
    groups = client.get(
        "/api/v1/groups", params={"project_id": project_id}, headers=bearer(token)
    ).json()["groups"]
    assert [row["group_id"] for row in groups] == [f"{project_id}-g1"]


def test_an_empty_rename_is_refused(tmp_path, monkeypatch):
    client, _database, token = _env(tmp_path, monkeypatch)
    project_id = new_project_id()
    client.post(
        "/api/v1/projects",
        json={"project_id": project_id, "name": "名字"},
        headers=bearer(token),
    )
    response = client.post(
        f"/api/v1/projects/{project_id}/rename", json={"name": "   "}, headers=bearer(token)
    )
    assert response.status_code in {400, 422}, response.text


def test_removing_a_project_puts_it_in_the_trash_and_leaves_the_data_alone(tmp_path, monkeypatch):
    client, database, token = _env(tmp_path, monkeypatch)
    project_id = new_project_id()
    client.post(
        "/api/v1/projects",
        json={"project_id": project_id, "name": "要移除的"},
        headers=bearer(token),
    )

    assert client.post(f"/api/v1/projects/{project_id}/trash", headers=bearer(token)).status_code == 200

    # 默认列表里没有了……
    listed = client.get("/api/v1/projects", headers=bearer(token)).json()["projects"]
    assert project_id not in {row["project_id"] for row in listed}
    # ……但带上 include_trashed 还在，并且知道它什么时候进的回收站。
    trashed = client.get(
        "/api/v1/projects", params={"include_trashed": 1}, headers=bearer(token)
    ).json()["projects"]
    row = next(item for item in trashed if item["project_id"] == project_id)
    assert row["trashed_at"], "要记下进回收站的时间（界面据此算还剩几天）"
    # 行还在库里：移除不是删除。
    assert database.read(
        "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
    ), "回收站里的项目必须还在表里"


def test_removing_twice_keeps_the_first_timestamp(tmp_path, monkeypatch):
    """反复点移除不该把"还剩几天"重置 —— 那等于无限续期。"""
    client, _database, token = _env(tmp_path, monkeypatch)
    project_id = new_project_id()
    client.post(
        "/api/v1/projects",
        json={"project_id": project_id, "name": "点两次"},
        headers=bearer(token),
    )
    first = client.post(
        f"/api/v1/projects/{project_id}/trash", headers=bearer(token)
    ).json()["trashed_at"]
    second = client.post(
        f"/api/v1/projects/{project_id}/trash", headers=bearer(token)
    ).json()["trashed_at"]
    assert first == second


def test_restoring_brings_it_back_untouched(tmp_path, monkeypatch):
    client, _database, token = _env(tmp_path, monkeypatch)
    project_id = new_project_id()
    client.post(
        "/api/v1/projects",
        json={"project_id": project_id, "name": "找回来"},
        headers=bearer(token),
    )
    client.post(f"/api/v1/projects/{project_id}/trash", headers=bearer(token))
    client.post(f"/api/v1/projects/{project_id}/restore", headers=bearer(token))

    listed = client.get("/api/v1/projects", headers=bearer(token)).json()["projects"]
    row = next(item for item in listed if item["project_id"] == project_id)
    assert row["name"] == "找回来"
    assert not row["trashed_at"]


def test_trashing_an_unknown_project_is_a_404_not_a_silent_success(tmp_path, monkeypatch):
    client, _database, token = _env(tmp_path, monkeypatch)
    for action in ("trash", "restore"):
        response = client.post(f"/api/v1/projects/proj-nope/{action}", headers=bearer(token))
        assert response.status_code == 404, f"{action}: {response.text}"
