"""Tests unitaires — protocole d'artefacts : validation stricte + round-trip enveloppe."""

import pytest
from pydantic import ValidationError

from hemicycle_shared.artifacts import (
    ArtifactEnvelope, MiSearchPayload, validate_output,
)


def test_mi_search_payload_rejette_champ_inattendu():
    """extra='forbid' : un champ mal nommé échoue vite au lieu de se perdre silencieusement."""
    with pytest.raises(ValidationError):
        MiSearchPayload.model_validate({"summarys": "faute de frappe"})


def test_validate_output_discipline_connue_normalise_et_extrait_preview():
    """Discipline connue : payload validé + preview = summary."""
    payload, preview = validate_output("mi_search", {
        "sources": [{"title": "T", "snippet": "snip", "reliability": "B"}],
        "summary": "synthèse",
    })
    assert preview == "synthèse"
    assert payload["sources"][0]["title"] == "T"
    assert payload["sources"][0]["score"] == 0.0          # défaut appliqué


def test_validate_output_strict_remonte_sur_output_malforme():
    """Un output de discipline connue avec un champ étranger lève (fail-fast à la frontière)."""
    with pytest.raises(ValidationError):
        validate_output("imint", {"images": [], "summary": "ok", "bogus": 1})


def test_validate_output_discipline_inconnue_passe_tel_quel():
    """Agent A2A externe (pas de schéma) : passage tel quel + preview best-effort."""
    payload, preview = validate_output("data-management-agent",
                                       {"text": "résultat libre", "extra": 42})
    assert payload == {"text": "résultat libre", "extra": 42}
    assert preview == "résultat libre"


def test_artifact_envelope_round_trip():
    """to_bytes/from_bytes : l'enveloppe survit au passage par l'Object Store."""
    env = ArtifactEnvelope(artifact_id="corr-1", agent_type="geoint",
                           schema_name="geoint",
                           payload={"locations": [], "area": "z", "summary": "s"},
                           preview="s")
    back = ArtifactEnvelope.from_bytes(env.to_bytes())
    assert back.artifact_id == "corr-1"
    assert back.agent_type == "geoint"
    assert back.payload["area"] == "z"
    assert back.preview == "s"
