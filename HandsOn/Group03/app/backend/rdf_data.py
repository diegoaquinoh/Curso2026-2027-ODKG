"""Load the Group03 linked RDF exports into Oxigraph and answer SPARQL queries.

Every value the application shows comes from the three ``*-with-links.ttl``
exports in ``HandsOn/Group03/rdf``; the original CSV files are never read.
"""
from __future__ import annotations

import bisect
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pyoxigraph as ox

log = logging.getLogger(__name__)

RDF_FILES = (
    "estaciones_acusticas-with-links.ttl",
    "terrazas_hosteleria-with-links.ttl",
    "contaminacion_acustica-with-links.ttl",
)

PREFIXES = """
PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:     <http://www.w3.org/2002/07/owl#>
PREFIX xsd:     <http://www.w3.org/2001/XMLSchema#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX dcat:    <http://www.w3.org/ns/dcat#>
PREFIX mn:      <https://madridnoise.linkeddata.es/ontology#>
PREFIX period:  <https://madridnoise.linkeddata.es/resource/period/>
"""

STATIONS_QUERY = PREFIXES + """
SELECT ?station ?code ?name ?lat ?lon ?district ?districtLabel ?districtWikidata
WHERE {
  ?station a mn:NoiseMonitoringStation ;
           mn:stationCode ?code ; mn:stationName ?name ;
           mn:latitude ?lat ; mn:longitude ?lon .
  OPTIONAL {
    ?station dcterms:spatial ?district .
    ?district a mn:District .
    OPTIONAL { ?district rdfs:label ?districtLabel }
    OPTIONAL { ?district owl:sameAs ?districtWikidata }
  }
}
ORDER BY ?code
"""

TERRACES_QUERY = PREFIXES + """
SELECT ?terraceId ?lat ?lon
       ?district ?districtLabel ?districtWikidata
       ?neighbourhood ?neighbourhoodLabel ?neighbourhoodWikidata
WHERE {
  ?terrace a mn:Terrace ; mn:terraceId ?terraceId ;
           mn:latitude ?lat ; mn:longitude ?lon .
  OPTIONAL {
    ?terrace mn:hasDistrict ?district .
    OPTIONAL { ?district rdfs:label ?districtLabel }
    OPTIONAL { ?district owl:sameAs ?districtWikidata }
  }
  OPTIONAL {
    ?terrace mn:hasNeighbourhood ?neighbourhood .
    OPTIONAL { ?neighbourhood rdfs:label ?neighbourhoodLabel }
    OPTIONAL { ?neighbourhood owl:sameAs ?neighbourhoodWikidata }
  }
}
"""

TERRACE_TOTAL_QUERY = PREFIXES + """
SELECT (COUNT(?terrace) AS ?total) WHERE { ?terrace a mn:Terrace }
"""

NIGHT_DATE_RANGE_QUERY = PREFIXES + """
SELECT (MIN(?date) AS ?first) (MAX(?date) AS ?last) (COUNT(?o) AS ?observations)
WHERE {
  ?o a mn:DailyNoiseObservation ; mn:forPeriod period:N ;
     mn:observationDate ?date ; mn:laeq ?laeq .
}
"""

DATASETS_QUERY = PREFIXES + """
SELECT ?dataset ?title ?description ?license ?source ?created
WHERE {
  ?dataset a dcat:Dataset ; dcterms:title ?title .
  OPTIONAL { ?dataset dcterms:description ?description }
  OPTIONAL { ?dataset dcterms:license ?license }
  OPTIONAL { ?dataset dcterms:source ?source }
  OPTIONAL { ?dataset dcterms:created ?created }
}
ORDER BY ?dataset
"""

# Arithmetic mean of the reported night (period N) LAeq per station, as in
# query L2 of rdf/queries-with-links.sparql. Dates are validated with
# datetime.date before being inserted, so the literals cannot inject SPARQL.
NIGHT_NOISE_QUERY = PREFIXES + """
SELECT ?code (COUNT(?o) AS ?nights) (AVG(?laeq) AS ?meanLaeq)
WHERE {{
  ?o a mn:DailyNoiseObservation ;
     mn:forPeriod period:N ; mn:recordedAt ?station ;
     mn:observationDate ?date ; mn:laeq ?laeq .
  ?station mn:stationCode ?code .
  FILTER (?date >= "{start}"^^xsd:date && ?date <= "{end}"^^xsd:date)
}}
GROUP BY ?code
"""

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _value(solution: ox.QuerySolution, name: str):
    term = solution[name]
    return None if term is None else term.value


def _wikidata_id(uri: str | None) -> str | None:
    return uri.rsplit("/", 1)[-1] if uri else None


@dataclass
class Station:
    uri: str
    code: str
    name: str
    lat: float
    lon: float
    district: str | None
    district_label: str | None
    district_wikidata: str | None
    # (distance in metres, terrace index), sorted by distance.
    nearby: list[tuple[float, int]] = field(default_factory=list, repr=False)
    distances: list[float] = field(default_factory=list, repr=False)

    def terraces_within(self, radius_m: float) -> int:
        return bisect.bisect_right(self.distances, radius_m)

    def as_dict(self) -> dict:
        return {
            "uri": self.uri,
            "code": self.code,
            "name": self.name,
            "lat": self.lat,
            "lon": self.lon,
            "district": {
                "uri": self.district,
                "label": self.district_label,
                "wikidata": self.district_wikidata,
                "wikidataId": _wikidata_id(self.district_wikidata),
            } if self.district else None,
        }


@dataclass
class Terrace:
    id: int
    lat: float
    lon: float
    district_label: str | None
    district_wikidata: str | None
    neighbourhood_label: str | None
    neighbourhood_wikidata: str | None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "lat": self.lat,
            "lon": self.lon,
            "district": self.district_label,
            "districtWikidata": self.district_wikidata,
            "districtWikidataId": _wikidata_id(self.district_wikidata),
            "neighbourhood": self.neighbourhood_label,
            "neighbourhoodWikidata": self.neighbourhood_wikidata,
            "neighbourhoodWikidataId": _wikidata_id(self.neighbourhood_wikidata),
        }


class MadridNoiseGraph:
    """In-memory Oxigraph store holding the group's linked RDF exports."""

    def __init__(self, rdf_dir: Path):
        self.rdf_dir = rdf_dir
        self.store = ox.Store()
        self.stations: list[Station] = []
        self.station_by_code: dict[str, Station] = {}
        self.terraces: list[Terrace] = []
        self.terrace_total = 0
        self.first_night: date | None = None
        self.last_night: date | None = None
        self.night_observations = 0
        self.datasets: list[dict] = []
        self.triples = 0

    # -- loading -------------------------------------------------------------
    def load(self) -> "MadridNoiseGraph":
        start = time.monotonic()
        missing = [f for f in RDF_FILES if not (self.rdf_dir / f).is_file()]
        if missing:
            raise FileNotFoundError(
                f"RDF exports not found in {self.rdf_dir}: {', '.join(missing)}. "
                "Set MADRIDNOISE_RDF_DIR to the Group03 rdf/ folder."
            )
        for name in RDF_FILES:
            self.store.bulk_load(path=str(self.rdf_dir / name), format=ox.RdfFormat.TURTLE)
        self.triples = len(self.store)
        self._load_stations()
        self._load_terraces()
        self._load_metadata()
        self._index_distances()
        log.info(
            "Loaded %s triples, %s stations, %s located terraces in %.1fs",
            f"{self.triples:,}", len(self.stations), len(self.terraces),
            time.monotonic() - start,
        )
        return self

    def select(self, query: str) -> list[ox.QuerySolution]:
        return list(self.store.query(query))

    def _load_stations(self) -> None:
        for s in self.select(STATIONS_QUERY):
            station = Station(
                uri=_value(s, "station"),
                code=_value(s, "code"),
                name=_value(s, "name"),
                lat=float(_value(s, "lat")),
                lon=float(_value(s, "lon")),
                district=_value(s, "district"),
                district_label=_value(s, "districtLabel"),
                district_wikidata=_value(s, "districtWikidata"),
            )
            if station.code not in self.station_by_code:
                self.stations.append(station)
                self.station_by_code[station.code] = station

    def _load_terraces(self) -> None:
        seen: set[int] = set()
        for s in self.select(TERRACES_QUERY):
            terrace_id = int(_value(s, "terraceId"))
            if terrace_id in seen:
                continue
            seen.add(terrace_id)
            self.terraces.append(Terrace(
                id=terrace_id,
                lat=float(_value(s, "lat")),
                lon=float(_value(s, "lon")),
                district_label=_value(s, "districtLabel"),
                district_wikidata=_value(s, "districtWikidata"),
                neighbourhood_label=_value(s, "neighbourhoodLabel"),
                neighbourhood_wikidata=_value(s, "neighbourhoodWikidata"),
            ))
        self.terraces.sort(key=lambda t: t.id)

    def _load_metadata(self) -> None:
        self.terrace_total = int(_value(self.select(TERRACE_TOTAL_QUERY)[0], "total"))
        row = self.select(NIGHT_DATE_RANGE_QUERY)[0]
        self.first_night = date.fromisoformat(_value(row, "first"))
        self.last_night = date.fromisoformat(_value(row, "last"))
        self.night_observations = int(_value(row, "observations"))
        self.datasets = [
            {
                "uri": _value(s, "dataset"),
                "title": _value(s, "title"),
                "description": _value(s, "description"),
                "license": _value(s, "license"),
                "source": _value(s, "source"),
                "created": _value(s, "created"),
            }
            for s in self.select(DATASETS_QUERY)
        ]

    def _index_distances(self) -> None:
        for station in self.stations:
            pairs = sorted(
                (haversine_m(station.lat, station.lon, t.lat, t.lon), i)
                for i, t in enumerate(self.terraces)
            )
            station.nearby = pairs
            station.distances = [d for d, _ in pairs]

    # -- queries -------------------------------------------------------------
    def night_noise(self, start: date, end: date) -> dict[str, tuple[int, float]]:
        """Return {station code: (nights with data, mean night LAeq)}."""
        query = NIGHT_NOISE_QUERY.format(start=start.isoformat(), end=end.isoformat())
        result = {}
        for s in self.select(query):
            result[_value(s, "code")] = (int(_value(s, "nights")), float(_value(s, "meanLaeq")))
        return result

    def terraces_near(self, station: Station, radius_m: float) -> list[dict]:
        rows = []
        for distance, index in station.nearby:
            if distance > radius_m:
                break
            row = self.terraces[index].as_dict()
            row["distance"] = round(distance, 1)
            rows.append(row)
        return rows
