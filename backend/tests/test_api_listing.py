"""frontend-thread: the two read endpoints the rail needs (MVP-UI-001).

``GET /projects`` and ``GET /groups`` did not exist -- the frontend rail cannot
render its top level without them (R10 §22's left column). Both are pure reads
(the frontend only talks to the API), so this file checks shape, scope and
order, not guards.
"""

from __future__ import annotations

from tests.api_support import bootstrap, make_client


def test_projects_and_groups_lists_feed_the_rail(tmp_path):
    client, _database = make_client(tmp_path)
    bootstrap(client)  # creates P-1 / G-1
    created = client.post("/api/v1/projects", json={"project_id": "P-2", "name": "second"})
    assert created.status_code == 200, created.text
    grouped = client.post("/api/v1/projects/P-2/groups", json={"group_id": "G-2", "name": "g2"})
    assert grouped.status_code == 200, grouped.text

    projects = client.get("/api/v1/projects").json()["projects"]
    assert [item["project_id"] for item in projects] == ["P-1", "P-2"]
    assert projects[0]["name"] == "project"

    all_groups = client.get("/api/v1/groups").json()["groups"]
    assert [item["group_id"] for item in all_groups] == ["G-1", "G-2"]
    assert [item["project_id"] for item in all_groups] == ["P-1", "P-2"]

    scoped = client.get("/api/v1/groups", params={"project_id": "P-2"}).json()["groups"]
    assert [item["group_id"] for item in scoped] == ["G-2"]

    empty = client.get("/api/v1/groups", params={"project_id": "P-404"}).json()["groups"]
    assert empty == []
