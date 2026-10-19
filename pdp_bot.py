import os
from threading import Thread

from flask import Flask, jsonify

app = Flask(__name__)
etat = {"nom": "VoiceMaster", "statut": "démarrage..."}

COMMANDES = [
    ("Tout le monde", [
        ("=panneau", "Réafficher le panneau de gestion de ton salon."),
    ]),
    ("Whitelist", [
        ("=setup <id>", "Définir le salon générateur."),
        ("=find @m", "Trouver quelqu'un en vocal."),
        ("=bringall", "Ramener tout le monde dans ton vocal."),
        ("=bringcc <cat> <cat>", "Répartir les vocaux d'une catégorie dans une autre."),
    ]),
    ("Outils vocaux", [
        ("=mv @m", "Ramener un membre dans TON vocal."),
        ("=join @m", "Te déplacer dans SON vocal."),
        ("=follow @m", "La personne te suit de salon en salon."),
        ("=unfollow @m", "Elle arrête de te suivre."),
    ]),
    ("Owner", [
        ("=perm", "Panneau des perms (rôles → commandes)."),
        ("=wl @m", "Gérer la whitelist."),
        ("=owner @m", "Gérer les owners du bot."),
        ("=perso", "Personnaliser le bot (avatar, nom, statut)."),
        ("=prefix <nouveau>", "Changer le préfixe du bot."),
    ]),
]


def construire_commandes() -> str:
    blocs = []
    for titre, lignes in COMMANDES:
        items = "".join(
            f"<div class='cmd'><code>{cmd}</code><small>{desc}</small></div>"
            for cmd, desc in lignes
        )
        blocs.append(f"<section class='card'><h2>{titre.upper()}</h2>{items}</section>")
    return "".join(blocs)


PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VoiceMaster</title>
<style>
  body { margin:0; font-family: system-ui, sans-serif; background:#0b1220; color:#e6f7ff; }
  header { padding:56px 24px 32px; text-align:center; }
  h1 { color:#33c6ff; margin:0 0 8px; font-size:44px; letter-spacing:.02em; }
  .sub { color:#8aa0b5; margin:0 0 18px; }
  .badge { display:inline-block; padding:6px 14px; border-radius:999px; background:#12263a; color:#7fe0ff; font-size:14px; }
  main { max-width:960px; margin:0 auto; padding:0 20px 48px;
         display:grid; grid-template-columns:repeat(auto-fit,minmax(270px,1fr)); gap:16px; }
  .card { background:#12263a; border-radius:12px; padding:18px; }
  .card h2 { font-size:13px; color:#33c6ff; margin:0 0 12px; letter-spacing:.1em; }
  .cmd { margin-bottom:12px; }
  code { display:inline-block; background:#0b1220; padding:6px 10px; border-radius:8px; color:#fff; font-weight:600; }
  .cmd small { display:block; color:#8aa0b5; margin-top:4px; font-size:13px; }
  footer { text-align:center; color:#8aa0b5; padding:24px; font-size:13px; }
</style>
</head>
<body>
<header>
  <h1>VoiceMaster</h1>
  <p class="sub">Salons vocaux temporaires · panneau de gestion</p>
  <span class="badge">__STATUT__ · préfixe =</span>
</header>
<main>__COMMANDES__</main>
<footer>Rejoins le générateur pour créer ton vocal · =panneau pour le gérer</footer>
</body>
</html>"""


@app.route("/")
def accueil():
    page = PAGE.replace("__STATUT__", etat["statut"]).replace("__COMMANDES__", construire_commandes())
    return page


@app.route("/health")
def health():
    return jsonify(status="ok", bot=etat["nom"], statut=etat["statut"])


def set_status(texte: str):
    etat["statut"] = texte


def _run():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    Thread(target=_run, daemon=True).start()
