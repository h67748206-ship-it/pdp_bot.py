import asyncio
import json
import os
from pathlib import Path
from threading import Thread

import discord
from discord.ext import commands
from flask import Flask, jsonify

# ---------- Site web (Render) ----------
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
    return PAGE.replace("__STATUT__", etat["statut"]).replace("__COMMANDES__", construire_commandes())


@app.route("/health")
def health():
    return jsonify(status="ok", bot=etat["nom"], statut=etat["statut"])


def keep_alive():
    port = int(os.environ.get("PORT", 10000))
    Thread(
        target=lambda: app.run(host="0.0.0.0", port=port),
        daemon=True,
    ).start()


def set_status(texte: str):
    etat["statut"] = texte


# ---------- Configuration du bot ----------
TOKEN = os.environ["DISCORD_TOKEN"]
BOT_OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "0"))
DATA_FILE = Path("data.json")
DELAI = 0.5

NIVEAUX_PAR_DEFAUT = {
    "panneau": "all",
    "setup": "wl",
    "find": "wl",
    "bringall": "wl",
    "bringcc": "wl",
    "mv": "wl",
    "join": "wl",
    "follow": "wl",
    "unfollow": "wl",
}

suivis: dict[int, int] = {}


def charger() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"prefix": "=", "owners": [], "guilds": {}}


data = charger()


def sauver():
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def guilde(guild_id: int) -> dict:
    g = data["guilds"].setdefault(str(guild_id), {})
    g.setdefault("generator", None)
    g.setdefault("temp", {})
    g.setdefault("whitelist", [])
    g.setdefault("perms", {})
    return g


def get_prefix(bot, message):
    return data["prefix"]


def est_owner(membre: discord.Member) -> bool:
    return (
        membre.id == BOT_OWNER_ID
        or membre.id in data["owners"]
        or membre.guild.owner_id == membre.id
    )


def niveau(guild: discord.Guild, commande: str) -> str:
    return guilde(guild.id)["perms"].get(commande, NIVEAUX_PAR_DEFAUT.get(commande, "all"))


def autorise(ctx: commands.Context, commande: str) -> bool:
    if est_owner(ctx.author):
        return True
    niv = niveau(ctx.guild, commande)
    if niv == "all":
        return True
    if niv == "wl":
        return ctx.author.id in guilde(ctx.guild.id)["whitelist"]
    return False


def requis(commande: str):
    async def predicat(ctx):
        if autorise(ctx, commande):
            return True
        raise commands.CheckFailure(f"Tu n'as pas la permission d'utiliser `{ctx.prefix}{commande}`.")
    return commands.check(predicat)


def owner_only():
    async def predicat(ctx):
        if est_owner(ctx.author):
            return True
        raise commands.CheckFailure("Cette commande est réservée aux owners.")
    return commands.check(predicat)


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)


# ---------- Salons temporaires ----------
async def creer_temp(membre: discord.Member, generateur: discord.VoiceChannel):
    g = guilde(membre.guild.id)
    overwrites = {
        membre: discord.PermissionOverwrite(
            view_channel=True, connect=True, manage_channels=True, move_members=True
        )
    }
    salon = await membre.guild.create_voice_channel(
        name=f"{membre.display_name} • vocal",
        category=generateur.category,
        overwrites=overwrites,
        reason="Salon vocal temporaire",
    )
    g["temp"][str(salon.id)] = membre.id
    sauver()
    try:
        await membre.move_to(salon)
    except discord.HTTPException:
        await nettoyer(salon, force=True)


async def nettoyer(salon: discord.VoiceChannel, force: bool = False):
    g = guilde(salon.guild.id)
    if str(salon.id) not in g["temp"]:
        return
    if force or len(salon.members) == 0:
        g["temp"].pop(str(salon.id), None)
        sauver()
        try:
            await salon.delete(reason="Salon temporaire vide")
        except discord.HTTPException:
            pass


async def suivre(leader: discord.Member, salon: discord.VoiceChannel):
    for suiveur_id, leader_id in list(suivis.items()):
        if leader_id != leader.id:
            continue
        suiveur = leader.guild.get_member(suiveur_id)
        if suiveur and suiveur.voice and suiveur.voice.channel != salon:
            try:
                await suiveur.move_to(salon)
            except discord.HTTPException:
                pass


@bot.event
async def on_ready():
    set_status("en ligne")
    print(f"Connecté en tant que {bot.user} (préfixe : {data['prefix']})")


@bot.event
async def on_voice_state_update(membre, avant, apres):
    if membre.bot:
        return
    if avant.channel and avant.channel != apres.channel:
        await nettoyer(avant.channel)
    if apres.channel and apres.channel != avant.channel:
        await suivre(membre, apres.channel)
        if guilde(membre.guild.id)["generator"] == apres.channel.id:
            await creer_temp(membre, apres.channel)


# ---------- Panneau de gestion ----------
class RenommerModal(discord.ui.Modal, title="Renommer le salon"):
    nom = discord.ui.TextInput(label="Nouveau nom", max_length=100)

    def __init__(self, salon_id: int):
        super().__init__()
        self.salon_id = salon_id

    async def on_submit(self, interaction: discord.Interaction):
        salon = interaction.guild.get_channel(self.salon_id)
        if salon is None:
            return await interaction.response.send_message("Salon introuvable.", ephemeral=True)
        await salon.edit(name=self.nom.value)
        await interaction.response.send_message("Salon renommé ✅", ephemeral=True)


class Panneau(discord.ui.View):
    def __init__(self, salon_id: int, proprio_id: int):
        super().__init__(timeout=None)
        self.salon_id = salon_id
        self.proprio_id = proprio_id

    async def verifier(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.proprio_id or est_owner(interaction.user):
            return True
        await interaction.response.send_message(
            "Seul le propriétaire du salon peut faire ça.", ephemeral=True
        )
        return False

    async def _modifier(self, interaction, **kwargs):
        salon = interaction.guild.get_channel(self.salon_id)
        if salon is None:
            return await interaction.response.send_message("Salon introuvable.", ephemeral=True)
        await salon.set_permissions(interaction.guild.default_role, **kwargs)
        await interaction.response.send_message("Fait ✅", ephemeral=True)

    @discord.ui.button(label="Verrouiller", emoji="🔒", style=discord.ButtonStyle.secondary)
    async def verrouiller(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.verifier(interaction):
            await self._modifier(interaction, connect=False)

    @discord.ui.button(label="Déverrouiller", emoji="🔓", style=discord.ButtonStyle.secondary)
    async def deverrouiller(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.verifier(interaction):
            await self._modifier(interaction, connect=None)

    @discord.ui.button(label="Cacher", emoji="🙈", style=discord.ButtonStyle.secondary)
    async def cacher(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.verifier(interaction):
            await self._modifier(interaction, view_channel=False)

    @discord.ui.button(label="Afficher", emoji="👁️", style=discord.ButtonStyle.secondary)
    async def afficher(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.verifier(interaction):
            await self._modifier(interaction, view_channel=None)

    @discord.ui.button(label="Renommer", emoji="✏️", style=discord.ButtonStyle.primary)
    async def renommer(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.verifier(interaction):
            await interaction.response.send_modal(RenommerModal(self.salon_id))


# ---------- Commandes : tout le monde ----------
@bot.command(name="panneau")
@requis("panneau")
async def panneau(ctx):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.send("Tu dois être dans un salon vocal.")
    salon = ctx.author.voice.channel
    proprio = guilde(ctx.guild.id)["temp"].get(str(salon.id))
    if proprio is None:
        return await ctx.send("Ce salon n'est pas un vocal temporaire.")
    embed = discord.Embed(
        title=f"Panneau de {salon.name}",
        description=f"Propriétaire : <@{proprio}>",
        color=0x33C6FF,
    )
    await ctx.send(embed=embed, view=Panneau(salon.id, proprio))


# ---------- Commandes : whitelist ----------
@bot.command(name="setup")
@requis("setup")
async def setup(ctx, salon: discord.VoiceChannel):
    guilde(ctx.guild.id)["generator"] = salon.id
    sauver()
    await ctx.send(f"Salon générateur défini sur **{salon.name}**.")


@bot.command(name="find")
@requis("find")
async def find(ctx, membre: discord.Member):
    if not membre.voice or not membre.voice.channel:
        return await ctx.send(f"{membre.display_name} n'est pas en vocal.")
    await ctx.send(f"{membre.display_name} est dans {membre.voice.channel.mention}.")


@bot.command(name="bringall")
@requis("bringall")
async def bringall(ctx):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.send("Tu dois être dans un salon vocal.")
    cible = ctx.author.voice.channel
    total = 0
    for salon in ctx.guild.voice_channels:
        if salon == cible:
            continue
        for membre in list(salon.members):
            if membre.bot:
                continue
            try:
                await membre.move_to(cible)
                total += 1
                await asyncio.sleep(DELAI)
            except discord.HTTPException:
                pass
    await ctx.send(f"{total} membre(s) ramené(s) dans {cible.mention}.")


@bot.command(name="bringcc")
@requis("bringcc")
async def bringcc(ctx, source: discord.CategoryChannel, cible: discord.CategoryChannel):
    salons_cible = cible.voice_channels
    if not salons_cible:
        return await ctx.send("La catégorie cible n'a aucun salon vocal.")
    membres = [m for s in source.voice_channels for m in s.members if not m.bot]
    for i, membre in enumerate(membres):
        try:
            await membre.move_to(salons_cible[i % len(salons_cible)])
            await asyncio.sleep(DELAI)
        except discord.HTTPException:
            pass
    await ctx.send(f"{len(membres)} membre(s) réparti(s) sur {len(salons_cible)} salon(s).")


# ---------- Commandes : outils vocaux ----------
@bot.command(name="mv")
@requis("mv")
async def mv(ctx, membre: discord.Member):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.send("Tu dois être dans un salon vocal.")
    if not membre.voice or not membre.voice.channel:
        return await ctx.send(f"{membre.display_name} n'est pas en vocal.")
    await membre.move_to(ctx.author.voice.channel)


@bot.command(name="join")
@requis("join")
async def join(ctx, membre: discord.Member):
    if not membre.voice or not membre.voice.channel:
        return await ctx.send(f"{membre.display_name} n'est pas en vocal.")
    await ctx.author.move_to(membre.voice.channel)


@bot.command(name="follow")
@requis("follow")
async def follow(ctx, membre: discord.Member):
    suivis[membre.id] = ctx.author.id
    if ctx.author.voice and ctx.author.voice.channel:
        try:
            await membre.move_to(ctx.author.voice.channel)
        except discord.HTTPException:
            pass
    await ctx.send(f"{membre.display_name} te suit maintenant de salon en salon.")


@bot.command(name="unfollow")
@requis("unfollow")
async def unfollow(ctx, membre: discord.Member):
    if suivis.pop(membre.id, None) is None:
        return await ctx.send(f"{membre.display_name} ne te suivait pas.")
    await ctx.send(f"{membre.display_name} ne te suit plus.")


# ---------- Commandes : owner ----------
@bot.command(name="owner")
@owner_only()
async def owner(ctx, membre: discord.Member):
    if membre.id in data["owners"]:
        data["owners"].remove(membre.id)
        msg = f"{membre.display_name} n'est plus owner."
    else:
        data["owners"].append(membre.id)
        msg = f"{membre.display_name} est maintenant owner."
    sauver()
    await ctx.send(msg)


@bot.command(name="wl")
@owner_only()
async def wl(ctx, membre: discord.Member):
    g = guilde(ctx.guild.id)
    if membre.id in g["whitelist"]:
        g["whitelist"].remove(membre.id)
        msg = f"{membre.display_name} retiré de la whitelist."
    else:
        g["whitelist"].append(membre.id)
        msg = f"{membre.display_name} ajouté à la whitelist."
    sauver()
    await ctx.send(msg)


@bot.command(name="perm")
@owner_only()
async def perm(ctx, commande: str = None, niveau_voulu: str = None):
    if commande is None:
        lignes = [f"`{c}` : {niveau(ctx.guild, c)}" for c in NIVEAUX_PAR_DEFAUT]
        return await ctx.send("**Niveaux requis** (all / wl / owner) :\n" + "\n".join(lignes))
    if commande not in NIVEAUX_PAR_DEFAUT or niveau_voulu not in ("all", "wl", "owner"):
        return await ctx.send("Usage : `=perm <commande> <all|wl|owner>`")
    guilde(ctx.guild.id)["perms"][commande] = niveau_voulu
    sauver()
    await ctx.send(f"`{commande}` nécessite maintenant : **{niveau_voulu}**.")


@bot.command(name="prefix")
@owner_only()
async def prefix(ctx, nouveau: str):
    data["prefix"] = nouveau
    sauver()
    await ctx.send(f"Préfixe changé en `{nouveau}`.")


@bot.command(name="perso")
@owner_only()
async def perso(ctx, champ: str, *, valeur: str = None):
    if champ == "nom" and valeur:
        await bot.user.edit(username=valeur)
    elif champ == "statut" and valeur:
        await bot.change_presence(activity=discord.CustomActivity(name=valeur))
    elif champ == "avatar" and ctx.message.attachments:
        image = await ctx.message.attachments[0].read()
        await bot.user.edit(avatar=image)
    else:
        return await ctx.send("Usage : `=perso nom <texte>`, `=perso statut <texte>` ou `=perso avatar` (avec image).")
    await ctx.send("Mis à jour ✅")


# ---------- Gestion des erreurs ----------
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        msg = str(error) or "Permission refusée."
    elif isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        msg = f"Utilisation incorrecte ou élément introuvable. Essaie `{ctx.prefix}{ctx.command}`."
    elif isinstance(error, discord.Forbidden):
        msg = "Il me manque une permission Discord pour faire ça."
    else:
        print(f"Erreur sur {ctx.command}: {error}")
        msg = "Une erreur est survenue."
    await ctx.send(msg, delete_after=8)


# ---------- Lancement ----------
keep_alive()
bot.run(TOKEN)
