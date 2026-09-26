import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

# Charger les variables d'environnement (fichier .env en local, variables Railway en ligne)
load_dotenv()

# Noms utilisés par le bot (tu peux les changer ici)
NOM_CATEGORIE = "🔊 Vocaux"
NOM_SALON_CREATION = "➕ Créer un vocal"
NOM_VOCAL_TEMPORAIRE = "🔊 Vocal de {pseudo}"

# Configuration du bot
intents = discord.Intents.default()
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents)


async def preparer_serveur(guild):
    """Crée la catégorie et le salon « Créer un vocal » s'ils n'existent pas,
    puis supprime les vocaux temporaires restés vides (ex: après un redémarrage)."""

    categorie = discord.utils.get(guild.categories, name=NOM_CATEGORIE)
    if categorie is None:
        categorie = await guild.create_category(NOM_CATEGORIE)
        print(f"[{guild.name}] Catégorie créée")

    salon_creation = discord.utils.get(categorie.voice_channels, name=NOM_SALON_CREATION)
    if salon_creation is None:
        salon_creation = await categorie.create_voice_channel(NOM_SALON_CREATION)
        print(f"[{guild.name}] Salon de création créé")

    for salon in categorie.voice_channels:
        if salon.id != salon_creation.id and len(salon.members) == 0:
            await salon.delete(reason="Vocal temporaire vide")

    return salon_creation


def est_vocal_temporaire(salon):
    """Un vocal temporaire = n'importe quel vocal de la catégorie, sauf le salon de création."""
    return (
        isinstance(salon, discord.VoiceChannel)
        and salon.category is not None
        and salon.category.name == NOM_CATEGORIE
        and salon.name != NOM_SALON_CREATION
    )


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
async def on_voice_state_update(member, before, after):
    """Quand quelqu'un rejoint / quitte / change de vocal"""

    # 1) Il rejoint « Créer un vocal » -> on lui crée son propre vocal et on l'y déplace
    if (
        after.channel is not None
        and after.channel != before.channel
        and after.channel.name == NOM_SALON_CREATION
        and after.channel.category is not None
        and after.channel.category.name == NOM_CATEGORIE
    ):
        categorie = after.channel.category
        nom = NOM_VOCAL_TEMPORAIRE.format(pseudo=member.display_name)[:100]

        # Le créateur peut renommer / limiter / expulser dans SON vocal
        permissions = dict(categorie.overwrites)
        permissions[member] = discord.PermissionOverwrite(
            manage_channels=True, move_members=True, connect=True
        )

        try:
            try:
                nouveau = await categorie.create_voice_channel(nom, overwrites=permissions)
            except discord.Forbidden:
                # Pas la permission « Gérer les rôles » : on crée sans droits spéciaux
                nouveau = await categorie.create_voice_channel(nom)
        except discord.HTTPException as e:
            print(f"Impossible de créer le vocal pour {member}: {e}")
            return

        try:
            await member.move_to(nouveau)
            print(f"Vocal créé pour {member}")
        except discord.HTTPException:
            # Il est parti avant d'être déplacé : on nettoie
            await nouveau.delete(reason="Créateur parti")
            return

    # 2) Il quitte un vocal temporaire -> si le vocal est vide, on le supprime
    if (
        before.channel is not None
        and before.channel != after.channel
        and est_vocal_temporaire(before.channel)
        and len(before.channel.members) == 0
    ):
        try:
            await before.channel.delete(reason="Vocal temporaire vide")
        except discord.NotFound:
            pass  # déjà supprimé
        except discord.HTTPException as e:
            print(f"Impossible de supprimer {before.channel.name}: {e}")


# Lancer le bot
if __name__ == "__main__":
    TOKEN = os.getenv('TOKEN')
    if TOKEN is None:
        print("❌ ERREUR: Le token n'est pas configuré!")
        print("Ajoute la variable TOKEN dans les variables d'environnement de Railway")
    else:
        bot.run(TOKEN)
