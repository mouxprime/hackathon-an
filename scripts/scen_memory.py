"""Scénarios mémoire inter-conversations."""
import sys, time, uuid
from hemicycle_probe import run_turn, _fmt_result

def banner(t):
    print("\n" + "="*78 + f"\n  {t}\n" + "="*78)

sid = uuid.uuid4().hex[:6]

# --- Test M1 : rappel d'une mémoire GLOBALE existante (id=2 ORBAT) dans une conv neuve
banner("M1 — Rappel mémoire globale existante : 'Que sais-tu sur ORBAT ?'")
r = run_turn(f"mem-orbat-{sid}", "Que sais-tu sur le terme ORBAT ?", lang="fr",
             auto_approve=False)  # on veut voir le routage, pas lancer d'investigation
_fmt_result(r)

# --- Test M2 : scoping — la mémoire 'Maxime' est scopée channel g1. Fuit-elle en conv normale ?
banner("M2 — Scoping mémoire cellule : 'Comment je m'appelle ?' (mémoire id=5 scopée g1)")
r = run_turn(f"mem-name-{sid}", "Comment je m'appelle ?", lang="fr", auto_approve=False)
_fmt_result(r)

# --- Test M3 : mémoire globale id=1 influence-t-elle le routage NER ?
banner("M3 — 'Le texte pour le NER doit être en quelle langue ?' (mémoire id=1)")
r = run_turn(f"mem-ner-{sid}", "Dans quelle langue dois-je fournir le texte au NER ?",
             lang="fr", auto_approve=False)
_fmt_result(r)
