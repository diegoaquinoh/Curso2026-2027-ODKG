"""End-to-end tests against the real Group03 RDF exports (run from app/: pytest)."""
import math

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.rdf_data import haversine_m
from backend.stats import linear_fit, pearson

RANGE = {"start": "2025-01-01", "end": "2025-06-30"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_meta_matches_published_rdf(client):
    meta = client.get("/api/meta").json()
    assert meta["stations"] == 31
    assert meta["terracesTotal"] == 6594
    assert meta["terracesLocated"] == 6534
    assert meta["firstNight"] == "2025-01-01"
    assert meta["lastNight"] == "2026-10-04"
    assert len(meta["datasets"]) == 3


def test_stations_are_linked_to_wikidata(client):
    stations = client.get("/api/stations").json()
    assert len(stations) == 31
    assert all(s["district"]["wikidata"].startswith("http://www.wikidata.org/entity/Q") for s in stations)


def test_analysis_counts_and_correlation(client):
    a = client.get("/api/analysis", params={"radius": 250, **RANGE}).json()
    rows = a["stations"]
    assert len(rows) == 31
    included = [r for r in rows if r["included"]]
    assert a["correlation"]["n"] == len(included)
    assert len(a["excluded"]) == 31 - len(included)
    expected = pearson([r["terraces"] for r in included], [r["meanLaeq"] for r in included])
    assert a["correlation"]["r"] == pytest.approx(expected, abs=1e-3)


def test_terrace_counts_grow_with_radius(client):
    counts = {}
    for radius in (100, 250, 500):
        a = client.get("/api/analysis", params={"radius": radius, **RANGE}).json()
        counts[radius] = {r["code"]: r["terraces"] for r in a["stations"]}
    for code in counts[100]:
        assert counts[100][code] <= counts[250][code] <= counts[500][code]


def test_station_detail_is_consistent_with_analysis(client):
    a = client.get("/api/analysis", params={"radius": 250, **RANGE}).json()
    row = next(r for r in a["stations"] if r["code"] == "RF-03")
    d = client.get("/api/stations/RF-03", params={"radius": 250, **RANGE}).json()
    assert d["name"] == "Plaza del Carmen"
    assert d["terraceCount"] == row["terraces"] == len(d["terraces"])
    assert d["meanLaeq"] == row["meanLaeq"]
    assert all(t["distance"] <= 250 for t in d["terraces"])
    assert all(t["districtWikidata"] for t in d["terraces"])


def test_range_without_data_excludes_every_station(client):
    a = client.get("/api/analysis", params={"radius": 250, "start": "2024-01-01", "end": "2024-01-31"}).json()
    assert a["correlation"] == {"r": None, "n": 0, "strength": None}
    assert len(a["excluded"]) == 31


@pytest.mark.parametrize("params,status", [
    ({"radius": 250, "start": "2025-02-01", "end": "2025-01-01"}, 400),
    ({"radius": 5, **RANGE}, 422),
    ({"radius": 250, "start": "2025-01-01\" } DROP", "end": "2025-01-31"}, 422),
])
def test_invalid_parameters_are_rejected(client, params, status):
    assert client.get("/api/analysis", params=params).status_code == status


def test_unknown_station_returns_404(client):
    assert client.get("/api/stations/RF-99", params=RANGE).status_code == 404


def test_sparql_endpoint_is_read_only(client):
    ok = client.get("/sparql", params={"query": "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"})
    assert ok.status_code == 200
    assert int(ok.json()["results"]["bindings"][0]["n"]["value"]) > 1_000_000
    assert client.get("/sparql", params={"query": "DELETE WHERE { ?s ?p ?o }"}).status_code == 400


def test_frontend_is_served(client):
    res = client.get("/")
    assert res.status_code == 200 and "Terraces" in res.text


def test_helpers():
    # Puerta del Sol to Plaza Mayor: roughly 400 m apart.
    assert 350 < haversine_m(40.41692, -3.70358, 40.41553, -3.70742) < 400
    assert pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert pearson([1, 1, 1], [1, 2, 3]) is None
    slope, intercept = linear_fit([0, 1, 2], [1, 3, 5])
    assert math.isclose(slope, 2) and math.isclose(intercept, 1)
