import json
import os
import time

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


class ReconnexionRapide(discord.backoff.ExponentialBackoff):
    """Si la connexion à Discord coupe (souvent un souci réseau de l'hébergeur),
    on réessaie au maximum toutes les 30 secondes au lieu d'attendre jusqu'à 17 minutes."""

    def delay(self):
        return min(super().delay(), 30.0)


discord.client.ExponentialBackoff = ReconnexionRapide


def charger_donnees():
    try:
        with open(FICHIER_DONNEES, encoding='utf-8') as f:
            donnees = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        donnees = {}
    donnees.setdefault('salons_creation', {})  # id du serveur -> id du salon « Créer un vocal »
    donnees.setdefault('vocaux_temporaires', [])  # ids des vocaux créés par le bot
    donnees.setdefault('salons_poemes', {})  # id du serveur -> id du salon des poèmes
    donnees.setdefault('salons_logs_poemes', {})  # id du serveur -> id du salon privé des modos
    donnees.setdefault('salons_confessions', {})  # id du serveur -> id du salon des confessions
    donnees.setdefault('salons_logs_confessions', {})  # id du serveur -> id du salon privé des modos
    donnees.setdefault('compteur_confessions', {})  # id du serveur -> numéro de la dernière confession
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
            print("➡️ Vérifie que le rôle du bot a « Voir », « Gérer les salons » et « Déplacer des membres » dans cette catégorie")
            return

        DONNEES['vocaux_temporaires'].append(nouveau.id)
        sauvegarder_donnees()

        try:
            await member.move_to(nouveau)
            print(f"Vocal créé pour {member}")
        except discord.HTTPException as e:
            # Il est parti avant d'être déplacé, ou il manque « Déplacer des membres » : on nettoie
            print(f"Impossible de déplacer {member} dans son vocal: {e}")
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


# ---------------------------------------------------------------------------
# Messages anonymes (poèmes et confessions)
# /poeme ou /confession ouvre un formulaire, le bot publie le texte sans auteur.
# L'auteur est envoyé uniquement dans un salon privé des modos (s'il est configuré).
# ---------------------------------------------------------------------------

DELAI_ENTRE_MESSAGES = 5 * 60  # secondes entre deux envois d'une même personne (anti-spam)
dernier_envoi = {}  # (type, id du membre) -> heure du dernier envoi (en mémoire seulement)

TYPES_ANONYMES = {
    'poeme': {
        'nom': "poème",
        'titre_formulaire': "Envoyer un poème anonyme",
        'label_texte': "Ton poème",
        'avec_titre': True,
        'couleur': discord.Color.from_rgb(155, 89, 182),
        'confirmation': "✅ Ton poème a été publié anonymement dans {salon} !",
        'pied': "✒️ Poème anonyme • envoie le tien avec /poeme",
        'titre_log': "📜 Nouveau poème anonyme",
        'cle_salon': 'salons_poemes',
        'cle_logs': 'salons_logs_poemes',
        'commande_salon': 'salon_poemes',
    },
    'confession': {
        'nom': "confession",
        'titre_formulaire': "Faire une confession anonyme",
        'label_texte': "Ta confession",
        'avec_titre': False,
        'couleur': discord.Color.from_rgb(52, 73, 94),
        'confirmation': "✅ Ta confession a été publiée anonymement dans {salon} !",
        'pied': "🤫 Confession anonyme • fais la tienne avec /confession",
        'titre_log': "🤫 Nouvelle confession anonyme",
        'cle_salon': 'salons_confessions',
        'cle_logs': 'salons_logs_confessions',
        'commande_salon': 'salon_confessions',
    },
}


class FormulaireAnonyme(discord.ui.Modal):
    def __init__(self, type_message):
        self.config = TYPES_ANONYMES[type_message]
        self.type_message = type_message
        super().__init__(title=self.config['titre_formulaire'])

        self.titre = None
        if self.config['avec_titre']:
            self.titre = discord.ui.TextInput(label="Titre (facultatif)", required=False, max_length=100)
            self.add_item(self.titre)
        self.texte = discord.ui.TextInput(
            label=self.config['label_texte'], style=discord.TextStyle.paragraph, max_length=4000
        )
        self.add_item(self.texte)

    async def on_submit(self, interaction):
        config = self.config
        salon_id = DONNEES[config['cle_salon']].get(str(interaction.guild_id))
        salon = interaction.guild.get_channel(salon_id) if salon_id else None
        if salon is None:
            await interaction.response.send_message(
                f"❌ Le salon des {config['nom']}s n'est pas configuré. "
                f"Demande à un admin de faire /{config['commande_salon']}.",
                ephemeral=True,
            )
            return

        titre = self.titre.value if self.titre else ''
        if self.type_message == 'confession':
            # Numéro de la confession (#1, #2, ...) propre à chaque serveur
            numero = DONNEES['compteur_confessions'].get(str(interaction.guild_id), 0) + 1
            titre = f"Confession #{numero}"

        embed = discord.Embed(title=titre or None, description=self.texte.value, color=config['couleur'])
        embed.set_footer(text=config['pied'])

        try:
            message = await salon.send(embed=embed)
        except discord.HTTPException:
            await interaction.response.send_message(
                f"❌ Je n'arrive pas à écrire dans le salon des {config['nom']}s (permissions ?).",
                ephemeral=True,
            )
            return

        if self.type_message == 'confession':
            DONNEES['compteur_confessions'][str(interaction.guild_id)] = numero
            sauvegarder_donnees()

        dernier_envoi[(self.type_message, interaction.user.id)] = time.monotonic()
        # Message visible uniquement par l'auteur, les membres ne savent pas qui l'a envoyé
        await interaction.response.send_message(
            config['confirmation'].format(salon=salon.mention) + "\n"
            "-# Les autres membres ne voient pas ton nom, seule l'équipe de modération peut le voir.",
            ephemeral=True,
        )
        await envoyer_log(interaction, config, message, titre)


async def envoyer_log(interaction, config, message, titre):
    """Envoie l'auteur dans le salon privé des modos (s'il est configuré)"""
    salon_id = DONNEES[config['cle_logs']].get(str(interaction.guild_id))
    salon_logs = interaction.guild.get_channel(salon_id) if salon_id else None
    if salon_logs is None:
        return

    auteur = interaction.user
    lignes = [f"**Auteur :** {auteur.mention} (`{auteur}` • ID `{auteur.id}`)"]
    if config['avec_titre'] or titre:
        lignes.append(f"**Titre :** {titre or '*sans titre*'}")
    lignes.append(f"**Message :** [voir le message]({message.jump_url})")

    embed = discord.Embed(
        title=config['titre_log'],
        description="\n".join(lignes),
        color=discord.Color.dark_grey(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_thumbnail(url=auteur.display_avatar.url)
    try:
        await salon_logs.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as e:
        print(f"Impossible d'écrire dans le salon des logs ({config['nom']}): {e}")


async def ouvrir_formulaire(interaction, type_message):
    depuis = time.monotonic() - dernier_envoi.get((type_message, interaction.user.id), -DELAI_ENTRE_MESSAGES)
    attente = DELAI_ENTRE_MESSAGES - depuis
    if attente > 0:
        await interaction.response.send_message(
            f"⏳ Attends encore {int(attente // 60) + 1} min avant d'en envoyer un autre.",
            ephemeral=True,
        )
        return
    await interaction.response.send_modal(FormulaireAnonyme(type_message))


async def configurer_salon(interaction, cle, salon, texte):
    DONNEES[cle][str(interaction.guild_id)] = salon.id
    sauvegarder_donnees()
    await interaction.response.send_message(texte.format(salon=salon.mention), ephemeral=True)


AVERTISSEMENT_LOGS = " Pense à rendre ce salon visible uniquement par les admins/modos !"


# --- Poèmes ---

@bot.tree.command(name="poeme", description="Envoyer un poème anonymement")
@discord.app_commands.guild_only()
async def poeme(interaction):
    await ouvrir_formulaire(interaction, 'poeme')


@bot.tree.command(name="salon_poemes", description="Choisir le salon où sont publiés les poèmes anonymes")
@discord.app_commands.guild_only()
@discord.app_commands.default_permissions(manage_guild=True)
async def salon_poemes(interaction, salon: discord.TextChannel):
    await configurer_salon(interaction, 'salons_poemes', salon,
                           "✅ Les poèmes anonymes seront publiés dans {salon}")


@bot.tree.command(name="salon_logs_poemes", description="Choisir le salon privé où les modos voient l'auteur des poèmes")
@discord.app_commands.guild_only()
@discord.app_commands.default_permissions(manage_guild=True)
async def salon_logs_poemes(interaction, salon: discord.TextChannel):
    await configurer_salon(interaction, 'salons_logs_poemes', salon,
                           "✅ L'auteur de chaque poème sera envoyé dans {salon}." + AVERTISSEMENT_LOGS)


# --- Confessions ---

@bot.tree.command(name="confession", description="Faire une confession anonyme")
@discord.app_commands.guild_only()
async def confession(interaction):
    await ouvrir_formulaire(interaction, 'confession')


@bot.tree.command(name="salon_confessions", description="Choisir le salon où sont publiées les confessions anonymes")
@discord.app_commands.guild_only()
@discord.app_commands.default_permissions(manage_guild=True)
async def salon_confessions(interaction, salon: discord.TextChannel):
    await configurer_salon(interaction, 'salons_confessions', salon,
                           "✅ Les confessions anonymes seront publiées dans {salon}")


@bot.tree.command(name="salon_logs_confessions", description="Choisir le salon privé où les modos voient l'auteur des confessions")
@discord.app_commands.guild_only()
@discord.app_commands.default_permissions(manage_guild=True)
async def salon_logs_confessions(interaction, salon: discord.TextChannel):
    await configurer_salon(interaction, 'salons_logs_confessions', salon,
                           "✅ L'auteur de chaque confession sera envoyé dans {salon}." + AVERTISSEMENT_LOGS)


@bot.event
async def setup_hook():
    # Enregistre les commandes slash (/poeme, /confession, ...) auprès de Discord
    await bot.tree.sync()


# Lancer le bot
if __name__ == "__main__":
    TOKEN = (os.getenv('TOKEN') or '').strip().strip('"').strip("'")
    if not TOKEN:
        print("❌ ERREUR: Le token n'est pas configuré!")
        print(f"Crée un fichier .env dans {DOSSIER} avec la ligne : TOKEN=ton_token")
        print("Fichiers trouvés dans ce dossier :", sorted(os.listdir(DOSSIER)))
    else:
        bot.run(TOKEN)
