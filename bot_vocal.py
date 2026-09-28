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
    donnees.setdefault('en_attente', {})  # id du message des modos -> poème/confession en attente de validation
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
# /poeme ou /confession ouvre un formulaire. Le texte part d'abord dans le salon
# privé des modos (avec le nom de l'auteur) où ils peuvent Accepter / Refuser /
# Refuser avec une raison. S'il est accepté, le bot le publie sans auteur.
# ---------------------------------------------------------------------------

DELAI_ENTRE_MESSAGES = 5 * 60  # secondes entre deux envois d'une même personne (anti-spam)
dernier_envoi = {}  # (type, id du membre) -> heure du dernier envoi (en mémoire seulement)

TYPES_ANONYMES = {
    'poeme': {
        'nom': "poème",
        'le': "Ton poème",
        'titre_formulaire': "Envoyer un poème anonyme",
        'label_texte': "Ton poème",
        'avec_titre': True,
        'couleur': discord.Color.from_rgb(155, 89, 182),
        'pied': "✒️ Poème anonyme • envoie le tien avec le bouton ci-dessous",
        'bouton': "Envoyer un poème anonyme",
        'emoji': "✒️",
        'panneau_titre': "✒️ Poèmes anonymes",
        'panneau_texte': "Tu écris des poèmes mais tu n'oses pas les signer ? Partage-les ici **anonymement** !",
        'titre_log': "📜 Poème anonyme",
        'accorde': "",  # « publié » / « refusé »
        'il': "il",
        'cle_salon': 'salons_poemes',
        'cle_logs': 'salons_logs_poemes',
        'commande_salon': 'salon_poemes',
        'commande_logs': 'salon_logs_poemes',
    },
    'confession': {
        'nom': "confession",
        'le': "Ta confession",
        'titre_formulaire': "Faire une confession anonyme",
        'label_texte': "Ta confession",
        'avec_titre': False,
        'couleur': discord.Color.from_rgb(52, 73, 94),
        'pied': "🤫 Confession anonyme • fais la tienne avec le bouton ci-dessous",
        'bouton': "Faire une confession anonyme",
        'emoji': "🤫",
        'panneau_titre': "🤫 Confessions anonymes",
        'panneau_texte': "Un secret, un aveu, quelque chose que tu n'as jamais osé dire ? Dis-le ici **anonymement** !",
        'titre_log': "🤫 Confession anonyme",
        'accorde': "e",  # « publiée » / « refusée »
        'il': "elle",
        'cle_salon': 'salons_confessions',
        'cle_logs': 'salons_logs_confessions',
        'commande_salon': 'salon_confessions',
        'commande_logs': 'salon_logs_confessions',
    },
}

COULEUR_ATTENTE = discord.Color.orange()
COULEUR_ACCEPTE = discord.Color.green()
COULEUR_REFUSE = discord.Color.red()


def salon_configure(guild, cle):
    salon_id = DONNEES[cle].get(str(guild.id))
    return guild.get_channel(salon_id) if salon_id else None


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
        salon_public = salon_configure(interaction.guild, config['cle_salon'])
        salon_modos = salon_configure(interaction.guild, config['cle_logs'])
        if salon_public is None or salon_modos is None:
            await interaction.response.send_message(
                f"❌ Les {config['nom']}s ne sont pas encore configuré{config['accorde']}s. Demande à un admin "
                f"de faire /{config['commande_salon']} et /{config['commande_logs']}.",
                ephemeral=True,
            )
            return

        demande = {
            'type': self.type_message,
            'auteur_id': interaction.user.id,
            'titre': self.titre.value if self.titre else '',
            'texte': self.texte.value,
        }
        embed = embed_moderation(demande, interaction.user)
        try:
            message = await salon_modos.send(
                embed=embed, view=VueModeration(), allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                f"❌ Je n'arrive pas à écrire dans le salon de validation des {config['nom']}s (permissions ?).",
                ephemeral=True,
            )
            return

        DONNEES['en_attente'][str(message.id)] = demande
        sauvegarder_donnees()
        dernier_envoi[(self.type_message, interaction.user.id)] = time.monotonic()

        e, il = config['accorde'], config['il']
        await interaction.response.send_message(
            f"📨 {config['le']} a été envoyé{e} à l'équipe de modération. "
            f"Tu recevras un message privé quand {il} sera accepté{e} ou refusé{e}.\n"
            f"-# Une fois publié{e}, les autres membres ne verront pas ton nom. "
            "Seule l'équipe de modération peut le voir.",
            ephemeral=True,
        )


def embed_moderation(demande, auteur):
    """Message envoyé aux modos : contenu + auteur"""
    config = TYPES_ANONYMES[demande['type']]
    embed = discord.Embed(
        title=f"{config['titre_log']} • ⏳ En attente de validation",
        description=demande['texte'],
        color=COULEUR_ATTENTE,
        timestamp=discord.utils.utcnow(),
    )
    if demande['titre']:
        embed.add_field(name="Titre", value=demande['titre'], inline=False)
    embed.add_field(
        name="Auteur (visible seulement ici)",
        value=f"{auteur.mention} (`{auteur}` • ID `{auteur.id}`)",
        inline=False,
    )
    embed.set_thumbnail(url=auteur.display_avatar.url)
    return embed


async def prevenir_auteur(guild, demande, texte):
    """Envoie un message privé à l'auteur (ne marche pas s'il a fermé ses MP)"""
    membre = guild.get_member(demande['auteur_id'])
    if membre is None:
        try:
            membre = await guild.fetch_member(demande['auteur_id'])
        except discord.HTTPException:
            return False
    try:
        await membre.send(texte)
        return True
    except discord.HTTPException:
        return False


def peut_moderer(interaction):
    return interaction.user.guild_permissions.manage_messages


async def terminer(interaction, demande, statut, couleur, details=""):
    """Met à jour le message des modos : statut, qui a décidé, et on enlève les boutons"""
    embed = interaction.message.embeds[0]
    config = TYPES_ANONYMES[demande['type']]
    embed.title = f"{config['titre_log']} • {statut}"
    embed.color = couleur
    embed.add_field(name="Décision", value=f"{statut} par {interaction.user.mention}{details}", inline=False)
    await interaction.response.edit_message(embed=embed, view=None)


async def accepter(interaction, demande):
    config = TYPES_ANONYMES[demande['type']]
    guild = interaction.guild
    salon_public = salon_configure(guild, config['cle_salon'])

    titre = demande['titre']
    if demande['type'] == 'confession':
        # Numéro de la confession (#1, #2, ...) propre à chaque serveur
        numero = DONNEES['compteur_confessions'].get(str(guild.id), 0) + 1
        titre = f"Confession #{numero}"

    embed = discord.Embed(title=titre or None, description=demande['texte'], color=config['couleur'])
    embed.set_footer(text=config['pied'])

    message = None
    if salon_public is not None:
        try:
            message = await salon_public.send(embed=embed, view=VueEnvoyer(demande['type']))
        except discord.HTTPException:
            pass
    if message is None:
        DONNEES['en_attente'][str(interaction.message.id)] = demande  # on remet en attente
        await interaction.response.send_message(
            f"❌ Impossible de publier dans le salon des {config['nom']}s (salon supprimé ou permissions ?).",
            ephemeral=True,
        )
        return

    if demande['type'] == 'confession':
        DONNEES['compteur_confessions'][str(guild.id)] = numero
    sauvegarder_donnees()

    ok = await prevenir_auteur(
        guild, demande,
        f"✅ {config['le']} sur **{guild.name}** a été accepté{config['accorde']} et publié{config['accorde']} "
        f"anonymement : {message.jump_url}",
    )
    await terminer(
        interaction, demande, "✅ Accepté" + config['accorde'], COULEUR_ACCEPTE,
        f"\n[Voir le message publié]({message.jump_url})" + ("" if ok else "\n-# (MP de l'auteur fermés)"),
    )


async def refuser(interaction, demande, raison=None):
    config = TYPES_ANONYMES[demande['type']]
    sauvegarder_donnees()
    texte = f"❌ {config['le']} sur **{interaction.guild.name}** a été refusé{config['accorde']} par l'équipe de modération."
    if raison:
        texte += f"\n**Raison :** {raison}"
    ok = await prevenir_auteur(interaction.guild, demande, texte)
    details = (f"\n**Raison :** {raison}" if raison else "") + ("" if ok else "\n-# (MP de l'auteur fermés)")
    await terminer(interaction, demande, "❌ Refusé" + config['accorde'], COULEUR_REFUSE, details)


def prendre_demande(interaction):
    """Retire la demande de la file d'attente (évite que 2 modos la traitent en même temps)"""
    return DONNEES['en_attente'].pop(str(interaction.message.id), None)


async def deja_traitee(interaction):
    await interaction.response.send_message("⚠️ Cette demande a déjà été traitée.", ephemeral=True)


class FormulaireRaison(discord.ui.Modal, title="Refuser avec une raison"):
    raison = discord.ui.TextInput(
        label="Raison (envoyée à l'auteur en MP)", style=discord.TextStyle.paragraph, max_length=1000
    )

    async def on_submit(self, interaction):
        demande = prendre_demande(interaction)
        if demande is None:
            await deja_traitee(interaction)
            return
        await refuser(interaction, demande, self.raison.value)


class VueModeration(discord.ui.View):
    """Boutons sous chaque demande. Ils marchent même après un redémarrage du bot."""

    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction):
        if not peut_moderer(interaction):
            await interaction.response.send_message(
                "❌ Il faut la permission « Gérer les messages » pour faire ça.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Accepter", emoji="✅", style=discord.ButtonStyle.success, custom_id="anonyme:accepter")
    async def bouton_accepter(self, interaction, button):
        demande = prendre_demande(interaction)
        if demande is None:
            await deja_traitee(interaction)
            return
        await accepter(interaction, demande)

    @discord.ui.button(label="Refuser", emoji="❌", style=discord.ButtonStyle.danger, custom_id="anonyme:refuser")
    async def bouton_refuser(self, interaction, button):
        demande = prendre_demande(interaction)
        if demande is None:
            await deja_traitee(interaction)
            return
        await refuser(interaction, demande)

    @discord.ui.button(label="Refuser avec une raison", emoji="📝", style=discord.ButtonStyle.secondary,
                       custom_id="anonyme:refuser_raison")
    async def bouton_refuser_raison(self, interaction, button):
        if str(interaction.message.id) not in DONNEES['en_attente']:
            await deja_traitee(interaction)
            return
        await interaction.response.send_modal(FormulaireRaison())


class VueEnvoyer(discord.ui.View):
    """Bouton « Faire une confession » / « Envoyer un poème » : plus besoin de connaître la commande.
    Il marche même après un redémarrage du bot."""

    def __init__(self, type_message):
        super().__init__(timeout=None)
        config = TYPES_ANONYMES[type_message]
        bouton = discord.ui.Button(
            label=config['bouton'], emoji=config['emoji'], style=discord.ButtonStyle.primary,
            custom_id=f"anonyme:ouvrir:{type_message}",
        )

        async def clic(interaction):
            await ouvrir_formulaire(interaction, type_message)

        bouton.callback = clic
        self.add_item(bouton)


def embed_panneau(type_message):
    """Message d'explication épinglé dans le salon public"""
    config = TYPES_ANONYMES[type_message]
    e = config['accorde']
    embed = discord.Embed(
        title=config['panneau_titre'],
        description=(
            f"{config['panneau_texte']}\n\n"
            "**Comment faire ?**\n"
            f"1️⃣ Clique sur le bouton **{config['emoji']} {config['bouton']}** ci-dessous "
            f"(ou sous n'importe quel{'le' if e else ''} {config['nom']}, ou tape `/{type_message}`)\n"
            "2️⃣ Écris ton texte dans la fenêtre qui s'ouvre et valide\n"
            "3️⃣ L'équipe de modération le relit, puis il est publié ici **sans ton nom**\n\n"
            "🔒 Les autres membres ne sauront jamais que c'est toi. Seule l'équipe de modération "
            "peut voir l'auteur, pour éviter les abus.\n"
            "📩 Tu reçois un message privé quand c'est accepté ou refusé."
        ),
        color=config['couleur'],
    )
    return embed


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


async def configurer_salon_public(interaction, type_message, salon):
    """Enregistre le salon public et y poste (et épingle) le message d'explication avec le bouton"""
    config = TYPES_ANONYMES[type_message]
    DONNEES[config['cle_salon']][str(interaction.guild_id)] = salon.id
    sauvegarder_donnees()

    texte = f"✅ Les {config['nom']}s accepté{config['accorde']}s seront publié{config['accorde']}s dans {salon.mention}."
    try:
        panneau = await salon.send(embed=embed_panneau(type_message), view=VueEnvoyer(type_message))
        texte += " J'y ai posté le message d'explication avec le bouton."
        try:
            await panneau.pin()
        except discord.HTTPException:
            texte += "\n⚠️ Je n'ai pas pu l'épingler (il me manque la permission « Épingler des messages » dans ce salon)."
    except discord.HTTPException:
        texte += "\n⚠️ Je n'ai pas pu poster le message d'explication (vérifie mes permissions dans ce salon)."
    await interaction.response.send_message(texte, ephemeral=True)


# --- Poèmes ---

@bot.tree.command(name="poeme", description="Envoyer un poème anonymement")
@discord.app_commands.guild_only()
async def poeme(interaction):
    await ouvrir_formulaire(interaction, 'poeme')


@bot.tree.command(name="salon_poemes", description="Choisir le salon où sont publiés les poèmes anonymes")
@discord.app_commands.guild_only()
@discord.app_commands.default_permissions(manage_guild=True)
async def salon_poemes(interaction, salon: discord.TextChannel):
    await configurer_salon_public(interaction, 'poeme', salon)


@bot.tree.command(name="salon_logs_poemes", description="Choisir le salon privé où les modos valident les poèmes")
@discord.app_commands.guild_only()
@discord.app_commands.default_permissions(manage_guild=True)
async def salon_logs_poemes(interaction, salon: discord.TextChannel):
    await configurer_salon(interaction, 'salons_logs_poemes', salon,
                           "✅ Les poèmes (avec leur auteur) arriveront dans {salon} pour validation." + AVERTISSEMENT_LOGS)


# --- Confessions ---

@bot.tree.command(name="confession", description="Faire une confession anonyme")
@discord.app_commands.guild_only()
async def confession(interaction):
    await ouvrir_formulaire(interaction, 'confession')


@bot.tree.command(name="salon_confessions", description="Choisir le salon où sont publiées les confessions anonymes")
@discord.app_commands.guild_only()
@discord.app_commands.default_permissions(manage_guild=True)
async def salon_confessions(interaction, salon: discord.TextChannel):
    await configurer_salon_public(interaction, 'confession', salon)


@bot.tree.command(name="salon_logs_confessions", description="Choisir le salon privé où les modos valident les confessions")
@discord.app_commands.guild_only()
@discord.app_commands.default_permissions(manage_guild=True)
async def salon_logs_confessions(interaction, salon: discord.TextChannel):
    await configurer_salon(interaction, 'salons_logs_confessions', salon,
                           "✅ Les confessions (avec leur auteur) arriveront dans {salon} pour validation." + AVERTISSEMENT_LOGS)


# --- Photo de profil du bot ---

@bot.tree.command(name="photo_bot", description="Changer la photo de profil du bot")
@discord.app_commands.guild_only()
@discord.app_commands.default_permissions(administrator=True)
async def photo_bot(interaction, image: discord.Attachment):
    if not (image.content_type or '').startswith('image/'):
        await interaction.response.send_message("❌ Envoie une image (PNG, JPG ou GIF).", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        await bot.user.edit(avatar=await image.read())
    except discord.HTTPException as e:
        # Discord limite les changements de photo (environ 2 toutes les 10 minutes)
        await interaction.followup.send(f"❌ Discord a refusé le changement : {e.text or e}", ephemeral=True)
        return
    await interaction.followup.send("✅ Photo de profil changée ! (ça peut prendre quelques minutes à s'afficher)", ephemeral=True)


@bot.event
async def setup_hook():
    # Boutons Accepter / Refuser : on les réactive à chaque démarrage
    bot.add_view(VueModeration())
    for type_message in TYPES_ANONYMES:
        bot.add_view(VueEnvoyer(type_message))
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
