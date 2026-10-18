"""
Bot PDP Pinterest -> Webhook Discord

- Toutes les X minutes, il va chercher des pdp sur Pinterest (recherches de ton choix)
- Il les envoie dans ton salon via un webhook (embeds avec l'image)
- Il garde en mémoire les images déjà postées (seen.json) pour pas faire de doublons
- Commande slash /pdp pour en poster direct (réservée à ceux qui ont "Gérer le serveur")
- Commande /salon pour choisir le salon où le bot envoie les pdp
- Commande /leave (réservée à TOI) : le bot quitte le serveur où tu la tapes
- Commande /leaveall (réservée à TOI) : le bot quitte TOUS ses serveurs (avec confirmation)
- Sécurité : si TOI (OWNER_ID) tu perds "Gérer le serveur" sur un serveur (ou si tu en pars),
  le bot quitte tout seul ce serveur
- Mini site web intégré (pour Render) :
    /        -> page de stats + galerie des dernières pdp
    /health  -> répond "ok" (pour cron-job.org)

Variables d'environnement (.env ou Render) :
    DISCORD_TOKEN = token du bot
    WEBHOOK_URL   = (optionnel) webhook par défaut, utilisé tant que /salon n'a pas été fait
    OWNER_ID      = (optionnel) ton ID Discord, déjà mis par défaut dans le code
    PORT          = donné automatiquement par Render (8080 par défaut en local)
"""

import os
import io
import re
import json
import time
import html
import random
import asyncio

from urllib.parse import quote

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
OWNER_ID = int(os.getenv("OWNER_ID") or 1535245254209577060)  # ton ID Discord

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

# Boards Pinterest PUBLICS (fiable à 100%) : colle les liens de boards de pdp ici
# ex: "https://www.pinterest.com/pseudo/nom-du-board/"
BOARDS = [
]

INTERVAL_MIN = 10      # toutes les X minutes
PICS_PER_POST = 5      # nombre de pdp par envoi (max 10)
EMBED_COLOR = 0x2B2D31
WEBHOOK_NAME = "PDP Bot"
SEEN_FILE = "seen.json"
CONFIG_FILE = "config.json"
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


# ---------- salons choisis avec /salon ----------
def load_config() -> dict:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"guilds": {}}


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f)


config = load_config()


def get_targets() -> list[str]:
    """Tous les webhooks où le bot doit poster."""
    urls = [v["webhook"] for v in config.get("guilds", {}).values()]
    if WEBHOOK_URL and WEBHOOK_URL not in urls:
        urls.append(WEBHOOK_URL)
    return urls


def target_for_guild(guild_id) -> str | None:
    entry = config.get("guilds", {}).get(str(guild_id))
    return entry["webhook"] if entry else WEBHOOK_URL

# stats affichées sur le mini site
stats = {
    "total_sent": 0,
    "last_post": None,   # timestamp
    "last_query": "-",
    "recent": [],        # dernières URLs postées
}


# ---------- Pinterest ----------
API_URL = "https://www.pinterest.com/resource/BaseSearchResource/get/"


def to_items(urls) -> list[str]:
    """Transforme des URLs en 'identifiant|url' sans doublons (taille 736x)."""
    results = {}
    for u in urls:
        hd = re.sub(r"/(?:\d+x|originals)/", "/736x/", u)
        key = hd.rsplit("/", 1)[-1]
        results[key] = hd
    return [f"{k}|{v}" for k, v in results.items()]


def parse_search_json(js: dict) -> list[str]:
    """Sort les URLs d'images de la réponse JSON de la recherche Pinterest."""
    results = js.get("resource_response", {}).get("data", {}).get("results", []) or []
    urls = []
    for pin in results:
        if not isinstance(pin, dict):
            continue
        images = pin.get("images") or {}
        img = images.get("736x") or images.get("474x") or images.get("orig") or images.get("236x")
        if isinstance(img, dict) and IMG_RE.fullmatch(img.get("url", "")):
            urls.append(img["url"])
    return urls


async def fetch_search(session: aiohttp.ClientSession, query: str) -> list[str]:
    """Recherche Pinterest via leur API interne (retourne du JSON)."""
    data = {
        "options": {
            "query": query,
            "scope": "pins",
            "page_size": 50,
            "bookmarks": [],
            "field_set_key": "unauth_react",
            "no_fetch_context_on_resource": False,
        },
        "context": {},
    }
    params = {
        "source_url": f"/search/pins/?q={quote(query)}&rs=typed",
        "data": json.dumps(data),
        "_": str(int(time.time() * 1000)),
    }
    headers = {
        **HEADERS,
        "Accept": "application/json, text/javascript, */*, q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "X-Pinterest-AppState": "active",
        "X-Pinterest-PWS-Handler": "www/search/[scope].js",
        "Referer": "https://www.pinterest.com/",
    }
    try:
        async with session.get(
            API_URL, params=params, headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            if r.status != 200:
                print(f"[Pinterest] recherche '{query}' -> statut {r.status}")
                return []
            js = await r.json(content_type=None)
    except Exception as e:
        print(f"[Pinterest] erreur recherche : {e}")
        return []
    return parse_search_json(js)


async def fetch_board(session: aiohttp.ClientSession, board_url: str) -> list[str]:
    """Lit un board public via son flux RSS."""
    url = board_url.split("?")[0].rstrip("/")
    if not url.endswith(".rss"):
        url += ".rss"
    try:
        async with session.get(
            url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)
        ) as r:
            if r.status != 200:
                print(f"[Pinterest] board {url} -> statut {r.status}")
                return []
            text = await r.text()
    except Exception as e:
        print(f"[Pinterest] erreur board : {e}")
        return []
    return IMG_RE.findall(text)


async def fetch_pins(session: aiohttp.ClientSession, source: str) -> list[str]:
    """source = lien de board (http...) ou mot de recherche."""
    if source.startswith("http"):
        urls = await fetch_board(session, source)
    else:
        urls = await fetch_search(session, source)
    return to_items(urls)


SIZES = ["736x", "564x", "474x", "originals", "236x"]
MAX_BYTES = 7_000_000


async def get_new_pics(session: aiohttp.ClientSession, query: str):
    raw = await fetch_pins(session, query)
    print(f"[Pinterest] {len(raw)} images trouvées pour '{query}'")
    fresh = []
    for item in raw:
        key, url = item.split("|", 1)
        if key not in seen_images:
            fresh.append((key, url))
    random.shuffle(fresh)
    return fresh


async def download_image(session: aiohttp.ClientSession, url: str):
    """Télécharge l'image (en essayant plusieurs tailles). Retourne (url, bytes) ou None."""
    base = re.sub(r"/(?:\d+x|originals)/", "/@@/", url)
    headers = {**HEADERS, "Referer": "https://www.pinterest.com/"}
    for size in SIZES:
        candidate = base.replace("@@", size)
        try:
            async with session.get(
                candidate, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                if r.status != 200 or not r.content_type.startswith("image/"):
                    continue
                data = await r.read()
                if 0 < len(data) <= MAX_BYTES:
                    return candidate, data
        except Exception as e:
            print(f"[Image] erreur : {e}")
    print(f"[Image] impossible de télécharger {url}")
    return None


# ---------- Envoi webhook ----------
async def send_pics(session: aiohttp.ClientSession, candidates, query: str, amount: int, webhook_url: str) -> int:
    files, embeds, shown = [], [], []
    for key, url in candidates[:40]:
        if len(files) >= amount:
            break
        seen_images.add(key)  # même si ça rate, on ne réessaie pas la même
        got = await download_image(session, url)
        if not got:
            continue
        used_url, data = got
        files.append(discord.File(io.BytesIO(data), filename=key))
        e = discord.Embed(color=EMBED_COLOR)
        e.set_image(url=f"attachment://{key}")
        embeds.append(e)
        shown.append(used_url)
    save_seen(seen_images)

    if not files:
        return 0

    embeds[0].title = f"📌 {query}"
    webhook = discord.Webhook.from_url(webhook_url, session=session)
    await webhook.send(embeds=embeds, files=files, username=WEBHOOK_NAME)

    stats["total_sent"] += len(files)
    stats["last_post"] = time.time()
    stats["last_query"] = query
    stats["recent"] = (shown + stats["recent"])[:24]
    return len(files)


async def run_once(session: aiohttp.ClientSession, source: str | None, webhook_url: str) -> tuple[int, str]:
    source = source or random.choice(QUERIES + BOARDS)
    label = source.split("?")[0].rstrip("/").rsplit("/", 1)[-1] if source.startswith("http") else source
    candidates = await get_new_pics(session, source)
    sent = await send_pics(session, candidates, label, min(PICS_PER_POST, 10), webhook_url)
    return sent, label


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
        guard.start()

    async def close(self):
        if self.web_runner:
            await self.web_runner.cleanup()
        if self.session:
            await self.session.close()
        await super().close()


bot = PdpBot()


@tasks.loop(minutes=INTERVAL_MIN)
async def auto_post():
    targets = get_targets()
    if not targets:
        print("[Auto] aucun salon configuré (fais /salon)")
        return
    for url in targets:
        try:
            sent, label = await run_once(bot.session, None, url)
            print(f"[Auto] {sent} pdp envoyées ('{label}')")
        except Exception as e:
            print(f"[Auto] erreur : {e}")


@auto_post.before_loop
async def before_auto():
    await bot.wait_until_ready()


@bot.tree.command(name="salon", description="Choisir le salon où le bot envoie les pdp")
@app_commands.describe(salon="Le salon où poster les pdp")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def salon(interaction: discord.Interaction, salon: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    try:
        hooks = await salon.webhooks()
        hook = next(
            (h for h in hooks if h.user and h.user.id == bot.user.id and h.token), None
        )
        if hook is None:
            hook = await salon.create_webhook(name=WEBHOOK_NAME)
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Il me manque la permission **Gérer les webhooks** dans ce salon.",
            ephemeral=True,
        )
        return
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ Erreur Discord : {e}", ephemeral=True)
        return

    config.setdefault("guilds", {})[str(interaction.guild_id)] = {
        "channel_id": salon.id,
        "webhook": hook.url,
    }
    save_config(config)
    await interaction.followup.send(
        f"✅ Les pdp seront envoyées dans {salon.mention}.", ephemeral=True
    )


@bot.tree.command(name="pdp", description="Poster des pdp Pinterest maintenant")
@app_commands.describe(recherche="Ce que tu veux chercher (ex: pdp anime) ou un lien de board")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def pdp(interaction: discord.Interaction, recherche: str | None = None):
    url = target_for_guild(interaction.guild_id)
    if not url:
        await interaction.response.send_message(
            "❌ Choisis d'abord un salon avec `/salon`.", ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    sent, label = await run_once(bot.session, recherche, url)
    if sent:
        await interaction.followup.send(f"✅ {sent} pdp envoyées ('{label}').", ephemeral=True)
    else:
        await interaction.followup.send(
            "❌ Rien trouvé (Pinterest a peut-être bloqué ou plus de nouvelles images). Réessaie.",
            ephemeral=True,
        )


@bot.tree.command(name="leave", description="Faire quitter ce serveur au bot (réservé au propriétaire du bot)")
@app_commands.guild_only()
async def leave(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "❌ Tu n'as pas le droit d'utiliser cette commande.", ephemeral=True
        )
        return
    await interaction.response.send_message("👋 Je quitte le serveur.", ephemeral=True)
    await interaction.guild.leave()


class ConfirmLeaveAll(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    @discord.ui.button(label="Oui, quitter tous les serveurs", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ Pas pour toi.", ephemeral=True)
            return
        self.stop()
        guilds = list(bot.guilds)
        await interaction.response.edit_message(
            content=f"👋 Je quitte {len(guilds)} serveur(s)...", view=None
        )
        for g in guilds:
            try:
                await g.leave()
                print(f"[LeaveAll] quitté '{g.name}'")
            except discord.HTTPException as e:
                print(f"[LeaveAll] impossible de quitter '{g.name}' : {e}")
            await asyncio.sleep(1)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ Pas pour toi.", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(content="Annulé ✅", view=None)


@bot.tree.command(name="leaveall", description="Faire quitter TOUS les serveurs au bot (réservé au propriétaire du bot)")
async def leaveall(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "❌ Tu n'as pas le droit d'utiliser cette commande.", ephemeral=True
        )
        return
    await interaction.response.send_message(
        f"⚠️ Le bot est sur **{len(bot.guilds)}** serveur(s). Tout quitter ?",
        view=ConfirmLeaveAll(),
        ephemeral=True,
    )


# ---------- Sécurité : le bot part si TOI tu perds tes perms ----------
async def owner_still_allowed(guild: discord.Guild):
    """True = ok / False = le bot doit partir / None = impossible de savoir (on ne fait rien)."""
    if guild.owner_id == OWNER_ID:
        return True
    try:
        member = await guild.fetch_member(OWNER_ID)
    except discord.NotFound:
        return False  # tu n'es plus sur le serveur
    except discord.HTTPException:
        return None   # erreur Discord, on ne quitte pas par erreur
    perms = member.guild_permissions
    return perms.administrator or perms.manage_guild


async def check_guild(guild: discord.Guild):
    if not OWNER_ID:
        return
    if await owner_still_allowed(guild) is False:
        print(f"[Sécurité] tu n'as plus les perms sur '{guild.name}' -> je quitte")
        try:
            await guild.leave()
        except discord.HTTPException as e:
            print(f"[Sécurité] impossible de quitter : {e}")


@tasks.loop(minutes=3)
async def guard():
    for guild in list(bot.guilds):
        await check_guild(guild)
        await asyncio.sleep(1)


@guard.before_loop
async def before_guard():
    await bot.wait_until_ready()


@bot.event
async def on_guild_join(guild: discord.Guild):
    await check_guild(guild)


@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    await check_guild(after.guild)


@bot.event
async def on_guild_role_delete(role: discord.Role):
    await check_guild(role.guild)


@bot.event
async def on_guild_remove(guild: discord.Guild):
    # on oublie le salon choisi pour ce serveur
    if config.get("guilds", {}).pop(str(guild.id), None):
        save_config(config)


@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user} — auto-post toutes les {INTERVAL_MIN} min")
    if not OWNER_ID:
        print("[Sécurité] OWNER_ID non défini -> le bot ne quittera aucun serveur")


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Il manque DISCORD_TOKEN dans les variables d'environnement.")
    bot.run(TOKEN)
