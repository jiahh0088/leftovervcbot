import os
import random
import json
import asyncio
import math
from datetime import datetime, timedelta, timezone
import discord
from discord.ext import commands, tasks
import aiosqlite  

# ==========================================
#         RAILWAY ENVIRONMENT CONFIG
# ==========================================

# Force the database into Railway's persistent volume path if mounted, fallback to local
DATA_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", ".")
DB_FILE = os.path.join(DATA_DIR, "activity.db")
CONFIG_FILE = os.path.join(DATA_DIR, "portal_config.json")

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.members = True
intents.message_content = True  

# ==========================================
#          DATABASE ARTIFACT ENGINE
# ==========================================

async def init_db():
    """Initializes a SINGLE persistent, non-blocking connection pool for the entire runtime."""
    bot.db = await aiosqlite.connect(DB_FILE)
    
    # Critical performance tweaks for multi-threaded cloud hosting
    await bot.db.execute("PRAGMA journal_mode=WAL;")  # Write-Ahead Logging allows simultaneous reads/writes
    await bot.db.execute("PRAGMA synchronous=NORMAL;") # Drastically cuts back on IO bottlenecks
    
    await bot.db.execute("""
        CREATE TABLE IF NOT EXISTS user_activity (
            guild_id TEXT,
            user_id TEXT,
            points INTEGER DEFAULT 0,
            bank INTEGER DEFAULT 0,
            daily_norm INTEGER DEFAULT 10,
            daily_progress INTEGER DEFAULT 0,
            checked_in INTEGER DEFAULT 0,
            last_fg_switch TEXT DEFAULT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    await bot.db.execute("""
        CREATE TABLE IF NOT EXISTS group_activity (
            guild_id TEXT,
            role_id TEXT,
            points INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, role_id)
        )
    """)
    await bot.db.execute("""
        CREATE TABLE IF NOT EXISTS confessions (
            guild_id TEXT,
            confession_id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id TEXT,
            content TEXT,
            timestamp TEXT
        )
    """)
    await bot.db.commit()

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            try: 
                return json.load(f)
            except json.JSONDecodeError: 
                return {}
    return {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

def get_prefix(bot_instance, message):
    if not message.guild: 
        return "!"
    config = load_json(CONFIG_FILE)
    return config.get(f"{message.guild.id}_prefix", "!")

# ==========================================
#              BOT CORE CONFIG
# ==========================================

bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)
voice_start_times = {}
last_voice_activity = {}

# Placeholder loops definition to prevent startup crashing if loops aren't defined elsewhere
@tasks.loop(seconds=60)
async def fast_update_loop():
    pass

@tasks.loop(hours=24)
async def daily_reset_loop():
    pass

@bot.event
async def on_ready():
    await init_db()
    print(f"Logged in safely as {bot.user.name}")
    print("Consolidated Systems Matrix Online. Interfaces Operational.")
    
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="/leftover"))
    
    if not fast_update_loop.is_running():
        fast_update_loop.start()
    if not daily_reset_loop.is_running():
        daily_reset_loop.start()

@bot.event
async def close():
    """Gracefully closes connection vectors during Railway redeployments."""
    if hasattr(bot, 'db'):
        await bot.db.close()
        print("Database framework connections closed down cleanly.")
    await super().close()

@bot.event
async def on_command_completion(ctx):
    try: 
        await ctx.message.add_reaction("✔️")
    except discord.HTTPException: 
        pass

# ==========================================
#          DATABASE OPERATION HOOKS
# ==========================================

async def get_user_data(guild_id, user_id):
    async with bot.db.execute(
        "SELECT points, bank, daily_norm, daily_progress, checked_in, last_fg_switch FROM user_activity WHERE guild_id = ? AND user_id = ?", 
        (str(guild_id), str(user_id))
    ) as cursor:
        row = await cursor.fetchone()
        
    if not row:
        await bot.db.execute("INSERT INTO user_activity (guild_id, user_id) VALUES (?, ?)", (str(guild_id), str(user_id)))
        await bot.db.commit()
        row = (0, 0, 10, 0, 0, None)
        
    return {
        "points": row[0], "bank": row[1], "daily_norm": row[2], 
        "daily_progress": row[3], "checked_in": row[4], "last_fg_switch": row[5]
    }

async def add_points(guild, member, amount):
    guild_id = str(guild.id)
    user_id = str(member.id)
    
    data = await get_user_data(guild_id, user_id)
    new_pts = data["points"] + amount
    new_prog = data["daily_progress"] + amount
    
    await bot.db.execute("""
        UPDATE user_activity 
        SET points = ?, daily_progress = ?
        WHERE guild_id = ? AND user_id = ?
    """, (new_pts, new_prog, guild_id, user_id))
    
    if not data["checked_in"] and new_prog >= math.ceil(data["daily_norm"] * 0.25):
        bonus = math.ceil(new_pts * 0.05)
        new_pts += bonus
        await bot.db.execute("""
            UPDATE user_activity 
            SET points = ?, checked_in = 1
            WHERE guild_id = ? AND user_id = ?
        """, (new_pts, guild_id, user_id))
        
    config = load_json(CONFIG_FILE)
    roles_list = config.get(f"{guild_id}_fg_roles_list", [])
    for rid in roles_list:
        role = guild.get_role(int(rid))
        if role and role in member.roles:
            await bot.db.execute("""
                INSERT INTO group_activity (guild_id, role_id, points)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, role_id) 
                DO UPDATE SET points = group_activity.points + EXCLUDED.points
            """, (guild_id, str(rid), amount))
            
    await bot.db.commit()

async def modify_bank(guild_id, user_id, amount):
    data = await get_user_data(guild_id, user_id)
    await bot.db.execute("UPDATE user_activity SET bank = ? WHERE guild_id = ? AND user_id = ?", (data["bank"] + amount, str(guild_id), str(user_id)))
    await bot.db.commit()

async def update_switch_timestamp(guild_id, user_id):
    now_str = datetime.now(timezone.utc).isoformat()  
    await bot.db.execute("UPDATE user_activity SET last_fg_switch = ? WHERE guild_id = ? AND user_id = ?", (now_str, str(guild_id), str(user_id)))
    await bot.db.commit()

async def get_top_users(guild_id, limit=10):
    async with bot.db.execute("SELECT user_id, points FROM user_activity WHERE guild_id = ? ORDER BY points DESC LIMIT ?", (str(guild_id), limit)) as cursor:
        return await cursor.fetchall()

async def get_top_groups(guild_id, limit=15):
    async with bot.db.execute("SELECT role_id, points FROM group_activity WHERE guild_id = ? ORDER BY points DESC LIMIT ?", (str(guild_id), limit)) as cursor:
        return await cursor.fetchall()

# ==========================================
#      DYNAMIC ROLE REPLACEMENT ROUTINE
# ==========================================

async def replace_fg_role(member: discord.Member, new_role_id: int, roles_list: list) -> bool:
    to_remove = [member.guild.get_role(int(rid)) for rid in roles_list if int(rid) != new_role_id and member.guild.get_role(int(rid)) in member.roles]
    target_role = member.guild.get_role(new_role_id)
    
    if not target_role: 
        return False
    try:
        if to_remove:
            await member.remove_roles(*[r for r in to_remove if r])
        await member.add_roles(target_role)
        return True
    except discord.Forbidden:
        return False

# ==========================================
#          HELP UI PAGINATION DECK
# ==========================================

class HelpView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("This menu interface does not belong to your session.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Public Core", style=discord.ButtonStyle.grey)
    async def cat_core(self, interaction: discord.Interaction, button: discord.ui.Button):
        emb = discord.Embed(title="Help // Public Core Utilities", color=discord.Color.blue())
        emb.add_field(name="!leaderboard (or !lb)", value="Display individual activity rankings.", inline=False)
        emb.add_field(name="!fgleaderboard (or !fglb)", value="Display group points rankings standings.", inline=False)
        emb.add_field(name="!dep / !withdraw / !give", value="Manage point vaults and peer asset transfers.", inline=False)
        await interaction.response.edit_message(embed=emb, view=self)

    @discord.ui.button(label="Economy & Shop", style=discord.ButtonStyle.green)
    async def cat_econ(self, interaction: discord.Interaction, button: discord.ui.Button):
        emb = discord.Embed(title="Help // Economy & Privilege Marketplace", color=discord.Color.green())
        emb.add_field(name="!shop", value="Render the purchase matrix setup display.", inline=False)
        emb.add_field(name="!buy mute @user", value="Timeout target handles for exactly 5 minutes.\nCost: `50,000` base tokens or 10% of total wealth parameters if superior.", inline=False)
        await interaction.response.edit_message(embed=emb, view=self)  # Fixed reference from embed -> emb

    @discord.ui.button(label="Casino Games", style=discord.ButtonStyle.blurple)
    async def cat_casino(self, interaction: discord.Interaction, button: discord.ui.Button):
        emb = discord.Embed(title="Help // High-Stakes Casino Modules", color=discord.Color.gold())
        emb.add_field(name="!mines <bet>", value="Launch a hazard sweep grid.", inline=False)
        emb.add_field(name="!blackjack <bet>", value="Open an automated card table canvas.", inline=False)
        emb.add_field(name="!crash <bet>", value="Launch structural multiplier vector acceleration graphs.", inline=False)
        await interaction.response.edit_message(embed=emb, view=self)

    @discord.ui.button(label="Friend Groups", style=discord.ButtonStyle.red)
    async def cat_fgs(self, interaction: discord.Interaction, button: discord.ui.Button):
        emb = discord.Embed(title="Help // Friend Group Navigation & Combat", color=discord.Color.red())
        emb.add_field(name="!fg list", value="Open the interactive scrolling directory.", inline=False)
        emb.add_field(name="!fg inv @user", value="Issue an interactive invitation handshake contract.", inline=False)
        emb.add_field(name="!fgarena @role <bet>", value="Launch cross-clan combat arrays.", inline=False)
        await interaction.response.edit_message(embed=emb, view=self)

@bot.command(name="help")
async def process_help_system(ctx):
    view = HelpView(ctx)
    emb = discord.Embed(title="System Interface Portal", description="Select an action node from the dashboard elements layout below to review operational parameters.", color=discord.Color.dark_grey())
    await ctx.send(embed=emb, view=view)

# ==========================================
#          ADMINISTRATIVE OVERRIDES
# ==========================================

@bot.command(name="setprefix")
@commands.has_permissions(administrator=True)
async def set_prefix(ctx, new_prefix: str = None):
    if not new_prefix: 
        return await ctx.send("Provide valid path strings.")
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_prefix"] = new_prefix
    save_json(CONFIG_FILE, config)
    await ctx.send(f"Command matrix routing updated. Path: `{new_prefix}`")

@bot.command(name="setportal")
@commands.has_permissions(administrator=True)
async def set_portal(ctx, channel: discord.VoiceChannel = None):
    if not channel: 
        return
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_portal"] = channel.id
    save_json(CONFIG_FILE, config)
    await ctx.send(f"Redirect matrix targeted: {channel.name}")

@bot.command(name="setleaderboard")
@commands.has_permissions(administrator=True)
async def set_leaderboard_channel(ctx, channel: discord.TextChannel = None):
    if not channel: 
        return
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_lb_channel"] = channel.id
    config.pop(f"{ctx.guild.id}_lb_msg_id", None)
    save_json(CONFIG_FILE, config)
    await ctx.send(f"Telemetry pipelines verified to target: {channel.name}")

@bot.command(name="setfgchannel")
@commands.has_permissions(administrator=True)
async def set_fg_channel(ctx, channel: discord.TextChannel = None):
    if not channel: 
        return
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_fg_lb_channel"] = channel.id
    config.pop(f"{ctx.guild.id}_fg_msg_id", None)
    save_json(CONFIG_FILE, config)
    await ctx.send(f"Friend Group updates targeted to: {channel.name}")

@bot.command(name="setroles")
@commands.has_permissions(administrator=True)
async def set_roles(ctx, r1: discord.Role, r2: discord.Role, r3: discord.Role):
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_role1"] = r1.id
    config[f"{ctx.guild.id}_role2"] = r2.id
    config[f"{ctx.guild.id}_role3"] = r3.id
    save_json(CONFIG_FILE, config)
    await ctx.send("Tier distribution configurations synchronized successfully.")

@bot.command(name="setfgroles")
@commands.has_permissions(administrator=True)
async def set_fg_roles(ctx, *roles: discord.Role):
    if not roles: 
        return await ctx.send("Provide valid role configurations.")
    config = load_json(CONFIG_FILE)
    cache_key = f"{ctx.guild.id}_fg_roles_list"
    if cache_key not in config or not isinstance(config[cache_key], list):
        config[cache_key] = []
        
    for r in roles:
        if r.id not in config[cache_key]:
            config[cache_key].append(r.id)
            
    for idx, r in enumerate(roles[:3], 1):
        config[f"{ctx.guild.id}_fg_role{idx}"] = r.id
        
    save_json(CONFIG_FILE, config)
    await ctx.send(f"Registered Friend Group roles tracking lines. Size: {len(config[cache_key])}")

@bot.command(name="setvipcategory")
@commands.has_permissions(administrator=True)
async def set_vip_category(ctx, category: discord.CategoryChannel = None):
    if not category: 
        return await ctx.send("Please specify a valid parent category ID channel.")
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_vip_category_id"] = category.id
    save_json(CONFIG_FILE, config)
    await ctx.send(f"VIP lounge creation path locked beneath category: `{category.name}`")

@bot.command(name="setwelcome")
@commands.has_permissions(administrator=True)
async def set_welcome_role(ctx, role: discord.Role = None):
    config = load_json(CONFIG_FILE)
    if not role:
        config.pop(f"{ctx.guild.id}_welcome_role", None)
        await ctx.send("Welcome role bindings removed.")
    else:
        config[f"{ctx.guild.id}_welcome_role"] = role.id
        await ctx.send(f"Welcome operations tracking role points modifier: {role.name}")
    save_json(CONFIG_FILE, config)

# ==========================================
#          ECONOMIC VAULT SYSTEM
# ==========================================

@bot.command(name="deposit", aliases=["dep"])
async def economic_deposit(ctx, amount: str):
    data = await get_user_data(ctx.guild.id, ctx.author.id)
    if amount.lower() == "all": 
        val = data["points"]
    else:
        try: 
            val = int(amount)
        except ValueError: 
            return await ctx.send("Specify whole integers.")
        
    if val <= 0 or data["points"] < val: 
        return await ctx.send("Allocation parameters exceed constraints.")
    await add_points(ctx.guild, ctx.author, -val)
    await modify_bank(ctx.guild.id, ctx.author.id, val)
    await ctx.send(f"Ledger updated. Deposited `{val}` tokens to bank reserves.")

@bot.command(name="withdraw")
async def economic_withdraw(ctx, amount: str):
    data = await get_user_data(ctx.guild.id, ctx.author.id)
    if amount.lower() == "all": 
        val = data["bank"]
    else:
        try: 
            val = int(amount)
        except ValueError: 
            return await ctx.send("Specify whole integers.")
        
    if val <= 0 or data["bank"] < val: 
        return await ctx.send("Vault reserves shortfall.")
    await modify_bank(ctx.guild.id, ctx.author.id, -val)
    await add_points(ctx.guild, ctx.author, val)
    await ctx.send(f"Vault reserves updated. Moved `{val}` points to active balance handles.")

@bot.command(name="give")
async def economic_peer_give(ctx, target: discord.Member, amount: int):
    if amount <= 0: 
        return await ctx.send("Invalid metric entry amount.")
    source_data = await get_user_data(ctx.guild.id, ctx.author.id)
    if source_data["points"] < amount: 
        return await ctx.send("Insufficient active balance.")
    
    await add_points(ctx.guild, ctx.author, -amount)
    await add_points(ctx.guild, target, amount)
    await ctx.send(f"Dispatched `{amount}` points safely over to {target.mention}.")

# ==========================================
#          MARKETPLACE & PRIVILEGES
# ==========================================

@bot.command(name="shop")
async def display_shop_layout(ctx):
    emb = discord.Embed(title="Privilege Marketplace", color=discord.Color.green())
    emb.add_field(name="!buy mute @user", value="Timeout target handles for exactly 5 minutes.\nCost: `50,000` base tokens or 10% of total wealth parameters if superior.", inline=False)
    emb.add_field(name="!buy nickname @user <name>", value="Modify target naming tags identities for 1 hour.\n- Standard Profiles: `10,000` tokens\n- Moderators: `25,000` tokens\n- Admins: `35,000` tokens or 10% overall vault assets scale evaluations.", inline=False)
    await ctx.send(embed=emb)

@bot.command(name="buy")
async def purchase_processor(ctx, item_type: str, target: discord.Member = None, *, aux_args: str = None):
    if not target: 
        return await ctx.send("Specify a recipient handle.")
    user_data = await get_user_data(ctx.guild.id, ctx.author.id)
    total_wealth = user_data["points"] + user_data["bank"]
    
    if item_type.lower() == "mute":
        cost = max(50000, math.ceil(total_wealth * 0.10))
        if user_data["points"] < cost: 
            return await ctx.send(f"Liquid assets insufficient. Cost evaluated to: `{cost}`")
        try:
            await target.timeout(timedelta(minutes=5), reason="Marketplace Privilege Purchase Execution.")
            await add_points(ctx.guild, ctx.author, -cost)
            await ctx.send(f"Enforced lockout constraints onto {target.mention}. Charge deducted: `{cost}` points.")
        except discord.Forbidden: 
            await ctx.send("Permissions hierarchy levels block command execution vectors.")
         
    elif item_type.lower() in ["nickname", "nick"]:
        if not aux_args: 
            return await ctx.send("Provide the new name string argument parameters.")
        if target.guild_permissions.administrator: 
            cost = max(35000, math.ceil(total_wealth * 0.10))
        elif target.guild_permissions.manage_messages: 
            cost = 25000
        else: 
            cost = 10000
            
        if user_data["points"] < cost: 
            return await ctx.send(f"Asset parameters shortfall. Requirement: `{cost}` points.")
        try:
            await target.edit(nick=aux_args[:32])
            await add_points(ctx.guild, ctx.author, -cost)
            await ctx.send(f"Identity changes applied to {target.mention}. Balance deducted: `{cost}` tokens.")
        except discord.Forbidden: 
            await ctx.send("Security configurations prohibit renaming this target profile.")

# ==========================================
#          ANONYMOUS CONFESSIONS MODULE
# ==========================================

class ConfessionSubmissionModal(discord.ui.Modal, title="Secure Cryptographic Confession Portal"):
    content_input = discord.ui.TextInput(label="Confession Payload Data", style=discord.TextStyle.long, required=True, max_length=1500)
    
    def __init__(self, logs_channel_id):
        super().__init__()
        self.logs_channel_id = logs_channel_id

    async def on_submit(self, interaction: discord.Interaction):
        config = load_json(CONFIG_FILE)
        public_channel_id = config.get(f"{interaction.guild.id}_confessions_public")
        public_channel = interaction.guild.get_channel(public_channel_id) if public_channel_id else None
        logs_channel = interaction.guild.get_channel(self.logs_channel_id) if self.logs_channel_id else None
        
        async with bot.db.execute(
            "INSERT INTO confessions (guild_id, author_id, content, timestamp) VALUES (?, ?, ?, ?)",
            (str(interaction.guild.id), str(interaction.user.id), self.content_input.value, datetime.now(timezone.utc).isoformat())
        ) as cursor:
            generated_id = cursor.lastrowid
        await bot.db.commit()
        
        if public_channel:
            pub_emb = discord.Embed(title=f"Anonymous Confession #{generated_id}", description=self.content_input.value, color=discord.Color.purple())
            await public_channel.send(embed=pub_emb)
        if logs_channel:
            log_emb = discord.Embed(title=f"Telemetry Audit // Confession #{generated_id}", description=self.content_input.value, color=discord.Color.red())
            log_emb.add_field(name="Author Handle Mapping Link", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
            await logs_channel.send(embed=log_emb)
            
        await interaction.response.send_message("Data safely funneled anonymized to output queues.", ephemeral=True)

class ConfessionTriggerView(discord.ui.View):
    def __init__(self, logs_channel_id):
        super().__init__(timeout=None)
        self.logs_channel_id = logs_channel_id

    @discord.ui.button(label="Initialize Anonymous Dispatch", style=discord.ButtonStyle.blurple, custom_id="trigger_confession_modal")
    async def launch_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ConfessionSubmissionModal(self.logs_channel_id))

@bot.command(name="setconfessions")
@commands.has_permissions(administrator=True)
async def setup_confession_system(ctx, public_feed: discord.TextChannel, logging_feed: discord.TextChannel):
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_confessions_public"] = public_feed.id
    config[f"{ctx.guild.id}_confessions_logs"] = logging_feed.id
    save_json(CONFIG_FILE, config)
    
    view = ConfessionTriggerView(logging_feed.id)
    emb = discord.Embed(title="Confessions Operations Core", description="Interact with the action node below to route secure content channels securely.", color=discord.Color.purple())
    await ctx.send(embed=emb, view=view)

# ==========================================
#          HIGH-STAKES CASINO ENGINE
# ==========================================

class MinesGameView(discord.ui.View):
    def __init__(self, ctx, bet, bombs_count):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.bet = bet
        self.bombs_count = bombs_count
        self.grid = ["safe"] * 25
        self.revealed = [False] * 25
        self.clicks = 0
        deployed = 0
        while deployed < bombs_count:
            idx = random.randint(0, 24)
            if self.grid[idx] == "safe":
                self.grid[idx] = "bomb"
                deployed += 1
        for i in range(25): 
            self.add_item(MinesButton(i, row=i // 5))

    def calculate_multiplier(self):
        k = self.clicks
        if k == 0: 
            return 1.0
        num = math.comb(25 - self.bombs_count, k)
        den = math.comb(25, k)
        if den == 0 or num == 0: 
            return 20.0
        return round((1.0 / (num / den)) * 0.95, 2)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.author.id

class MinesButton(discord.ui.Button):
    def __init__(self, index, row):
        super().__init__(label="?", style=discord.ButtonStyle.grey, row=row)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view: MinesGameView = self.view
        if view.revealed[self.index]: 
            return
        view.revealed[self.index] = True
        
        if view.grid[self.index] == "bomb":
            self.style = discord.ButtonStyle.red
            self.label = "💥"
            self.disabled = True
            for item in view.children: 
                item.disabled = True
            view.stop()
            await add_points(interaction.guild, view.ctx.author, -view.bet)
            await interaction.response.edit_message(content=f"💥 **BOOM!** Detonated a hazard mine. Balance lost: -`{view.bet}` points.", view=view)
        else:
            self.style = discord.ButtonStyle.green
            self.label = "💎"
            self.disabled = True
            view.clicks += 1
            mult = view.calculate_multiplier()
            
            if view.clicks + view.bombs_count == 25:
                for item in view.children: 
                    item.disabled = True
                view.stop()
                winnings = math.ceil(view.bet * mult)
                await add_points(interaction.guild, view.ctx.author, winnings)
                await interaction.response.edit_message(content=f"🏆 **PERFECT CLEAR!** Multiplier maxed: `{mult}x`. Payout: `{winnings}` points!", view=view)
            else:
                checkout_btn = discord.utils.get(view.children, custom_id="mines_cashout_node")
                if not checkout_btn: 
                    view.add_item(MinesCashoutButton())
                await interaction.response.edit_message(content=f"💎 Safe matrix node extracted. Evaluated Multiplier index: `{mult}x`", view=view)

class MinesCashoutButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cash Out", style=discord.ButtonStyle.green, row=4, custom_id="mines_cashout_node")

    async def callback(self, interaction: discord.Interaction):
        view: MinesGameView = self.view
        for item in view.children: 
            item.disabled = True
        view.stop()
        mult = view.calculate_multiplier()
        winnings = math.ceil(view.bet * mult) - view.bet
        await add_points(interaction.guild, view.ctx.author, winnings)
        await interaction.response.edit_message(content=f"💰 Extraction secure. Settled at `{mult}x`. Added `{winnings}` points to liquid assets.", view=view)

class MinesDifficultyView(discord.ui.View):
    def __init__(self, ctx, bet):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.bet = bet

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.author.id

    @discord.ui.button(label="Easy (3 Mines)", style=discord.ButtonStyle.green)
    async def easy_init(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Loading baseline grid sweeps layout matrix...", view=MinesGameView(self.ctx, self.bet, 3))

    @discord.ui.button(label="Medium (10 Mines)", style=discord.ButtonStyle.grey)
    async def med_init(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Constructing deep tactical hazard coordinates...", view=MinesGameView(self.ctx, self.bet, 10))

    @discord.ui.button(label="Hard (20 Mines)", style=discord.ButtonStyle.red)
    async def hard_init(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Loading extreme risk structural fields mappings...", view=MinesGameView(self.ctx, self.bet, 20))

@bot.command(name="mines")
async def launch_mines_game(ctx, bet: int):
    if bet <= 0: 
        return
    user_data = await get_user_data(ctx.guild.id, ctx.author.id)
    if user_data["points"] < bet: 
        return await ctx.send("Insufficient liquid asset tokens configurations to initialize wager.")
    await ctx.send("Select operational matrix threat parameters layers:", view=MinesDifficultyView(ctx, bet))

class BlackjackView(discord.ui.View):
    def __init__(self, ctx, bet):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.bet = bet
        self.deck = [2,3,4,5,6,7,8,9,10,10,10,10,11] * 4
        random.shuffle(self.deck)
        self.player_hand = [self.draw(), self.draw()]
        self.dealer_hand = [self.draw(), self.draw()]

    def draw(self): 
        return self.deck.pop()
        
    def eval_hand(self, hand):
        score = sum(hand)
        while score > 21 and 11 in hand: 
            hand[hand.index(11)] = 1
            score = sum(hand)
        return score
        
    def render_embed(self, finalized=False):
        emb = discord.Embed(title="Blackjack Operational Suite", color=discord.Color.gold())
        emb.add_field(name="Player Score", value=f"Cards: {self.player_hand}\nEvaluation metric: `{self.eval_hand(self.player_hand)}`", inline=True)
        if finalized: 
            emb.add_field(name="Dealer Score", value=f"Cards: {self.dealer_hand}\nEvaluation metric: `{self.eval_hand(self.dealer_hand)}`", inline=True)
        else: 
            emb.add_field(name="Dealer Score", value=f"Cards: [{self.dealer_hand[0]}, ?]", inline=True)
        return emb
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool: 
        return interaction.user.id == self.ctx.author.id

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.blurple)
    async def hit_node(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player_hand.append(self.draw())
        if self.eval_hand(self.player_hand) > 21:
            for item in self.children: 
                item.disabled = True
            self.stop()
            await add_points(interaction.guild, self.ctx.author, -self.bet)
            await interaction.response.edit_message(content="💥 Hand evaluation threshold busted! Wager lost.", embed=self.render_embed(True), view=self)
        else: 
            await interaction.response.edit_message(embed=self.render_embed(), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.grey)
    async def stand_node(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children: 
            item.disabled = True
        while self.eval_hand(self.dealer_hand) < 17: 
            self.dealer_hand.append(self.draw())
        p_total, d_total = self.eval_hand(self.player_hand), self.eval_hand(self.dealer_hand)
        if d_total > 21 or p_total > d_total: 
            await add_points(interaction.guild, self.ctx.author, self.bet)
            msg = f"🏆 Superiority established! Dispatched: `{self.bet}` points."
        elif p_total < d_total: 
            await add_points(interaction.guild, self.ctx.author, -self.bet)
            msg = f"📉 Deficit incurred. Lost: -`{self.bet}` points."
        else: 
            msg = "⚖️ Evaluation data push. Wagers pushed back balanced."
        self.stop()
        await interaction.response.edit_message(content=msg, embed=self.render_embed(True), view=self)

@bot.command(name="blackjack")
async def start_blackjack_table(ctx, bet: int):
    if bet <= 0: 
        return
    user_data = await get_user_data(ctx.guild.id, ctx.author.id)
    if user_data["points"] < bet: 
        return await ctx.send("Token ledger structural validation capacity balance error.")
    view = BlackjackView(ctx, bet)
    if view.eval_hand(view.player_hand) == 21:
        winnings = math.ceil(bet * 1.5)
        await add_points(ctx.guild, ctx.author, winnings)
        return await ctx.send(f"🃏 **NATURAL BLACKJACK!** Strategic payout allocated: `{winnings}` points.", embed=view.render_embed(True))
    await ctx.send(embed=view.render_embed(), view=view)

class CrashGameView(discord.ui.View):
    def __init__(self, ctx, bet):
        super().__init__(timeout=45)
        self.ctx = ctx
        self.bet = bet
        self.current_mult = 1.00
        self.cashed_out = False
        self.max_cap = round(random.uniform(1.1, 12.0), 2)
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool: 
        return interaction.user.id == self.ctx.author.id

    @discord.ui.button(label="Eject / Cash Out", style=discord.ButtonStyle.red)
    async def operational_eject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.cashed_out: 
            return
        self.cashed_out = True
        button.disabled = True
        self.stop()
        winnings = math.ceil(self.bet * self.current_mult) - self.bet
        await add_points(interaction.guild, self.ctx.author, winnings)
        await interaction.response.edit_message(content=f"🚀 **EJECT SECURE!** Multiplier locked at: `{self.current_mult}x`. Added: `{winnings}` tokens.", view=self)

@bot.command(name="crash")
async def load_crash_simulation(ctx, bet: int):
    if bet <= 0: 
        return
    user_data = await get_user_data(ctx.guild.id, ctx.author.id)
    if user_data["points"] < bet: 
        return await ctx.send("Insufficient active asset metrics configuration profile balances.")
    view = CrashGameView(ctx, bet)
    msg = await ctx.send(f"🚀 Vector scalar accelerated. Graph tracking point: `1.00x`", view=view)
    for tick in range(1, 100):
        await asyncio.sleep(0.6)
        if view.cashed_out: 
            break
        view.current_mult = round(view.current_mult + random.uniform(0.1, 0.45), 2)
        if view.current_mult >= view.max_cap:
            view.stop()
            for item in view.children: 
                item.disabled = True
            await add_points(ctx.guild, ctx.author, -bet)
            await msg.edit(content=f"💥 **CRASHED** at `{view.max_cap}x` trajectory vector limits. Wager asset units liquidated.", view=view)
            return
        try: 
            await msg.edit(content=f"🚀 Velocity multiplier scalar vectors climbing: `{view.current_mult}x` [random threshold bounds active]")
        except discord.HTTPException: 
            break

# ==========================================
#      FRIEND GROUP INTERACTION ENGINE
# ==========================================

# Fixed NameError routing: Subcommands must attach to 'friend_group_base_handler' instead of 'fg'
@bot.group(name="fg", invoke_without_command=True)
async def friend_group_base_handler(ctx):
    await ctx.send("Invalid sub-node operation tracking path parameter keys. Check usage charts inside `!help` dashboards.")

class FgInvitationView(discord.ui.View):
    def __init__(self, ctx, host, target, target_role_id, roles_list):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.host = host
        self.target = target
        self.target_role_id = target_role_id
        self.roles_list = roles_list

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("You are not the targeted recipient of this recruitment contract tracking node.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green, custom_id="fg_inv_accept")
    async def accept_node(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children: 
            item.disabled = True
        self.stop()
        user_data = await get_user_data(interaction.guild.id, self.target.id)
        if user_data["last_fg_switch"]:
            last_switch = datetime.fromisoformat(user_data["last_fg_switch"])
            if datetime.now(timezone.utc) - last_switch < timedelta(hours=24):
                time_remaining = timedelta(hours=24) - (datetime.now(timezone.utc) - last_switch)
                minutes = int(time_remaining.total_seconds() / 60)
                return await interaction.response.edit_message(content=f"❌ **DEFECTION COOLDOWN ACTIVE.** Migration parameters are locked. Remaining: `{minutes}` minutes.", view=self)
        success = await replace_fg_role(self.target, self.target_role_id, self.roles_list)
        if success:
            await update_switch_timestamp(interaction.guild.id, self.target.id)
            await interaction.response.edit_message(content=f"✅ **MIGRATION COMPLETE.** {self.target.mention} accepted the contract and joined <@&{self.target_role_id}> successfully.", view=self)
        else:
            await interaction.response.edit_message(content="❌ **HIERARCHY ERROR.** Bot permissions restriction layers avoid modifying role targets hierarchy scales.", view=self)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red, custom_id="fg_inv_decline")
    async def decline_node(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children: 
            item.disabled = True
        self.stop()
        await interaction.response.edit_message(content=f"❌ Invitation contract explicit declined by {self.target.mention}.", view=self)

class FgScrollingDirectoryView(discord.ui.View):
    def __init__(self, ctx, compiled_data, roles_list):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.compiled_data = compiled_data
        self.roles_list = roles_list
        self.current_page = 0
        self.max_pages = max(0, math.ceil(len(compiled_data) / 3) - 1)
        self.update_buttons()

    def update_buttons(self):
        prev_btn = discord.utils.get(self.children, custom_id="directory_prev")
        next_btn = discord.utils.get(self.children, custom_id="directory_next")
        if prev_btn: 
            prev_btn.disabled = (self.current_page == 0)
        if next_btn: 
            next_btn.disabled = (self.current_page >= self.max_pages)

    def render_page_embed(self) -> discord.Embed:
        emb = discord.Embed(title="Global Friend Group Directory", color=discord.Color.red())
        start = self.current_page * 3
        subset = self.compiled_data[start:start+3]
        if not subset:
            emb.description = "No group data tracks verified within this partition matrix."
            return emb
        for role, pts, count in subset:
            emb.add_field(
                name=f"🛡️ Group Forces: {role.name}", 
                value=f"• Handle Mapping: {role.mention}\n• Active Members: `{count}`\n• Cumulative Power Index: `{pts}`", 
                inline=False
            )
        emb.set_footer(text=f"Directory Display Index Page: {self.current_page + 1} / {self.max_pages + 1}")
        return emb

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id and interaction.data.get("custom_id") != "directory_join_node":
            await interaction.response.send_message("Initialize an independent discovery query session to scroll listings.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.grey, custom_id="directory_prev")
    async def page_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.render_page_embed(), view=self)

    @discord.ui.button(label="Join Selection", style=discord.ButtonStyle.green, custom_id="directory_join_node")
    async def self_service_join(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_data = await get_user_data(interaction.guild.id, interaction.user.id)
        if user_data["last_fg_switch"]:
            last_switch = datetime.fromisoformat(user_data["last_fg_switch"])
            if datetime.now(timezone.utc) - last_switch < timedelta(hours=24):
                time_remaining = timedelta(hours=24) - (datetime.now(timezone.utc) - last_switch)
                minutes = int(time_remaining.total_seconds() / 60)
                return await interaction.response.send_message(content=f"❌ **DEFECTION BLOCKED.** Profile switch locked. Cooldown remaining: `{minutes}` minutes.", ephemeral=True)
        start = self.current_page * 3
        subset = self.compiled_data[start:start+3]
        if not subset: 
            return await interaction.response.send_message("No valid targets selected.", ephemeral=True)
        target_role = subset[0][0]
        success = await replace_fg_role(interaction.user, target_role.id, self.roles_list)
        if success:
            await update_switch_timestamp(interaction.guild.id, interaction.user.id)
            await interaction.response.send_message(f"✅ **MIGRATION SETUP SECURE.** You joined group alliances: {target_role.mention}", ephemeral=True)
        else:
            await interaction.response.send_message("❌ **HIERARCHY EXCEPTION.** Discord role bounds prevent administrative replacement functions.", ephemeral=True)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.grey, custom_id="directory_next")
    async def page_forward(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_pages:
            self.current_page += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.render_page_embed(), view=self)

# Fixed decorators down here to bind to the group command handler cleanly
@friend_group_base_handler.command(name="inv")
async def issue_fg_invitation(ctx, target: discord.Member):
    config = load_json(CONFIG_FILE)
    roles_list = config.get(f"{ctx.guild.id}_fg_roles_list", [])
    
    # Locate host's current group role
    host_role_id = None
    for r_id in roles_list:
        role = ctx.guild.get_role(int(r_id))
        if role and role in ctx.author.roles:
            host_role_id = int(r_id)
            break
            
    if not host_role_id:
        return await ctx.send("❌ You are not associated with any recorded Friend Group alliances.")
        
    view = FgInvitationView(ctx, ctx.author, target, host_role_id, roles_list)
    await ctx.send(f"✉️ {target.mention}, you have received a recruitment ledger from {ctx.author.mention} to align with <@&{host_role_id}>.", view=view)

# Bot token initialization execution sequence
TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("CRITICAL: DISCORD_TOKEN is missing from your system environment variables configuration parameters setup.")
