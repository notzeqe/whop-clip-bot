import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
from datetime import datetime, timezone, date
from pymongo import MongoClient

# ── CONFIG ──────────────────────────────────────────────────────────────────
CLIPS_CHANNEL_NAME = "submit-clips"
ANNOUNCEMENTS_CHANNEL_NAME = "announcements"

LEVELS = [
    {"level": 1, "clips": 5,   "role": "🎬 Rookie Clipper"},
    {"level": 2, "clips": 15,  "role": "⚡ Rising Clipper"},
    {"level": 3, "clips": 35,  "role": "🔥 Skilled Clipper"},
    {"level": 4, "clips": 75,  "role": "💎 Elite Clipper"},
    {"level": 5, "clips": 150, "role": "👑 Clip God"},
]

LEADERBOARD_ROLES = {
    1: "🥇 #1 Clipper",
    2: "🥈 #2 Clipper",
    3: "🥉 #3 Clipper",
}

MILESTONES = [10, 25, 50, 100, 200, 500]

# ── MONGODB SETUP ─────────────────────────────────────────────────────────────
MONGO_URI = os.getenv("MONGODB_URI")
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["clipbot"]
collection = db["users"]

def load_data():
    return {u["_id"]: u for u in collection.find({})}

def get_user(uid: str):
    user = collection.find_one({"_id": uid})
    if not user:
        user = {
            "_id": uid,
            "clips": 0,
            "username": "",
            "level": 0,
            "submitted_links": [],
            "streak": 0,
            "last_clip_date": None,
        }
    return user

def save_user(user: dict):
    collection.replace_one({"_id": user["_id"]}, user, upsert=True)

# ── HELPERS ───────────────────────────────────────────────────────────────────
def get_level(clips: int):
    current = None
    for lvl in LEVELS:
        if clips >= lvl["clips"]:
            current = lvl
    return current

def get_next_level(clips: int):
    for lvl in LEVELS:
        if clips < lvl["clips"]:
            return lvl
    return None

# ── BOT SETUP ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ── EVENTS ────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        tree.copy_global_to(guild=discord.Object(id=1484552116705169410))
        synced = await tree.sync(guild=discord.Object(id=1484552116705169410))
        print(f"✅ Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"❌ Sync error: {e}")
    weekly_leaderboard.start()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.name != CLIPS_CHANNEL_NAME:
        await bot.process_commands(message)
        return

    has_clip = bool(message.attachments) or any(
        x in message.content for x in ["http://", "https://", "www."]
    )
    if not has_clip:
        await bot.process_commands(message)
        return

    uid = str(message.author.id)
    user = get_user(uid)
    user["username"] = str(message.author)

    # ── Duplicate link detection ──
    if not message.attachments:
        link = next((w for w in message.content.split() if w.startswith(("http://", "https://", "www."))), None)
        if link and link in user.get("submitted_links", []):
            await message.reply("⚠️ You've already submitted that clip! Please share a new one.", mention_author=True)
            return
        if link:
            user.setdefault("submitted_links", []).append(link)

    old_clips = user["clips"]
    user["clips"] += 1
    new_clips = user["clips"]

    # ── Streak tracking ──
    today = datetime.now(timezone.utc).date().isoformat()
    last_date = user.get("last_clip_date")
    if last_date is None:
        user["streak"] = 1
    else:
        last = date.fromisoformat(last_date)
        diff = (datetime.now(timezone.utc).date() - last).days
        if diff == 0:
            pass
        elif diff == 1:
            user["streak"] = user.get("streak", 0) + 1
        else:
            user["streak"] = 1
    user["last_clip_date"] = today

    old_level_info = get_level(old_clips)
    new_level_info = get_level(new_clips)
    old_level = old_level_info["level"] if old_level_info else 0
    new_level = new_level_info["level"] if new_level_info else 0

    save_user(user)

    # ── Auto react ──
    await message.add_reaction("🎬")

    ann_channel = discord.utils.get(message.guild.text_channels, name=ANNOUNCEMENTS_CHANNEL_NAME)

    # ── Milestone messages ──
    if new_clips in MILESTONES:
        if ann_channel:
            embed = discord.Embed(
                title="🏅 Milestone Reached!",
                description=f"🎉 {message.author.mention} just hit **{new_clips} clips!** What a legend! 🔥",
                color=discord.Color.green(),
                timestamp=datetime.utcnow()
            )
            embed.set_thumbnail(url=message.author.display_avatar.url)
            await ann_channel.send(embed=embed)

    # ── Level up ──
    if new_level > old_level:
        user["level"] = new_level
        save_user(user)

        guild = message.guild
        member = message.author

        new_role = discord.utils.get(guild.roles, name=new_level_info["role"])
        if new_role:
            for lvl in LEVELS:
                old_role = discord.utils.get(guild.roles, name=lvl["role"])
                if old_role and old_role in member.roles:
                    await member.remove_roles(old_role)
            await member.add_roles(new_role)

        if ann_channel:
            next_lvl = get_next_level(new_clips)
            next_text = f"Next level at **{next_lvl['clips']} clips**." if next_lvl else "You've reached the **max level!** 🏆"
            embed = discord.Embed(
                title="🎉 Level Up!",
                description=(
                    f"{member.mention} just leveled up to **{new_level_info['role']}**!\n\n"
                    f"📊 Total clips: **{new_clips}**\n"
                    f"⬆️ {next_text}"
                ),
                color=discord.Color.gold(),
                timestamp=datetime.utcnow()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await ann_channel.send(embed=embed)

        await update_leaderboard_roles(guild, load_data())

    await bot.process_commands(message)


async def update_leaderboard_roles(guild: discord.Guild, data: dict):
    sorted_users = sorted(data.values(), key=lambda x: x["clips"], reverse=True)

    for rank, role_name in LEADERBOARD_ROLES.items():
        role = discord.utils.get(guild.roles, name=role_name)
        if role:
            for member in role.members:
                await member.remove_roles(role)

    for i, udata in enumerate(sorted_users[:3], start=1):
        role_name = LEADERBOARD_ROLES.get(i)
        if not role_name:
            continue
        role = discord.utils.get(guild.roles, name=role_name)
        member = guild.get_member(int(udata["_id"]))
        if role and member:
            await member.add_roles(role)


# ── WEEKLY LEADERBOARD TASK ───────────────────────────────────────────────────
@tasks.loop(hours=24)
async def weekly_leaderboard():
    now = datetime.now(timezone.utc)
    if now.weekday() != 6:
        return

    data = load_data()
    if not data:
        return

    for guild in bot.guilds:
        ann_channel = discord.utils.get(guild.text_channels, name=ANNOUNCEMENTS_CHANNEL_NAME)
        if not ann_channel:
            continue

        sorted_users = sorted(data.values(), key=lambda x: x["clips"], reverse=True)
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        description = ""
        for i, udata in enumerate(sorted_users[:10], start=1):
            medal = medals.get(i, f"**#{i}**")
            level_info = get_level(udata["clips"])
            level_str = f" • {level_info['role']}" if level_info else ""
            streak = udata.get("streak", 0)
            streak_str = f" 🔥 {streak}d" if streak > 1 else ""
            description += f"{medal} <@{udata['_id']}> — **{udata['clips']} clips**{level_str}{streak_str}\n"

        embed = discord.Embed(
            title="📅 Weekly Leaderboard",
            description=description or "No data yet.",
            color=discord.Color.gold(),
            timestamp=datetime.utcnow()
        )
        await ann_channel.send(embed=embed)


# ── SLASH COMMANDS ────────────────────────────────────────────────────────────

@tree.command(name="leaderboard", description="Show the top clippers leaderboard")
async def leaderboard(interaction: discord.Interaction):
    data = load_data()
    if not data:
        await interaction.response.send_message("No clips recorded yet!", ephemeral=True)
        return

    sorted_users = sorted(data.values(), key=lambda x: x["clips"], reverse=True)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    description = ""
    for i, udata in enumerate(sorted_users[:10], start=1):
        medal = medals.get(i, f"**#{i}**")
        level_info = get_level(udata["clips"])
        level_str = f" • {level_info['role']}" if level_info else ""
        streak = udata.get("streak", 0)
        streak_str = f" 🔥 {streak}d" if streak > 1 else ""
        description += f"{medal} <@{udata['_id']}> — **{udata['clips']} clips**{level_str}{streak_str}\n"

    embed = discord.Embed(
        title="🏆 Clipping Leaderboard",
        color=discord.Color.gold(),
        timestamp=datetime.utcnow()
    )
    embed.description = description or "No data yet."
    await interaction.response.send_message(embed=embed)


@tree.command(name="mystats", description="Check your clipping stats")
async def mystats(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    user = collection.find_one({"_id": uid})

    if not user:
        await interaction.response.send_message("You haven't submitted any clips yet!", ephemeral=True)
        return

    clips = user["clips"]
    level_info = get_level(clips)
    next_level_info = get_next_level(clips)
    streak = user.get("streak", 0)

    embed = discord.Embed(
        title=f"📊 {interaction.user.display_name}'s Stats",
        color=discord.Color.blurple(),
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    embed.add_field(name="🎬 Total Clips", value=str(clips), inline=True)
    embed.add_field(name="🔥 Streak", value=f"{streak} day(s)", inline=True)

    if level_info:
        embed.add_field(name="⚡ Current Level", value=level_info["role"], inline=True)
    else:
        embed.add_field(name="⚡ Level", value="Unranked (submit 5 clips!)", inline=True)

    if next_level_info:
        needed = next_level_info["clips"] - clips
        embed.add_field(
            name="🎯 Next Level",
            value=f"{next_level_info['role']}\n**{needed} more clips** needed",
            inline=False
        )
    else:
        embed.add_field(name="🏆 Status", value="Max level reached!", inline=False)

    all_users = sorted(collection.find({}), key=lambda x: x["clips"], reverse=True)
    rank = next((i+1 for i, u in enumerate(all_users) if u["_id"] == uid), None)
    if rank:
        embed.add_field(name="🏅 Server Rank", value=f"#{rank}", inline=True)

    await interaction.response.send_message(embed=embed)


@tree.command(name="addclips", description="[Admin] Manually add clips to a user")
@app_commands.checks.has_permissions(administrator=True)
async def addclips(interaction: discord.Interaction, member: discord.Member, amount: int):
    uid = str(member.id)
    user = get_user(uid)
    user["username"] = str(member)
    old_clips = user["clips"]
    user["clips"] = max(0, old_clips + amount)
    new_clips = user["clips"]

    old_level_info = get_level(old_clips)
    new_level_info = get_level(new_clips)
    old_level = old_level_info["level"] if old_level_info else 0
    new_level = new_level_info["level"] if new_level_info else 0

    save_user(user)

    if new_level > old_level and new_level_info:
        new_role = discord.utils.get(interaction.guild.roles, name=new_level_info["role"])
        if new_role:
            for lvl in LEVELS:
                old_role = discord.utils.get(interaction.guild.roles, name=lvl["role"])
                if old_role and old_role in member.roles:
                    await member.remove_roles(old_role)
            await member.add_roles(new_role)

    await update_leaderboard_roles(interaction.guild, load_data())
    await interaction.response.send_message(
        f"✅ Added **{amount} clips** to {member.mention}. They now have **{new_clips} clips**.",
        ephemeral=True
    )


@tree.command(name="removeclips", description="[Admin] Remove clips from a user")
@app_commands.checks.has_permissions(administrator=True)
async def removeclips(interaction: discord.Interaction, member: discord.Member, amount: int):
    uid = str(member.id)
    user = collection.find_one({"_id": uid})

    if not user:
        await interaction.response.send_message(f"{member.mention} has no clip data.", ephemeral=True)
        return

    user["clips"] = max(0, user["clips"] - amount)
    new_clips = user["clips"]
    save_user(user)

    await update_leaderboard_roles(interaction.guild, load_data())
    await interaction.response.send_message(
        f"✅ Removed **{amount} clips** from {member.mention}. They now have **{new_clips} clips**.",
        ephemeral=True
    )


@tree.command(name="setclips", description="[Admin] Set a user's clip count to an exact number")
@app_commands.checks.has_permissions(administrator=True)
async def setclips(interaction: discord.Interaction, member: discord.Member, amount: int):
    uid = str(member.id)
    user = get_user(uid)
    user["username"] = str(member)
    user["clips"] = max(0, amount)
    save_user(user)

    new_level_info = get_level(amount)
    if new_level_info:
        new_role = discord.utils.get(interaction.guild.roles, name=new_level_info["role"])
        if new_role:
            for lvl in LEVELS:
                old_role = discord.utils.get(interaction.guild.roles, name=lvl["role"])
                if old_role and old_role in member.roles:
                    await member.remove_roles(old_role)
            await member.add_roles(new_role)

    await update_leaderboard_roles(interaction.guild, load_data())
    await interaction.response.send_message(
        f"✅ Set {member.mention}'s clips to **{amount}**.",
        ephemeral=True
    )


@tree.command(name="resetuser", description="[Admin] Reset a user's clip count")
@app_commands.checks.has_permissions(administrator=True)
async def resetuser(interaction: discord.Interaction, member: discord.Member):
    uid = str(member.id)
    user = collection.find_one({"_id": uid})

    if user:
        for lvl in LEVELS:
            role = discord.utils.get(interaction.guild.roles, name=lvl["role"])
            if role and role in member.roles:
                await member.remove_roles(role)
        collection.delete_one({"_id": uid})
        await update_leaderboard_roles(interaction.guild, load_data())
        await interaction.response.send_message(f"✅ Reset {member.mention}'s clip data.", ephemeral=True)
    else:
        await interaction.response.send_message(f"{member.mention} has no data to reset.", ephemeral=True)


@tree.command(name="setuproles", description="[Admin] Create all level & leaderboard roles in the server")
@app_commands.checks.has_permissions(administrator=True)
async def setuproles(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    created = []

    all_roles = [lvl["role"] for lvl in LEVELS] + list(LEADERBOARD_ROLES.values())
    for role_name in all_roles:
        existing = discord.utils.get(guild.roles, name=role_name)
        if not existing:
            await guild.create_role(name=role_name, reason="Clip bot setup")
            created.append(role_name)

    if created:
        await interaction.followup.send(f"✅ Created roles: {', '.join(created)}", ephemeral=True)
    else:
        await interaction.followup.send("✅ All roles already exist!", ephemeral=True)


# ── RUN ───────────────────────────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ERROR: Set your DISCORD_TOKEN environment variable.")
elif not MONGO_URI:
    print("❌ ERROR: Set your MONGODB_URI environment variable.")
else:
    bot.run(TOKEN)
