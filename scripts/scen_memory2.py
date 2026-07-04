"""Cycle mémoire complet : apprendre dans conv A → rappeler dans conv B (chats distincts)."""
import time, uuid
from hemicycle_probe import run_turn, _fmt_result

def banner(t):
    print("\n" + "="*78 + f"\n  {t}\n" + "="*78)

sid = uuid.uuid4().hex[:6]
convA = f"learn-{sid}"
convB = f"recall-{sid}"

# --- Étape 1 : enseigner des faits (personalize) dans la conv A ---
banner(f"W1 — Conv A ({convA}) : enseigner 3 faits sur l'analyste")
r = run_turn(convA,
    "Personnalise-toi : retiens que je m'appelle Capitaine Lefebvre, "
    "que mon domaine de spécialité est l'imagerie satellitaire (IMINT), "
    "et que je veux toujours mes rapports rédigés en anglais.",
    lang="fr", auto_approve=False, auto_confirm_memory=True)
_fmt_result(r)

print("\n   … pause 4s pour laisser la transaction memory_decision committer …")
time.sleep(4)

# --- Étape 2 : NOUVELLE conv B, interroger ces faits ---
banner(f"R1 — Conv B ({convB}) : 'Comment je m'appelle et quel est mon domaine ?'")
r = run_turn(convB, "Rappelle-moi : comment je m'appelle et quel est mon domaine de specialite ?",
             lang="fr", auto_approve=False)
_fmt_result(r)

# --- Étape 3 : conv C encore neuve, tester l'application de la préférence (langue rapport) ---
convC = f"applypref-{sid}"
banner(f"R2 — Conv C ({convC}) : préférence de langue rapport mémorisée ?")
r = run_turn(convC, "Dans quelle langue est-ce que je veux mes rapports ?",
             lang="fr", auto_approve=False)
_fmt_result(r)
