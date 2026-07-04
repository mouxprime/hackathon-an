"""Zoom sur le chaînage langue (NER anglais-only) — le cas qui a échoué.

On teste 3 formulations de force croissante pour voir si le routeur insère
jamais le step traducteur translator-agent en amont du nlp-agent.
"""
import uuid
from hemicycle_probe import run_turn, _fmt_result

sid = uuid.uuid4().hex[:6]

CASES = [
    ("C1-implicite",
     "Voici un rapport en français sur des mouvements de blindés russes près de "
     "Donetsk le 3 juin. Extrais-en les entités nommées via le NER.", "fr"),
    ("C2-explicite",
     "J'ai un texte en français. Je veux que l'agent NER (nlp-agent) en extraie "
     "les entités. Or le NER n'accepte que l'anglais. Organise le pipeline.", "fr"),
    ("C3-attachement-sim",
     "Traduis puis analyse au NER ce texte : « Le 12e régiment a franchi la "
     "rivière Oskil à l'aube, appuyé par trois batteries d'artillerie. »", "fr"),
]

for cid, text, lang in CASES:
    print("\n" + "="*78 + f"\n  {cid}\n  Q: {text}\n" + "="*78)
    r = run_turn(f"chain-{cid}-{sid}", text, lang=lang, auto_approve=False,
                 idle_timeout=10.0, hard_cap=70.0, quiet=True)
    plan = next((w for w in r["widgets"] if w["type"] == "plan_proposal"), None)
    mem = next((w for w in r["widgets"] if w["type"] == "memory_recall"), None)
    if mem:
        print(f"  mémoire rappelée: {[m.get('content') for m in mem['data'].get('memories',[])]}")
    if plan:
        steps = plan["data"].get("steps", [])
        agents = [s.get("agent_type") for s in steps]
        has_trans = "translator-agent" in agents
        order_ok = has_trans and agents.index("translator-agent") < (
            agents.index("nlp-agent") if "nlp-agent" in agents else 99)
        verdict = "✅ TRADUCTEUR EN AMONT" if order_ok else (
            "⚠️ traducteur présent mais mal ordonné" if has_trans else
            "❌ PAS DE TRADUCTEUR")
        print(f"  → PLAN: {agents}   {verdict}")
        for s in steps:
            print(f"      [{s.get('agent_type')}] {(s.get('instruction') or '')[:130]}")
    else:
        print(f"  → pas de plan. state={r['final_state']}")
        finals = [m for m in r['messages'] if m['role']=='assistant']
        if finals: print(f"  → {finals[-1]['text'][:200]}")
