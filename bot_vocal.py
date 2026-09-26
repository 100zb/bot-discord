import json
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

# Charger le token depuis un fichier .env ou TOKEN.env placé à côté de ce fichier
DOSSIER = os.path.dirname(os.path.abspath(__file__))
for fichier in ('.env', 'TOKEN.env', '.env.txt', 'TOKEN.env.txt'):
    load_dotenv(os.path.join(DOSSIER, fichier))

# Noms utilisés par le bot (tu peux les changer ici)
NOM_SALON_CREATION = "➕ Créer un vocal"
NOM_VOCAL_TEMPORAIRE = "🔊 Vocal de {pseudo}"

# Fichier où le bot retient quel salon est « Créer un vocal » et quels vocaux sont temporaires
FICHIER_DONNEES = os.path.join(DOSSIER, 'donnees_vocal.json')

# Configuration du bot
intents = discord.Intents.default()
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents)


def charger_donnees():
    try:
        with open(FICHIER_DONNEES, encoding='utf-8') as f:
            donnees = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        donnees = {}
    donnees.setdefault('salons_creation', {})  # id du serveur -> id du salon « Créer un vocal »
    donnees.setdefault('vocaux_temporaires', [])  # ids des vocaux créés par le bot
    return donnees


def sauvegarder_donnees():
    with open(FICHIER_DONNEES, 'w', encoding='utf-8') as f:
        json.dump(DONNEES, f, indent=2)


DONNEES = charger_donnees()


async def preparer_serveur(guild):
    """Crée le salon « Créer un vocal » s'il n'existe pas encore,
    puis supprime les vocaux temporaires restés vides (ex: après un redémarrage)."""

    salon_id = DONNEES['salons_creation'].get(str(guild.id))
    salon_creation = guild.get_channel(salon_id) if salon_id else None

    if salon_creation is None:
        # Reprendre un salon existant du même nom, sinon en créer un (sans catégorie)
        salon_creation = discord.utils.get(guild.voice_channels, name=NOM_SALON_CREATION)
        if salon_creation is None:
            salon_creation = await guild.create_voice_channel(NOM_SALON_CREATION)
            print(f"[{guild.name}] Salon « {NOM_SALON_CREATION} » créé, déplace-le où tu veux")
        DONNEES['salons_creation'][str(guild.id)] = salon_creation.id
        sauvegarder_donnees()

    for salon in guild.voice_channels:
        if salon.id in DONNEES['vocaux_temporaires'] and len(salon.members) == 0:
            await supprimer_vocal(salon)


def est_salon_creation(salon):
    return salon is not None and DONNEES['salons_creation'].get(str(salon.guild.id)) == salon.id


async def supprimer_vocal(salon):
    try:
        await salon.delete(reason="Vocal temporaire vide")
    except discord.NotFound:
        pass  # déjà supprimé
    except discord.HTTPException as e:
        print(f"Impossible de supprimer {salon.name}: {e}")
        return
    if salon.id in DONNEES['vocaux_temporaires']:
        DONNEES['vocaux_temporaires'].remove(salon.id)
        sauvegarder_donnees()


@bot.event
async def on_ready():
    print(f'{bot.user} est connecté!')
    for guild in bot.guilds:
        try:
            await preparer_serveur(guild)
        except discord.Forbidden:
            print(f"[{guild.name}] ❌ Il me manque la permission « Gérer les salons »")
    print('Bot prêt à fonctionner')


@bot.event
async def on_guild_join(guild):
    """Quand le bot est ajouté sur un nouveau serveur"""
    try:
        await preparer_serveur(guild)
    except discord.Forbidden:
        print(f"[{guild.name}] ❌ Il me manque la permission « Gérer les salons »")


@bot.event
async def on_guild_channel_delete(salon):
    """Si quelqu'un supprime le salon « Créer un vocal », le bot en recrée un"""
    if salon.id in DONNEES['vocaux_temporaires']:
        DONNEES['vocaux_temporaires'].remove(salon.id)
        sauvegarder_donnees()
    if est_salon_creation(salon):
        del DONNEES['salons_creation'][str(salon.guild.id)]
        sauvegarder_donnees()
        await preparer_serveur(salon.guild)


@bot.event
async def on_voice_state_update(member, before, after):
    """Quand quelqu'un rejoint / quitte / change de vocal"""

    # 1) Il rejoint « Créer un vocal » -> on lui crée son vocal juste à côté et on l'y déplace
    if after.channel is not None and after.channel != before.channel and est_salon_creation(after.channel):
        categorie = after.channel.category  # même catégorie que le salon (ou aucune)
        nom = NOM_VOCAL_TEMPORAIRE.format(pseudo=member.display_name)[:100]

        # Le créateur peut renommer / limiter / expulser dans SON vocal
        permissions = dict(categorie.overwrites) if categorie else {}
        permissions[member] = discord.PermissionOverwrite(
            manage_channels=True, move_members=True, connect=True
        )

        try:
            try:
                nouveau = await member.guild.create_voice_channel(
                    nom, category=categorie, overwrites=permissions
                )
            except discord.Forbidden:
                # Pas la permission « Gérer les rôles » : on crée sans droits spéciaux
                nouveau = await member.guild.create_voice_channel(nom, category=categorie)
        except discord.HTTPException as e:
            print(f"Impossible de créer le vocal pour {member}: {e}")
            return

        DONNEES['vocaux_temporaires'].append(nouveau.id)
        sauvegarder_donnees()

        try:
            await member.move_to(nouveau)
            print(f"Vocal créé pour {member}")
        except discord.HTTPException:
            # Il est parti avant d'être déplacé : on nettoie
            await supprimer_vocal(nouveau)
            return

    # 2) Il quitte un vocal temporaire -> si le vocal est vide, on le supprime
    if (
        before.channel is not None
        and before.channel != after.channel
        and before.channel.id in DONNEES['vocaux_temporaires']
        and len(before.channel.members) == 0
    ):
        await supprimer_vocal(before.channel)


# Lancer le bot
if __name__ == "__main__":
    TOKEN = (os.getenv('TOKEN') or '').strip().strip('"').strip("'")
    if not TOKEN:
        print("❌ ERREUR: Le token n'est pas configuré!")
        print(f"Crée un fichier .env dans {DOSSIER} avec la ligne : TOKEN=ton_token")
        print("Fichiers trouvés dans ce dossier :", sorted(os.listdir(DOSSIER)))
    else:
        bot.run(TOKEN)
