
import os
import random
import sqlite3
import discord
from discord.ext import commands, tasks

# Initialize Gateway Intents
intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.members = True
intents.message_content = True 

DB_FILE = "activity.db"
DEFAULT_PREFIX = "!"

# ==========================================
#          DATABASE INITIALIZATION
# ==========================================

def init_db():
    """Initializes the SQLite database tables if they do not exist."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Store user precision metrics (points = minutes active in VC)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_activity (
            guild_id TEXT,
            user_id TEXT,
            points INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    
    # Track server configuration structures
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vc_tier_config (
            guild_id TEXT PRIMARY KEY,
            ui_channel_id TEXT,
            ui_msg_id TEXT,
            role_tier1_id TEXT,
            role_tier2_id TEXT,
            role_protected_id TEXT,
            role_tier_x_id TEXT,
            role_vip_id TEXT
        )
    """)
    conn.commit()
    conn.close()

# ==========================================
#              BOT CORE SETUP
# ==========================================

# Bot prefix is now firmly hardcoded to "!" across all servers
bot = commands.Bot(command_prefix=DEFAULT_PREFIX, intents=intents, help_command=None)
voice_start_times = {}

@bot.event
async def on_ready():
    init_db()
    print(f"Logged in successfully as {bot.user.name}")
    print(f"Locked Prefix: {DEFAULT_PREFIX}")
    print("SQLite Engine initialized and ready.")
    
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="VC Activity"))
    if not fast_update_loop.is_running():
        fast_update_loop.start()

@bot.event
async def on_command_completion(ctx):
    try:
        await ctx.message.add_reaction("✔️")
    except discord.HTTPException:
        pass

# ==========================================
#        CORE VC HOURS TRACKING LOGIC
# ==========================================

def is_valid_vc_state(state):
    """Returns True if the member is unmuted and undeafened in a public VC."""
    if not state or not state.channel:
        return False
    # Verify user is completely active (not self-muted or server-muted/deafened)
    if state.self_mute or state.mute or state.self_deafen or state.deafen:
        return False
    return True

def add_vc_minutes(guild_id, user_id, minutes):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_activity (guild_id, user_id, points)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) 
        DO UPDATE SET points = points + EXCLUDED.points
    """, (str(guild_id), str(user_id), minutes))
    conn.commit()
    conn.close()

def get_user_hours(guild_id, user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT points FROM user_activity WHERE guild_id = ? AND user_id = ?", (str(guild_id), str(user_id)))
    row = cursor.fetchone()
    conn.close()
    if row:
        return round(row[0] / 60, 1)
    return 0.0

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot: return
    user_id = str(member.id)
    guild_id = str(member.guild.id)

    was_valid = is_valid_vc_state(before)
    is_valid = is_valid_vc_state(after)

    # User was earning points but left VC or muted/deafened
    if was_valid and not is_valid:
        if user_id in voice_start_times:
            duration = discord.utils.utcnow() - voice_start_times[user_id]
            minutes = int(duration.total_seconds() / 60)
            if minutes > 0:
                add_vc_minutes(guild_id, user_id, minutes)
            del voice_start_times[user_id]

    # User entered a valid earning state (became unmuted/undeafened in VC)
    elif not was_valid and is_valid:
        voice_start_times[user_id] = discord.utils.utcnow()

# ==========================================
#             FIXED MESSAGE ENGINE
# ==========================================

@bot.event
async def on_message(message):
    # Completely ignore bots and DMs
    if message.author.bot or not message.guild:
        return

    # Check if the message starts with the hardcoded prefix
    if message.content.startswith(DEFAULT_PREFIX):
        # Fire standard commands pipelines cleanly
        await bot.process_commands(message)
    else:
        # Text chat contribution points
        add_vc_minutes(message.guild.id, message.author.id, 1)

# ==========================================
#         UI GENERATION & COMMANDS
# ==========================================

def generate_tier_ui(guild):
    """Generates the main status overview interface dashboard."""
    embed = discord.Embed(
        title="🎙️ VC ACTIVITY ROLES",
        description="Earn premium tier roles automatically by hanging out active in **PUBLIC VC**.\n*Note: You must remain unmuted & undeafened to accumulate hours.*",
        color=discord.Color.purple()
    )
    
    embed.add_field(
        name="👑 VIP — 55 Hours",
        value="• Higher role display position\n• 45,000 Coins Daily Allocation\n• 2.15x Gamble Payout Multiplier\n• Authority to Join Locked Calls\n• Bypass & Type in Locked Chats",
        inline=False
    )
    embed.add_field(
        name="⚔️ Tier X — 35 Hours",
        value="• High role display location\n• 15,000 Coins Daily Allocation\n• 2.05x Gamble Payout Multiplier\n• Exclusive Access to Staff Giveaways\n• Ability to VC mute & deafen others",
        inline=False
    )
    embed.add_field(
        name="🛡️ Protected — 25 Hours",
        value="• Full moderation protection layer from Staff → Admin\n• Authority to use Soundboard in all VCs",
        inline=False
    )
    embed.add_field(
        name="🥈 Tier 2 — 5 Hours",
        value="• Picture & GIF permissions integration\n• 7,500 Coins Daily Allocation\n• Eligible to submit applications for Staff positions",
        inline=False
    )
    embed.add_field(
        name="🥇 Tier 1 — 1 Hour",
        value="• Access to specialized `,color` suite for custom names\n• 5,000 Coins Daily Allocation\n• Custom external Emoji Permissions",
        inline=False
    )
    
    embed.set_footer(text="Updates clear dynamically every 30 seconds • Use !stats to check your progression")
    return embed

@bot.command(name="help")
async def help_ui(ctx):
    embed = discord.Embed(
        title="Activity Systems",
        description="Public tracking utilities.",
        color=discord.Color.dark_grey()
    )
    embed.add_field(name="`!stats`", value="Display your personal voice chat hour progress tracking.", inline=False)
    embed.add_field(name="`!leaderboard` (or `!lb`)", value="Display top 10 most active members.", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="stats")
async def check_stats(ctx, member: discord.Member = None):
    """Public command allowing members to check their total valid tracked VC time."""
    target = member or ctx.author
    hours = get_user_hours(ctx.guild.id, target.id)
    
    embed = discord.Embed(
        title=f"📊 VC Telemetry — {target.display_name}",
        color=discord.Color.blue()
    )
    embed.add_field(name="Tracked Time", value=f"`{hours}` Hours", inline=True)
    
    # Determine milestone progression
    if hours < 1.0: next_tier = f"Tier 1 ({round(1.0 - hours, 1)}h left)"
    elif hours < 5.0: next_tier = f"Tier 2 ({round(5.0 - hours, 1)}h left)"
    elif hours < 25.0: next_tier = f"Protected ({round(25.0 - hours, 1)}h left)"
    elif hours < 35.0: next_tier = f"Tier X ({round(35.0 - hours, 1)}h left)"
    elif hours < 55.0: next_tier = f"VIP ({round(55.0 - hours, 1)}h left)"
    else: next_tier = "All Milestones Achieved 👑"
        
    embed.add_field(name="Next Rank Up", value=f"`{next_tier}`", inline=True)
    await ctx.send(embed=embed)

@bot.command(name="setup_vc_system")
@commands.has_permissions(administrator=True)
async def setup_vc_system(ctx, target_channel: discord.TextChannel = None):
    """Automated environment builder command."""
    channel = target_channel or ctx.channel
    await ctx.send("⚙️ Initializing VC Activity Role environments... Creating presets...")

    # Create default roles safely
    t1 = await ctx.guild.create_role(name="Tier 1", colour=discord.Colour.blue(), mentionable=False)
    t2 = await ctx.guild.create_role(name="Tier 2", colour=discord.Colour.green(), mentionable=False)
    prot = await ctx.guild.create_role(name="Protected", colour=discord.Colour.teal(), mentionable=False)
    tx = await ctx.guild.create_role(name="Tier X", colour=discord.Colour.orange(), mentionable=False)
    vip = await ctx.guild.create_role(name="VIP", colour=discord.Colour.gold(), mentionable=False)

    # Deploy Dashboard UI
    ui_embed = generate_tier_ui(ctx.guild)
    msg = await channel.send(embed=ui_embed)

    # Save unique configurations to SQL database
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO vc_tier_config (guild_id, ui_channel_id, ui_msg_id, role_tier1_id, role_tier2_id, role_protected_id, role_tier_x_id, role_vip_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            ui_channel_id = EXCLUDED.ui_channel_id,
            ui_msg_id = EXCLUDED.ui_msg_id,
            role_tier1_id = EXCLUDED.role_tier1_id,
            role_tier2_id = EXCLUDED.role_tier2_id,
            role_protected_id = EXCLUDED.role_protected_id,
            role_tier_x_id = EXCLUDED.role_tier_x_id,
            role_vip_id = EXCLUDED.role_vip_id
    """, (str(ctx.guild.id), str(channel.id), str(msg.id), str(t1.id), str(t2.id), str(prot.id), str(tx.id), str(vip.id)))
    conn.commit()
    conn.close()

    await ctx.send(f"✅ Setup Completed. Live UI designated to {channel.mention}. Generated Roles bound to database safely.")

@bot.command(name="link_custom_roles")
@commands.has_permissions(administrator=True)
async def link_custom_roles(ctx, r1: discord.Role, r2: discord.Role, r3: discord.Role, rx: discord.Role, rvip: discord.Role):
    """Allows administrators to manually bind custom pre-existing or renamed structural roles."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO vc_tier_config (guild_id, role_tier1_id, role_tier2_id, role_protected_id, role_tier_x_id, role_vip_id)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            role_tier1_id = EXCLUDED.role_tier1_id,
            role_tier2_id = EXCLUDED.role_tier2_id,
            role_protected_id = EXCLUDED.role_protected_id,
            role_tier_x_id = EXCLUDED.role_tier_x_id,
            role_vip_id = EXCLUDED.role_vip_id
    """, (str(ctx.guild.id), str(r1.id), str(r2.id), str(r3.id), str(rx.id), str(rvip.id)))
    conn.commit()
    conn.close()
    await ctx.send("✅ Custom Roles synchronized into database storage overrides successfully.")

@bot.command(name="leaderboard", aliases=["lb"])
async def show_leaderboard(ctx):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, points FROM user_activity WHERE guild_id = ? ORDER BY points DESC LIMIT 10", (str(ctx.guild.id),))
    rows = cursor.fetchall()
    conn.close()

    embed = discord.Embed(title=f"🏆 {ctx.guild.name.upper()} VC LEADERS", color=discord.Color.gold())
    if not rows:
        embed.description = "No voice data logged yet."
        await ctx.send(embed=embed)
        return

    lb_text = ""
    for idx, (uid, points) in enumerate(rows, 1):
        hours = round(points / 60, 1)
        lb_text += f"**#{idx:02d}** | <@{uid}> — `{hours}` hours\n"
    embed.description = lb_text
    await ctx.send(embed=embed)

# ==========================================
#         AUTOMATED REFRESH LOOP
# ==========================================

@tasks.loop(seconds=30)
async def fast_update_loop():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    for guild in bot.guilds:
        guild_id = str(guild.id)
        
        # Pull configuration setup data matching this server
        cursor.execute("SELECT * FROM vc_tier_config WHERE guild_id = ?", (guild_id,))
        config = cursor.fetchone()
        if not config: continue
        
        # Column Map indexes: 1:ui_chan, 2:ui_msg, 3:t1, 4:t2, 5:prot, 6:tx, 7:vip
        ui_channel_id, ui_msg_id = config[1], config[2]
        role_ids = {
            "t1": config[3],
            "t2": config[4],
            "prot": config[5],
            "tx": config[6],
            "vip": config[7]
        }
        
        # 1. Synchronize UI Dashboard Display
        if ui_channel_id and ui_msg_id:
            channel = guild.get_channel(int(ui_channel_id))
            if channel:
                try:
                    msg = await channel.fetch_message(int(ui_msg_id))
                    await msg.edit(embed=generate_tier_ui(guild))
                except (discord.NotFound, discord.HTTPException):
                    pass

        # 2. Automatically sync ranks and assign roles
        cursor.execute("SELECT user_id, points FROM user_activity WHERE guild_id = ?", (guild_id,))
        user_rows = cursor.fetchall()
        
        for user_id_str, points in user_rows:
            try:
                member = await guild.fetch_member(int(user_id_str))
                if not member: continue
                
                hours = points / 60
                
                # Setup milestones
                tier_status = {
                    "t1": hours >= 1.0,
                    "t2": hours >= 5.0,
                    "prot": hours >= 25.0,
                    "tx": hours >= 35.0,
                    "vip": hours >= 55.0
                }
                
                # Check status flags vs role assignments
                for key, should_have in tier_status.items():
                    r_id = role_ids.get(key)
                    if not r_id: continue
                    role = guild.get_role(int(r_id))
                    if not role: continue
                    
                    if should_have and role not in member.roles:
                        await member.add_roles(role)
                    elif not should_have and role in member.roles:
                        await member.remove_roles(role)
            except discord.HTTPException:
                pass 

    conn.close()

@fast_update_loop.before_loop
async def before_fast_update_loop():
    await bot.wait_until_ready()

TOKEN = os.getenv("DISCORD_TOKEN")

if __name__ == "__main__":
    if TOKEN: 
        bot.run(TOKEN)
    else: 
        print("CRITICAL ERROR: DISCORD_TOKEN environmental variable is missing!")
