# Terraces & night noise in Madrid: application

Web application for the question in
[requirements/applicationRequirements.html](../requirements/applicationRequirements.html):

> Is there an association between the number of registered terraces near a noise
> monitoring station and the night-time noise levels measured by that station?

The application reads **only the group's linked RDF** (`../rdf/*-with-links.ttl`),
never the CSV files (NFR2). At startup it loads the three Turtle exports
(about 1.07 million triples) into an in-memory [Oxigraph](https://github.com/oxigraph/oxigraph)
store. It answers the interface with SPARQL queries and also exposes a read-only
SPARQL endpoint.

```
 browser (Leaflet map + Chart.js scatter)
        │  JSON
        ▼
 FastAPI backend ── SPARQL ──► Oxigraph in-memory store
                                   ▲
              ../rdf/estaciones_acusticas-with-links.ttl
              ../rdf/terrazas_hosteleria-with-links.ttl
              ../rdf/contaminacion_acustica-with-links.ttl
```

## Contents

1. [Requirements coverage](#requirements-coverage)
2. [Prerequisites](#prerequisites)
3. [Option A: run locally with Python](#option-a-run-locally-with-python)
4. [Option B: run with Docker](#option-b-run-with-docker)
5. [Configuration](#configuration)
6. [Using the application](#using-the-application)
7. [HTTP API and SPARQL endpoint](#http-api-and-sparql-endpoint)
8. [Tests](#tests)
9. [Method and limitations](#method-and-limitations)
10. [Project layout](#project-layout)
11. [Licences and attribution](#licences-and-attribution)

## Requirements coverage

| ID | How the application meets it |
| --- | --- |
| FR1 | Leaflet map with the 31 stations (`mn:NoiseMonitoringStation`) and the 6,534 located terraces. |
| FR2 | Radius slider from 50 to 1,000 m in 25 m steps, with 100 / 250 / 500 m presets. |
| FR3 | Terraces within the radius are counted for every station and shown on the map pins, in the ranking and in the chart. |
| FR4 | "From" and "To" date pickers. A SPARQL aggregate computes the mean night LAeq (`period:N`) per station. |
| FR5 | Scatter plot with one point per station: terraces within the radius (x) against mean night LAeq (y), plus a least-squares line. |
| FR6 | Pearson *r* with the number of stations used and a verbal strength label. |
| FR7 | Clicking a station on the map, in the chart or in the ranking opens its detail: name, terrace count, mean night noise and nights with data. |
| FR8 | Stations with no night value in the range are left out of the chart and of *r*. The interface states how many were left out and names them. |
| NFR1 | Only CC BY 4.0 open data. The map attribution credits "Ayuntamiento de Madrid (CC BY 4.0)" and OpenStreetMap; the full source list is in [Licences and attribution](#licences-and-attribution). |
| NFR2 | All values come from SPARQL queries over the linked RDF. The station and terrace tables link the districts and neighbourhoods to Wikidata through `owl:sameAs`. |
| NFR3 | A radius or date change takes about 40–70 ms on the server (SPARQL aggregate plus pre-indexed distances), well under 3 s. |
| NFR4 | Plain HTML/CSS/JS, responsive from 360 px phones to wide desktop screens. |

Extras: a table of the terraces inside the radius of the selected station (with
Wikidata links for the district and neighbourhood), a sortable ranking of all
stations, shareable URLs (`#radius=…&start=…&end=…&station=…`) and a public `/sparql` endpoint.

## Prerequisites

* The linked RDF exports in `HandsOn/Group03/rdf/` (they are committed in the
  repository):
  `estaciones_acusticas-with-links.ttl`, `terrazas_hosteleria-with-links.ttl`,
  `contaminacion_acustica-with-links.ttl`. To regenerate them, follow the
  Hands-on 5 instructions in [../README.md](../README.md).
* **Either** Python 3.10 or later (tested with 3.12) **or** Docker with Docker Compose v2.
* About 300 MB of free RAM. The server uses about 200 MB once the graph is loaded.
* Internet access in the **browser** for the map tiles (OpenStreetMap), the
  Leaflet and Chart.js libraries (cdnjs) and the fonts (Google Fonts). The server
  itself makes no outgoing requests.

All commands below run from this folder:

```sh
cd HandsOn/Group03/app
```

## Option A: run locally with Python

```sh
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

With [uv](https://docs.astral.sh/uv/) instead:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Wait for the log line
`Loaded 1,070,386 triples, 31 stations, 6534 located terraces in …s`
(about 2 s), then open <http://127.0.0.1:8000>.

To reach the app from other devices on the network (for example to try it on a
phone), use `--host 0.0.0.0` and open `http://<your-computer-ip>:8000`.

## Option B: run with Docker

`docker-compose.yml` builds an image with only the code. It mounts `../rdf`
read-only at `/data/rdf`, so the 36 MB of Turtle files are not copied into the image.

```sh
docker compose up --build            # add -d to run in the background
```

Open <http://localhost:8000>. To use another port: `PORT=8080 docker compose up --build`.
Stop with `docker compose down`.

Without Compose:

```sh
docker build -t group03-terraces-night-noise .
docker run --rm -p 8000:8000 \
  -v "$(pwd)/../rdf:/data/rdf:ro" \
  group03-terraces-night-noise
```

The container runs as a non-root user and has a health check on `/api/meta`.

### Deploying on a server

The same image runs on any host that can run a container (a VM, Render, Fly.io,
Railway, …). Make the three `*-with-links.ttl` files available to the container
and point `MADRIDNOISE_RDF_DIR` to them. You can use a volume, or `COPY` them into
the image by adding `COPY rdf /data/rdf` to a Dockerfile whose build context is
`HandsOn/Group03`. Put a reverse proxy (nginx, Caddy, or the platform's) in front
for HTTPS. One worker is enough: each worker keeps its own copy of the graph in
memory, so more workers multiply the memory use.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `MADRIDNOISE_RDF_DIR` | `../rdf` (relative to this folder) | Folder containing the three `*-with-links.ttl` files. |

Host, port and number of workers are uvicorn options (`--host`, `--port`, `--workers`).

## Using the application

1. **Radius:** drag the slider or pick 100 / 250 / 500 m. The number on each
   station pin is the count of terraces within that radius, and the dashed circle shows the radius.
2. **Noise dates:** choose a "From" and "To" date (data covers 1 Jan 2025 to
   4 Oct 2026). The pin colour shows the mean night LAeq, from blue (quiet) to red (loud).
3. **Read the result:** the panel on the right shows Pearson *r*, how many stations
   were used and which were left out, and the scatter plot.
4. **Inspect a station:** click a pin, a point or a ranking row. The detail panel
   shows its figures and the terraces within the radius, with Wikidata links.
   Those terraces are also highlighted on the map.
5. **Share:** the URL keeps the radius, the dates and the selected station.

## HTTP API and SPARQL endpoint

Interactive documentation: <http://127.0.0.1:8000/docs>.

| Endpoint | Description |
| --- | --- |
| `GET /api/meta` | Graph size, date coverage, terrace totals and the `dcat:Dataset` descriptions. |
| `GET /api/stations` | Stations with coordinates and their district linked to Wikidata. |
| `GET /api/terraces` | Located terraces as `[id, lat, lon]`. |
| `GET /api/analysis?radius=250&start=2025-01-01&end=2025-06-30` | Per-station terrace count, nights with data and mean night LAeq, plus Pearson *r*, *n*, the fit line and the excluded stations. |
| `GET /api/stations/{code}?radius=…&start=…&end=…` | Detail of one station (for example `RF-03`) and its terraces within the radius. |
| `GET` or `POST /sparql?query=…` | Read-only SPARQL 1.1 query endpoint over the loaded graph. Returns SPARQL JSON by default, CSV/TSV for `Accept: text/csv` or `text/tab-separated-values`, and Turtle for CONSTRUCT/DESCRIBE. |

`radius` must be between 25 and 2,000 m, and dates use the `YYYY-MM-DD` format.

Example: run query L2 of [../rdf/queries-with-links.sparql](../rdf/queries-with-links.sparql)
against the running app:

```sh
curl -s http://127.0.0.1:8000/sparql \
  --data-urlencode query@- -H 'Accept: text/csv' <<'EOF'
PREFIX xsd:    <http://www.w3.org/2001/XMLSchema#>
PREFIX mn:     <https://madridnoise.linkeddata.es/ontology#>
PREFIX period: <https://madridnoise.linkeddata.es/resource/period/>
SELECT ?code (COUNT(?o) AS ?nights) (ROUND(AVG(?laeq) * 10) / 10 AS ?meanNightLAeq)
WHERE {
  ?o mn:recordedAt ?s ; mn:forPeriod period:N ; mn:observationDate ?d ; mn:laeq ?laeq .
  ?s mn:stationCode ?code .
  FILTER (?d >= "2025-01-01"^^xsd:date && ?d <= "2026-10-04"^^xsd:date)
}
GROUP BY ?code ORDER BY DESC(?meanNightLAeq)
EOF
```

The endpoint only accepts queries; SPARQL UPDATE is rejected. It has no query
timeout, so do not expose it publicly without a proxy-level rate limit or timeout.

## Tests

```sh
pip install -r requirements-dev.txt
pytest
```

The tests start the app against the real RDF files. They check the published
figures (31 stations, 6,594 terraces of which 6,534 are located, dates from
2025-01-01 to 2026-10-04), the consistency between the analysis and the station
detail, the FR8 exclusions, input validation and that the SPARQL endpoint is read-only.

## Method and limitations

* **Night noise:** arithmetic mean of the reported daily night LAeq (`mn:laeq`
  of observations with `mn:forPeriod period:N`, 23:00–07:00, dB(A)) over the
  selected dates, as in query L2. This averages the published decibel values; it
  is not an energy (logarithmic) average. Missing (`N/D`) values are not in the
  RDF and are therefore ignored.
* **Terrace counts:** great-circle (haversine) distance between the station
  coordinates and each terrace's approximate premises or grouping entrance
  location. 60 of the 6,594 registered terraces have no usable location and are
  not counted. Distances are computed once at startup, so any radius is answered instantly.
* **Time mismatch:** the terraces are a single snapshot (4 Oct 2026). Choosing an
  earlier noise range does not reconstruct which terraces existed then.
* **Station positions** are the current catalogue locations, not verified historical positions.
* **Association, not causation:** the interface says so next to the coefficient.
  Traffic, nightlife and street layout can influence both variables.

## Project layout

```
app/
├── README.md
├── requirements.txt          # runtime dependencies (FastAPI, uvicorn, pyoxigraph)
├── requirements-dev.txt      # + pytest, httpx
├── Dockerfile
├── docker-compose.yml        # mounts ../rdf read-only
├── pytest.ini
├── backend/
│   ├── main.py               # FastAPI app: JSON API, /sparql, static files
│   ├── rdf_data.py           # loads the TTL into Oxigraph, SPARQL queries, distance index
│   └── stats.py              # Pearson r and least-squares fit
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js                # map, chart, tables, state in the URL hash
└── tests/
    └── test_app.py
```

## Licences and attribution

* Source data © **Ayuntamiento de Madrid**, [datos.madrid.es](https://datos.madrid.es),
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/):
  daily noise ([215885](https://datos.madrid.es/dataset/215885-0-contaminacion-ruido)),
  noise stations ([211346](https://datos.madrid.es/dataset/211346-0-estaciones-acusticas)),
  premises and terraces census ([200085](https://datos.madrid.es/dataset/200085-0-censo-locales)).
* Transformed into RDF and linked to [Wikidata](https://www.wikidata.org) (CC0) by ODKG Group 03.
* Base map © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors (ODbL).
* Libraries: Leaflet (BSD-2), Chart.js (MIT), FastAPI (MIT), Oxigraph (MIT / Apache-2.0).
