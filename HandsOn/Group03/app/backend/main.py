"""FastAPI application: JSON API, a read-only SPARQL endpoint and the web UI."""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import pyoxigraph as ox
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .rdf_data import MadridNoiseGraph
from .stats import describe_strength, linear_fit, pearson

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

APP_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RDF_DIR = APP_DIR.parent / "rdf"
FRONTEND_DIR = APP_DIR / "frontend"

MIN_RADIUS, MAX_RADIUS = 25, 2000

graph: MadridNoiseGraph | None = None


def get_graph() -> MadridNoiseGraph:
    if graph is None:  # pragma: no cover - only before startup completes
        raise HTTPException(503, "The RDF graph is still loading.")
    return graph


@asynccontextmanager
async def lifespan(_: FastAPI):
    global graph
    rdf_dir = Path(os.environ.get("MADRIDNOISE_RDF_DIR", DEFAULT_RDF_DIR)).resolve()
    graph = MadridNoiseGraph(rdf_dir).load()
    yield


app = FastAPI(
    title="Madrid Terraces & Night Noise",
    description="Explores the association between registered terraces near Madrid noise "
                "monitoring stations and their night-time noise (Ln), using the linked RDF "
                "published by ODKG Group 03.",
    version="1.0.0",
    lifespan=lifespan,
)


def _check_range(start: date, end: date) -> None:
    if start > end:
        raise HTTPException(400, "'start' must be on or before 'end'.")


RadiusQuery = Query(250, ge=MIN_RADIUS, le=MAX_RADIUS, description="Radius in metres")
StartQuery = Query(..., description="First noise date (YYYY-MM-DD), inclusive")
EndQuery = Query(..., description="Last noise date (YYYY-MM-DD), inclusive")


# -- JSON API ----------------------------------------------------------------
@app.get("/api/meta")
def meta():
    g = get_graph()
    return {
        "triples": g.triples,
        "stations": len(g.stations),
        "terracesTotal": g.terrace_total,
        "terracesLocated": len(g.terraces),
        "nightObservations": g.night_observations,
        "firstNight": g.first_night.isoformat(),
        "lastNight": g.last_night.isoformat(),
        "terraceSnapshot": "2026-10-04",
        "radius": {"min": MIN_RADIUS, "max": MAX_RADIUS, "default": 250},
        "datasets": g.datasets,
    }


@app.get("/api/stations")
def stations():
    return [s.as_dict() for s in get_graph().stations]


@app.get("/api/terraces")
def terraces():
    """Compact list of located terraces for the map: [id, lat, lon]."""
    return [[t.id, t.lat, t.lon] for t in get_graph().terraces]


@app.get("/api/analysis")
def analysis(radius: int = RadiusQuery, start: date = StartQuery, end: date = EndQuery):
    """Terraces within the radius and mean night LAeq for every station (FR3-FR6, FR8)."""
    _check_range(start, end)
    g = get_graph()
    t0 = time.perf_counter()
    noise = g.night_noise(start, end)
    rows, xs, ys, excluded = [], [], [], []
    for s in g.stations:
        count = s.terraces_within(radius)
        nights, mean = noise.get(s.code, (0, None))
        included = mean is not None
        rows.append({
            "code": s.code,
            "name": s.name,
            "terraces": count,
            "nights": nights,
            "meanLaeq": round(mean, 2) if included else None,
            "included": included,
        })
        if included:
            xs.append(count)
            ys.append(mean)
        else:
            excluded.append({"code": s.code, "name": s.name})
    r = pearson(xs, ys)
    fit = linear_fit(xs, ys)
    return {
        "radius": radius,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "stations": rows,
        "correlation": {
            "r": None if r is None else round(r, 4),
            "n": len(xs),
            "strength": describe_strength(r),
        },
        "fit": None if fit is None else {"slope": fit[0], "intercept": fit[1]},
        "excluded": excluded,
        "elapsedMs": round((time.perf_counter() - t0) * 1000, 1),
    }


@app.get("/api/stations/{code}")
def station_detail(code: str, radius: int = RadiusQuery,
                   start: date = StartQuery, end: date = EndQuery):
    """Name, terrace count, mean night noise and nearby terraces of one station (FR7)."""
    _check_range(start, end)
    g = get_graph()
    station = g.station_by_code.get(code)
    if station is None:
        raise HTTPException(404, f"Unknown station '{code}'.")
    nights, mean = g.night_noise(start, end).get(code, (0, None))
    nearby = g.terraces_near(station, radius)
    return {
        **station.as_dict(),
        "radius": radius,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "terraceCount": len(nearby),
        "nights": nights,
        "meanLaeq": None if mean is None else round(mean, 2),
        "terraces": nearby,
    }


# -- SPARQL endpoint (read-only) ---------------------------------------------
def _run_sparql(query: str, accept: str) -> Response:
    try:
        result = get_graph().store.query(query)
    except SyntaxError as exc:
        raise HTTPException(400, f"SPARQL syntax error: {exc}") from exc
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(400, f"SPARQL error: {exc}") from exc
    if isinstance(result, (ox.QuerySolutions, ox.QueryBoolean)):
        if "csv" in accept:
            fmt, media = ox.QueryResultsFormat.CSV, "text/csv"
        elif "tab-separated" in accept:
            fmt, media = ox.QueryResultsFormat.TSV, "text/tab-separated-values"
        else:
            fmt, media = ox.QueryResultsFormat.JSON, "application/sparql-results+json"
        return Response(result.serialize(format=fmt), media_type=media)
    if "n-triples" in accept:
        fmt, media = ox.RdfFormat.N_TRIPLES, "application/n-triples"
    else:
        fmt, media = ox.RdfFormat.TURTLE, "text/turtle"
    return Response(result.serialize(format=fmt), media_type=media)


@app.get("/sparql", summary="SPARQL 1.1 query endpoint (GET)")
def sparql_get(request: Request, query: str = Query(..., description="SPARQL query")):
    return _run_sparql(query, request.headers.get("accept", ""))


@app.post("/sparql", summary="SPARQL 1.1 query endpoint (POST form)")
def sparql_post(request: Request, query: str = Form(...)):
    return _run_sparql(query, request.headers.get("accept", ""))


# -- Web interface -------------------------------------------------------------
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
