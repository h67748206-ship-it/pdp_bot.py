"""
Bot PDP Pinterest -> Webhook Discord

- Toutes les X minutes, il va chercher des pdp sur Pinterest (recherches de ton choix)
- Il les envoie dans ton salon via un webhook (embeds avec l'image)
- Il garde en mémoire les images déjà postées (seen.json) pour pas faire de doublons
- Commande slash /pdp pour en poster direct (réservée à ceux qui ont "Gérer le serveur")
- Mini site web intégré (pour Render) :
    /        -> page de stats + galerie des dernières pdp
    /health  -> répond "ok" (pour cron-job.org)

Variables d'environnement (.env ou Render) :
    DISCORD_TOKEN = token du bot
    WEBHOOK_URL   = URL du webhook du salon où poster les pdp
    PORT          = donné automatiquement par Render (8080 par défaut en local)
"""

import os
import re
import json
import time
import html
import random
import asyncio

import aiohttp
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import tasks

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ================= CONFIG =================
TOKEN = os.getenv("DISCORD_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# Recherches Pinterest utilisées (une est choisie au hasard à chaque tour)
QUERIES = [
    "pdp aesthetic",
    "pdp anime",
    "pdp boy",
    "pdp girl",
    "pdp discord",
    "pdp dark",
    "matching pfp",
]

INTERVAL_MIN = 10      # toutes les X minutes
PICS_PER_POST = 5      # nombre de pdp par envoi (max 10)
EMBED_COLOR = 0x2B2D31
WEBHOOK_NAME = "PDP Bot"
SEEN_FILE = "seen.json"
# ==========================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

IMG_RE = re.compile(
    r"https://i\.pinimg\.com/(?:\d+x|originals)/[0-9a-f/]+\.(?:jpg|jpeg|png|webp)"
)


# ---------- mémoire des images déjà postées ----------
def load_seen() -> set:
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen: set):
    # on garde les 5000 dernières max pour pas que le fichier grossisse
    data = list(seen)[-5000:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


seen_images = load_seen()

# stats affichées sur le mini site
stats = {
    "total_sent": 0,
    "last_post": None,   # timestamp
    "last_query": "-",
    "recent": [],        # dernières URLs postées
}


# ---------- Pinterest ----------
async def fetch_pins(session: aiohttp.ClientSession, query: str) -> list[str]:
    """Récupère des URLs d'images Pinterest pour une recherche."""
    url = "https://www.pinterest.com/search/pins/"
    try:
        async with session.get(
            url, params={"q": query}, headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            if r.status != 200:
                print(f"[Pinterest] statut {r.status} pour '{query}'")
                return []
            html = await r.text()
    except Exception as e:
        print(f"[Pinterest] erreur : {e}")
        return []

    results = {}
    for u in IMG_RE.findall(html):
        # on prend la version 736x (bonne qualité, toujours dispo)
        hd = re.sub(r"/(?:\d+x|originals)/", "/736x/", u)
        key = hd.rsplit("/", 1)[-1]  # nom du fichier = identifiant unique
        results[key] = hd
    return [f"{k}|{v}" for k, v in results.items()]


async def get_new_pics(session: aiohttp.ClientSession, query: str, amount: int):
    raw = await fetch_pins(session, query)
    fresh = []
    for item in raw:
        key, url = item.split("|", 1)
        if key not in seen_images:
            fresh.append((key, url))
    random.shuffle(fresh)
    return fresh[:amount]


# ---------- Envoi webhook ----------
async def send_pics(session: aiohttp.ClientSession, pics, query: str) -> int:
    if not pics:
        return 0
    webhook = discord.Webhook.from_url(WEBHOOK_URL, session=session)
    embeds = []
    for _, url in pics:
        e = discord.Embed(color=EMBED_COLOR)
        e.set_image(url=url)
        embeds.append(e)
    embeds[0].title = f"📌 {query}"
    await webhook.send(embeds=embeds, username=WEBHOOK_NAME)
    for key, _ in pics:
        seen_images.add(key)
    save_seen(seen_images)

    stats["total_sent"] += len(pics)
    stats["last_post"] = time.time()
    stats["last_query"] = query
    stats["recent"] = ([u for _, u in pics] + stats["recent"])[:24]
    return len(pics)


async def run_once(session: aiohttp.ClientSession, query: str | None = None) -> tuple[int, str]:
    query = query or random.choice(QUERIES)
    pics = await get_new_pics(session, query, min(PICS_PER_POST, 10))
    sent = await send_pics(session, pics, query)
    return sent, query


# ---------- Mini site web (pour Render) ----------
PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>PDP Bot</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: system-ui, sans-serif; background: #0f1014; color: #e8e8ee; }}
  .wrap {{ max-width: 900px; margin: 0 auto; padding: 24px 16px 48px; }}
  h1 {{ margin: 0 0 4px; font-size: 1.6rem; }}
  .sub {{ color: #8b8d98; margin-bottom: 20px; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 24px; }}
  .card {{ background: #181a20; border: 1px solid #262833; border-radius: 12px; padding: 14px; }}
  .card b {{ display: block; font-size: 1.15rem; margin-top: 4px; word-break: break-word; }}
  .card span {{ color: #8b8d98; font-size: .8rem; }}
  .dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; background: {dot}; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }}
  .grid img {{ width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 10px; background: #181a20; }}
  .empty {{ color: #8b8d98; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>📌 PDP Bot</h1>
  <div class="sub"><span class="dot"></span>{status}</div>
  <div class="stats">
    <div class="card"><span>PDP envoyées</span><b>{total}</b></div>
    <div class="card"><span>Dernier envoi (UTC)</span><b>{last}</b></div>
    <div class="card"><span>Dernière recherche</span><b>{query}</b></div>
    <div class="card"><span>Fréquence</span><b>{interval} min</b></div>
  </div>
  <h3>Dernières pdp</h3>
  <div class="grid">{gallery}</div>
</div>
</body>
</html>"""


def render_page() -> str:
    ready = bot.is_ready()
    last = stats["last_post"]
    last_txt = (
        time.strftime("%d/%m %H:%M:%S", time.gmtime(last)) if last else "pas encore"
    )
    gallery = "".join(
        f'<img src="{html.escape(u)}" loading="lazy" alt="pdp">'
        for u in stats["recent"]
    ) or '<div class="empty">Rien d\'envoyé pour l\'instant.</div>'
    return PAGE.format(
        dot="#3ba55d" if ready else "#f0b232",
        status="Bot en ligne" if ready else "Bot en démarrage...",
        total=stats["total_sent"],
        last=last_txt,
        query=html.escape(stats["last_query"]),
        interval=INTERVAL_MIN,
        gallery=gallery,
    )


async def web_index(request: web.Request) -> web.Response:
    return web.Response(text=render_page(), content_type="text/html")


async def web_health(request: web.Request) -> web.Response:
    # URL à mettre sur cron-job.org pour garder le service éveillé
    return web.Response(text="ok")


async def start_web() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", web_index)
    app.router.add_get("/health", web_health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))  # Render donne le PORT tout seul
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"[Web] mini site lancé sur le port {port}")
    return runner


# ---------- Bot ----------
class PdpBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.session: aiohttp.ClientSession | None = None
        self.web_runner: web.AppRunner | None = None

    async def setup_hook(self):
        self.session = aiohttp.ClientSession()
        self.web_runner = await start_web()  # d'abord le site, Render détecte le port
        await self.tree.sync()
        auto_post.start()

    async def close(self):
        if self.web_runner:
            await self.web_runner.cleanup()
        if self.session:
            await self.session.close()
        await super().close()


bot = PdpBot()


@tasks.loop(minutes=INTERVAL_MIN)
async def auto_post():
    try:
        sent, query = await run_once(bot.session)
        print(f"[Auto] {sent} pdp envoyées ('{query}')")
    except Exception as e:
        print(f"[Auto] erreur : {e}")


@auto_post.before_loop
async def before_auto():
    await bot.wait_until_ready()


@bot.tree.command(name="pdp", description="Poster des pdp Pinterest maintenant")
@app_commands.describe(recherche="Ce que tu veux chercher (ex: pdp anime)")
@app_commands.default_permissions(manage_guild=True)
async def pdp(interaction: discord.Interaction, recherche: str | None = None):
    await interaction.response.defer(ephemeral=True)
    sent, query = await run_once(bot.session, recherche)
    if sent:
        await interaction.followup.send(f"✅ {sent} pdp envoyées ('{query}').", ephemeral=True)
    else:
        await interaction.followup.send(
            "❌ Rien trouvé (Pinterest a peut-être bloqué ou plus de nouvelles images). Réessaie.",
            ephemeral=True,
        )


@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user} — auto-post toutes les {INTERVAL_MIN} min")


if __name__ == "__main__":
    if not TOKEN or not WEBHOOK_URL:
        raise SystemExit("Il manque DISCORD_TOKEN ou WEBHOOK_URL dans les variables d'environnement.")
    bot.run(TOKEN)
