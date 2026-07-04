"""Tests unitaires — extraction GeoJSON du bridge A2A (emprises/points → carte)."""

import json

import a2a_bridge as br


# FeatureCollection représentatif d'une réponse CSD : un Polygon + bbox, noyé dans
# l'enveloppe MCP (`result.content[].text` est une *string* JSON), elle-même placée
# par le wrapper A2A dans `output`.
def _csd_result_data() -> dict:
    fc = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "NSIL_CARD.identifier": "4ab5d9fe-c4ae-4798-bd85-526c141799d5",
                "NSIL_FILE.title": "testCNN.pdf",
                "NSIL_SECURITY.classification": "UNCLASSIFIED",
                "NSIL_PART.NSIL_COMMON.source": "CSDIMGTST",
                "NSIL_PART.NSIL_COVERAGE.spatialGeographicReferenceBox": {
                    "latmin": 44.8299033, "lonmin": 0.6361749,
                    "latmax": 46.8775767, "lonmax": 3.3840517,
                },
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [0.6361749, 44.8299033],
                    [3.3840517, 44.8299033],
                    [3.3840517, 46.8775767],
                    [0.6361749, 46.8775767],
                    [0.6361749, 44.8299033],
                ]],
            },
        }],
    }
    mcp = {"jsonrpc": "2.0", "id": 1,
           "result": {"content": [{"type": "text", "text": json.dumps(fc)}]}}
    return {"id": "t1", "status": {"state": "completed"}, "output": mcp,
            "artifacts": [{"parts": [{"kind": "text", "text": json.dumps(mcp)[:2000]}]}]}


def test_extract_geojson_polygone_csd():
    """Le Polygon CSD est déterré (même imbriqué) et converti en anneaux [lat,lon]."""
    geo = br._extract_geojson(_csd_result_data())
    assert "polygons" in geo and len(geo["polygons"]) == 1
    poly = geo["polygons"][0]
    assert poly["name"] == "testCNN.pdf"
    assert "UNCLASSIFIED" in poly["note"] and "CSDIMGTST" in poly["note"]
    ring = poly["rings"][0]
    # GeoJSON [lon,lat] → Leaflet [lat,lon] : le 1er sommet doit être (44.83, 0.636).
    assert ring[0] == [44.8299033, 0.6361749]
    assert len(ring) == 5
    # Pas de doublon bbox quand une vraie géométrie existe.
    assert len(geo["polygons"]) == 1


def test_extract_geojson_bbox_sans_geometrie():
    """Une Feature sans géométrie mais avec bbox → rectangle d'emprise."""
    feat = {"type": "Feature",
            "properties": {"NSIL_FILE.title": "zone",
                           "spatialGeographicReferenceBox": {
                               "latmin": 10.0, "lonmin": 20.0, "latmax": 11.0, "lonmax": 21.0}},
            "geometry": None}
    geo = br._extract_geojson({"type": "FeatureCollection", "features": [feat]})
    assert len(geo["polygons"]) == 1
    ring = geo["polygons"][0]["rings"][0]
    assert ring[0] == [10.0, 20.0] and len(ring) == 5


def test_extract_geojson_point_vers_locations():
    """Un Point GeoJSON alimente `locations` (canal mi_search), pas `polygons`."""
    pt = {"type": "Feature", "properties": {"name": "site"},
          "geometry": {"type": "Point", "coordinates": [2.35, 48.85]}}
    geo = br._extract_geojson({"type": "FeatureCollection", "features": [pt]})
    assert "polygons" not in geo
    assert geo["locations"] == [{"lat": 48.85, "lon": 2.35, "name": "site", "note": ""}]


def test_extract_geojson_aucun_geo():
    """Texte ordinaire (réponse mi_search) → rien à extraire."""
    assert br._extract_geojson({"output": {"text": "Voici la synthèse."}}) == {}


# --- Streaming du texte final (answer-stream → buffer draft de l'AgentCard) -----
def _answer_status(text: str) -> dict:
    """Forme d'un event status-update answer-stream émis par le wrapper A2A."""
    return {
        "status": {
            "state": "working",
            "message": {
                "role": "agent",
                "metadata": {"hemicycle_stream": "answer"},
                "parts": [{"kind": "text", "text": text}],
            },
        }
    }


def test_extract_answer_delta_marque():
    """Un status-update marqué hemicycle_stream=answer rend son delta de texte."""
    assert br._extract_answer_delta(_answer_status("le pont ")) == "le pont "
    # Delta vide = un delta quand même (pas None) → distinct de « pas un delta ».
    assert br._extract_answer_delta(_answer_status("")) == ""


def test_extract_answer_delta_non_marque():
    """Un status « humain » (sans le marqueur) n'est PAS un delta de réponse → None."""
    plain = {"status": {"message": {"parts": [{"kind": "text", "text": "searching…"}]}}}
    assert br._extract_answer_delta(plain) is None
    assert br._extract_answer_delta({}) is None
    # Le delta de réponse ne doit PAS être capté comme status « humain » (thinking).
    assert br._extract_status_text(_answer_status("le pont ")) == "le pont "  # même forme
    # …mais _extract_answer_delta le distingue par la metadata (route vers draft).
    assert br._extract_answer_delta(plain) is None


def test_looks_like_raw_json_et_resume():
    """Un blob JSON brut est détecté et remplacé par un résumé lisible."""
    raw = json.dumps(_csd_result_data()["output"])[:2000]
    assert br._looks_like_raw_json(raw) is True
    assert br._looks_like_raw_json("Synthèse en clair.") is False
    geo = br._extract_geojson(_csd_result_data())
    summary_fr = br._geo_summary(geo, "fr")
    assert "emprise" in summary_fr and "testCNN.pdf" in summary_fr
    summary_en = br._geo_summary(geo, "en")
    assert "footprint" in summary_en


# --- Géocodage LLM des lieux extraits (NER) -------------------------------------
def test_has_coords():
    """Seules des lat/lon numériques (pas booléennes) valent des coordonnées."""
    assert br._has_coords({"lat": 49.49, "lon": 0.1}) is True
    assert br._has_coords({"name": "Havre"}) is False           # nom seul (sortie NER)
    assert br._has_coords({"lat": "49", "lon": "0"}) is False    # strings refusées
    assert br._has_coords({"lat": True, "lon": False}) is False  # bool n'est pas une coord


def test_parse_geocode_json_avec_thinking_et_bruit():
    """Le parseur isole le tableau JSON malgré un <think>…</think> et du texte autour."""
    raw = ('<think>Le Havre est un port normand…</think>\n'
           'Voici les coordonnées :\n'
           '[{"name": "Le Havre", "lat": 49.49, "lon": 0.107}, '
           '{"name": "France", "lat": 46.6, "lon": 2.2}]\nVoilà.')
    coords = br._parse_geocode_json(raw)
    assert coords["le havre"] == (49.49, 0.107)   # clé normalisée (casefold)
    assert coords["france"] == (46.6, 2.2)


def test_parse_geocode_json_rejette_entrees_invalides():
    """Entrées sans coords numériques (null, string, bool) ignorées ; reste robuste."""
    raw = ('[{"name": "Inconnu", "lat": null, "lon": null}, '
           '{"name": "Bad", "lat": "x", "lon": 1}, '
           '{"name": "Bool", "lat": true, "lon": 2}, '
           '{"name": "OK", "lat": 1.5, "lon": 2.5}]')
    coords = br._parse_geocode_json(raw)
    assert coords == {"ok": (1.5, 2.5)}
    assert br._parse_geocode_json("pas de json ici") == {}
    assert br._parse_geocode_json("") == {}
