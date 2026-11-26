import json
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import text

from grok_bot.api.routes import auth, maintenance_bridge, operations as operations_routes
from grok_bot.auth import operations


def test_operations_provisioning_and_repair_handoff_are_tenant_scoped(client, app, migrator_engine, monkeypatch):
    settings = app.state.settings
    monkeypatch.setattr(settings, "operations_workspace_provisioning", True)
    monkeypatch.setattr(settings, "operations_repair_handoff_enabled", True)
    monkeypatch.setattr(settings, "operations_turnover_origin", "https://turnover.example")
    pairs = {code * 43: (uuid4(), uuid4()) for code in ("a", "b")}
    first_workspace, _ = pairs["a" * 43]
    teammate_code = "m" * 43
    pairs[teammate_code] = (first_workspace, uuid4())
    owners = {workspace: account for workspace, account in list(pairs.values())[:2]}
    active = {f"opm_{code}": identity for code, identity in pairs.items()}

    def redeem(request, **_):
        code = json.loads(request.data)["code"]
        workspace, account = pairs[code]
        payload = json.dumps({
            "workspace_id": str(workspace), "account_id": str(account),
            "workspace_owner_id": str(owners[workspace]), "membership_token": f"opm_{code}",
        }).encode()
        return nullcontext(SimpleNamespace(read=lambda _: payload))

    monkeypatch.setattr(auth, "build_opener", lambda _: SimpleNamespace(open=redeem))

    def verify_membership(request, **_):
        from urllib.error import HTTPError

        identity = active.get(request.get_header("Authorization").removeprefix("Bearer "))
        if identity is None:
            raise HTTPError(request.full_url, 401, "Inactive", {}, None)
        workspace, account = identity
        payload = json.dumps({"workspace_id": str(workspace), "account_id": str(account)}).encode()
        return nullcontext(SimpleNamespace(read=lambda _: payload))

    monkeypatch.setattr(operations, "build_opener", lambda _: SimpleNamespace(open=verify_membership))

    def sign_in(code):
        response = client.post("/v1/auth/operations/redeem", json={"code": code, "verifier": "c" * 43})
        assert response.status_code == 200, response.text
        workspace, account = pairs[code]
        assert response.json()["organization_id"] == str(workspace)
        assert response.json()["user_id"] == str(account)
        return {"Authorization": "Bearer " + response.json()["token"]}

    def post(path, headers, payload):
        return client.post(path, headers={**headers, "Idempotency-Key": str(uuid4())}, json=payload)

    external_property = str(uuid4())
    teammate = sign_in(teammate_code)
    manager_a, manager_b = (sign_in(letter * 43) for letter in ("a", "b"))
    sign_in("a" * 43)
    assert client.get("/v1/me", headers=manager_a).json()["roles"] == ["property_manager"]
    bridges = []
    for index, (manager, (workspace, account)) in enumerate(zip((manager_a, manager_b), list(pairs.values())[:2], strict=True)):
        if index == 0:
            home, unit = uuid4(), uuid4()
            with migrator_engine.begin() as db:
                db.execute(text("""
                    INSERT INTO properties
                    (id, organization_id, name, address_line1, city, region, postal_code, country_code, timezone, created_by)
                    VALUES (:id, :org, :name, 'Synthetic Address', 'Charlotte', 'NC', '28000', 'US', 'America/New_York', :actor)
                """), {"id": home, "org": workspace, "name": f"Fixture House {index}", "actor": account})
                db.execute(text("""
                    INSERT INTO units (id, organization_id, property_id, label) VALUES (:id, :org, :home, 'Entire home')
                """), {"id": unit, "org": workspace, "home": home})
            linked = post("/v1/maintenance-property-links", manager, {
                "external_property_id": external_property, "unit_id": str(unit),
            })
            assert linked.status_code == 200, linked.text
        connection = post("/v1/maintenance-connections", manager, {"source": "guest_recovery"})
        assert connection.status_code == 200, connection.text
        bridges.append({"Authorization": "Bearer " + connection.json()["token"]})

    request = {
        "issue_id": "synthetic-issue",
        "external_property_id": external_property,
        "reservation_id": "synthetic-reservation",
        "conversation_id": "synthetic-conversation",
        "description": "The bathroom sink is dripping under the cabinet.",
        "source_fingerprint": "e" * 64,
    }
    path = "/v1/maintenance-bridge/requests"
    repair_tokens = [f"Bearer opr_{letter * 43}" for letter in ("a", "b")]
    grants = dict(zip(repair_tokens, list(pairs.values())[:2], strict=True))

    def verify_stay(upstream, **_):
        from urllib.parse import parse_qs, urlsplit

        workspace, account = grants[upstream.get_header("Authorization")]
        query = parse_qs(urlsplit(upstream.full_url).query)
        payload = json.dumps({
            "workspace_id": str(workspace), "account_id": str(account),
            "workspace_owner_id": str(owners[workspace]),
            "property_external_id": query["property_external_id"][0],
            "reservation_external_id": query["reservation_external_id"][0],
            "home": {
                "name": "Imported Fixture House",
                "address_line1": "20 Synthetic Street",
                "city": "Charlotte", "region": "NC", "postal_code": "28000",
                "country_code": "US", "timezone": "America/New_York",
                "unit_label": "Entire home",
            },
        }).encode()
        return nullcontext(SimpleNamespace(read=lambda _: payload))

    monkeypatch.setattr(maintenance_bridge, "build_opener", lambda _: SimpleNamespace(open=verify_stay))
    handoff = "/v1/maintenance-bridge/operations-requests"
    assert post(handoff, bridges[0], request).status_code == 401
    result_a = post(handoff, {"Authorization": repair_tokens[0]}, request)
    assert result_a.status_code == 200, result_a.text
    assert client.get(path + "/synthetic-issue", headers=bridges[1]).status_code == 404
    result_b = post(handoff, {"Authorization": repair_tokens[1]}, request)
    assert result_b.status_code == 200, result_b.text
    assert result_a.json()["ticket_id"] != result_b.json()["ticket_id"]
    assert client.get(path + "/synthetic-issue", headers=bridges[0]).json()["ticket_id"] == result_a.json()["ticket_id"]
    assert client.get(path + "/synthetic-issue", headers=bridges[1]).json()["ticket_id"] == result_b.json()["ticket_id"]
    homes = client.get("/v1/maintenance-workspace", headers=manager_b)
    assert homes.status_code == 200, homes.text
    assert [home["name"] for home in homes.json()["properties"]] == ["Imported Fixture House"]
    status_path = "/v1/maintenance-bridge/operations-status"
    status_query = {key: request[key] for key in ("issue_id", "external_property_id", "reservation_id")}
    assert client.get(status_path, params=status_query, headers=bridges[0]).status_code == 401
    current = client.get(status_path, params=status_query, headers={"Authorization": repair_tokens[0]})
    assert current.status_code == 200, current.text
    assert current.json()["ticket_id"] == result_a.json()["ticket_id"]
    wrong_stay = client.get(
        status_path, params={**status_query, "reservation_id": "other-stay"}, headers={"Authorization": repair_tokens[0]},
    )
    assert wrong_stay.status_code == 404, wrong_stay.text
    question = result_a.json()["next_question"]
    assert question is not None
    answer = {
        "issue_id": request["issue_id"],
        "external_property_id": external_property,
        "reservation_id": request["reservation_id"],
        "field_key": question["field_key"],
        "body": "The leak is under the bathroom sink.",
    }
    answers = "/v1/maintenance-bridge/operations-answers"
    assert post(answers, {"Authorization": repair_tokens[0]}, {
        **answer, "reservation_id": "another-reservation",
    }).status_code == 404
    answered = post(answers, {"Authorization": repair_tokens[0]}, answer)
    assert answered.status_code == 200, answered.text
    assert answered.json()["ticket_id"] == result_a.json()["ticket_id"]
    teammate_homes = client.get("/v1/maintenance-workspace", headers=teammate)
    assert teammate_homes.status_code == 200, teammate_homes.text
    assert [home["name"] for home in teammate_homes.json()["properties"]] == ["Fixture House 0"]
    del active["opm_" + teammate_code]
    assert client.get("/v1/maintenance-workspace", headers=teammate).status_code == 401
    assert client.get("/v1/maintenance-workspace", headers=manager_a).status_code == 200

    # Inbox identity is independently read-only and only returns ticket summaries in that workspace.
    identity = {"workspace_id": str(first_workspace), "account_id": str(pairs[teammate_code][1]),
                "workspace_owner_id": str(owners[first_workspace])}
    monkeypatch.setattr(operations_routes, "read_operations_identity", lambda *_args, **_kwargs: identity)
    with migrator_engine.begin() as db:
        db.execute(text("UPDATE maintenance_tickets SET status='ASSIGNMENT_PENDING' WHERE id=:id"),
                   {"id": result_a.json()["ticket_id"]})
    queue = client.get("/v1/operations/action-inbox", headers={"Authorization": "Bearer opq_" + "a" * 43})
    assert queue.status_code == 200, queue.text
    assert [item["id"] for item in queue.json()["items"]] == [result_a.json()["ticket_id"]]
    assert queue.json()["items"][0]["property"] == "Fixture House 0"
