"""Mesure de non-déterminisme : même requête répétée N fois, taux de pipeline correct."""
import uuid
from hemicycle_probe import run_turn

N = 6
TEXT = ("Voici un rapport en français sur des mouvements de blindés. Extrais-en "
        "toutes les entités nommées (unités, lieux, dates) via le NER.")

results = []
for i in range(N):
    sid = uuid.uuid4().hex[:8]
    r = run_turn(f"det-{sid}", TEXT, lang="fr", auto_approve=False,
                 idle_timeout=9.0, hard_cap=60.0, quiet=True)
    plan = next((w for w in r["widgets"] if w["type"] == "plan_proposal"), None)
    agents = [s.get("agent_type") for s in plan["data"]["steps"]] if plan else []
    has_trans = "translator-agent" in agents
    ok = has_trans and "nlp-agent" in agents and \
         agents.index("translator-agent") < agents.index("nlp-agent")
    results.append((agents, ok))
    print(f"  run {i+1}: {agents}  {'✅' if ok else '❌ NER sans traducteur' if agents==['nlp-agent'] else '⚠️ '+str(agents)}")

ok_n = sum(1 for _, ok in results if ok)
print(f"\n  Pipeline correct (trad→NER) : {ok_n}/{N}  ({100*ok_n//N}%)")
print(f"  Variantes observées : {set(tuple(a) for a,_ in results)}")
