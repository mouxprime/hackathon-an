"""Batterie de routage : on observe le PLAN proposé sans lancer les agents.

Juge la pertinence de décision de l'orchestrateur (intent, choix d'agents,
ordre, chaînage langue, instructions ciblées) indépendamment des backends.
"""
import uuid
from hemicycle_probe import run_turn

sid = uuid.uuid4().hex[:6]

CASES = [
    # (id, texte, lang, attendu)
    ("R-pipeline-lang",
     "Voici un rapport en français sur des mouvements de blindés. Extrais-en "
     "toutes les entités nommées (unités, lieux, dates) via le NER.",
     "fr", "translator-agent (trad) AMONT de nlp-agent (NER anglais-only)"),

    ("R-multi-geo",
     "Trouve les informations récentes sur les positions militaires autour de "
     "Kharkiv et localise-les sur une carte.",
     "fr", "mi-search-agent + agent géo (csd/geoint)"),

    ("R-c2-firesupport",
     "Crée une demande d'appui-feu sur la position ennemie.",
     "fr", "c2-agent EXCLUSIF (garde déterministe C2)"),

    ("R-dataops",
     "Quelles collections de données avons-nous sur l'Ukraine ?",
     "fr", "data-management-agent (data_ops → investigate)"),

    ("R-vague",
     "Analyse ça.",
     "fr", "chitchat : UNE question de clarification (pas de refus)"),

    ("R-imint",
     "Analyse les dernières images satellite de l'aéroport de Saky et "
     "détecte les aéronefs présents.",
     "fr", "imint via csd-agent ou recherche imagerie"),

    ("R-widget",
     "Ouvre le widget de traduction.",
     "fr", "spawn_widget=translation, AUCUN agent"),
]

for cid, text, lang, expect in CASES:
    print("\n" + "="*78)
    print(f"  {cid}  | attendu: {expect}")
    print(f"  Q: {text}")
    print("="*78)
    r = run_turn(f"{cid}-{sid}", text, lang=lang, auto_approve=False,
                 idle_timeout=10.0, hard_cap=70.0, quiet=True)
    # Extraire le plan/chitchat
    plan = next((w for w in r["widgets"] if w["type"] == "plan_proposal"), None)
    if plan:
        steps = plan["data"].get("steps", [])
        print(f"  → PLAN ({len(steps)} étapes):")
        for s in steps:
            print(f"      [{s.get('agent_type')}] {(s.get('instruction') or '')[:110]}")
    else:
        print(f"  → PAS de plan (chitchat/widget). state={r['final_state']}")
    if r["spawned"]:
        print(f"  → spawn_widget: {r['spawned']}")
    finals = [m for m in r["messages"] if m["role"] == "assistant"]
    if finals:
        print(f"  → réponse: {finals[-1]['text'][:300]}")
    print(f"  ({r['elapsed_s']}s, status: {' → '.join(str(s) for s in r['status_chain'])})")
