from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from typantic.web.endpoint import add_endpoint


class Cfg(BaseModel):
    name: str
    seed: int | None = None


def _client(**kwargs):
    app = FastAPI()
    received = {}

    def handler(cfg: Cfg) -> dict[str, str]:
        received["cfg"] = cfg
        return {"name": cfg.name}

    add_endpoint(app, Cfg, handler, **kwargs)
    return TestClient(app), received


def test_post_validates_and_calls_handler():
    client, received = _client(name="run")
    resp = client.post("/run", json={"name": "x", "seed": 3})
    assert resp.status_code == 200
    assert resp.json() == {"name": "x"}
    assert received["cfg"].seed == 3


def test_post_invalid_body_is_422():
    client, _ = _client(name="run")
    assert client.post("/run", json={}).status_code == 422


def test_schema_route_is_normalized():
    client, _ = _client(name="run")
    schema = client.get("/run/schema").json()
    seed = schema["properties"]["seed"]
    assert "anyOf" not in seed  # nullable union collapsed
    assert seed["type"] == "integer"


def test_default_name_from_handler():
    app = FastAPI()

    def process(cfg: Cfg) -> dict[str, str]:
        return {"name": cfg.name}

    add_endpoint(app, Cfg, process)
    client = TestClient(app)
    assert client.post("/process", json={"name": "y"}).status_code == 200


def test_explicit_path():
    client, _ = _client(path="/custom/run")
    assert client.post("/custom/run", json={"name": "z"}).status_code == 200
    assert client.get("/custom/run/schema").status_code == 200
