"""Suivi multi-tours dans UNE même conversation : historique + self_context."""
import time, uuid
from hemicycle_probe import run_turn

def banner(t): print("\n" + "="*78 + f"\n  {t}\n" + "="*78)

conv = f"multiturn-{uuid.uuid4().hex[:6]}"

def show(r, approve_note=""):
    plan = next((w for w in r["widgets"] if w["type"]=="plan_proposal"), None)
    if plan:
        print(f"  → PLAN: {[(s.get('agent_type')) for s in plan['data']['steps']]}")
        for s in plan['data']['steps']:
            print(f"      [{s.get('agent_type')}] {(s.get('instruction') or '')[:120]}")
    finals=[m for m in r['messages'] if m['role']=='assistant']
    if finals: print(f"  → réponse: {finals[-1]['text'][:280]}")
    print(f"  (state={r['final_state']}, {r['elapsed_s']}s)")

# Tour 1 : pose un sujet (sans lancer — on regarde juste le routage)
banner(f"T1 [{conv}] : sujet initial Allemagne (routage seul)")
r = run_turn(conv, "Je m'intéresse à la situation des forces allemandes en Lituanie.",
             lang="fr", auto_approve=False, idle_timeout=9, hard_cap=60, quiet=True)
show(r)

# Tour 2 : ordre laconique — le SUJET doit venir de T1 (ancrage historique)
banner(f"T2 [{conv}] : ordre laconique 'demande à Search' → ancrage sur T1 ?")
r = run_turn(conv, "demande à Search", lang="fr", auto_approve=False,
             idle_timeout=9, hard_cap=60, quiet=True)
show(r)

# Tour 3 : question factuelle simple, puis on la repose pour tester self_context
banner(f"T3 [{conv}] : question chitchat dans le fil")
r = run_turn(conv, "Resume en une phrase ce dont on parle depuis le debut.",
             lang="fr", auto_approve=False, idle_timeout=9, hard_cap=60, quiet=True)
show(r)
