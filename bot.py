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
#         ENVIRONMENT CONFIG
# ==========================================

DATA_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", ".")
DB_FILE = os.path.join(DATA_DIR, "activity.db")
CONFIG_FILE = os.path.join(DATA_DIR, "portal_config.json")

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.members = True
intents.message_content = True  

# ==========================================
#          DATABASE INITIALIZATION
# ==========================================

async def init_db():
    bot.db = await aiosqlite.connect(DB_FILE)
    await bot.db.execute("PRAGMA journal_mode=WAL;")  
    await bot.db.execute("PRAGMA synchronous=NORMAL;") 
    
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
#              BOT CORE SETUP
# ==========================================

bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)
voice_start_times = {}
last_voice_activity = {}

@bot.event
async def on_ready():
    await init_db()
    print(f"Bot ready: {bot.user.name}")
    if not fast_update_loop.is_running():
        fast_update_loop.start()
    if not daily_reset_loop.is_running():
        daily_reset_loop.start()

@bot.event
async def close():
    if hasattr(bot, 'db'):
        await bot.db.close()
    await super().close()

@bot.event
async def on_command_completion(ctx):
    try: 
        await ctx.message.add_reaction("✔️")
    except discord.HTTPException: 
        pass

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return 
    error_msg = "❌ Error running command."
    if isinstance(error, commands.MissingRequiredArgument):
        error_msg = f"❌ Missing args. Use: `{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`"
    elif isinstance(error, commands.BadArgument):
        error_msg = f"❌ Invalid input type. Use: `{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`"
    elif isinstance(error, commands.MissingPermissions):
        error_msg = "❌ Missing admin permissions."
    elif isinstance(error, commands.CommandOnCooldown):
        error_msg = f"⏳ Cooldown. Wait {round(error.retry_after, 1)}s."
    try:
        await ctx.send(error_msg, delete_after=15)
    except discord.HTTPException:
        pass

# ==========================================
#          DATABASE MANAGER HOOKS
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
#          MINIMAL HELP UI SYSTEM
# ==========================================

class HelpView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Not your menu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="[server] settings", style=discord.ButtonStyle.grey)
    async def cat_server(self, interaction: discord.Interaction, button: discord.ui.Button):
        emb = discord.Embed(title="[server] configuration", color=0xFFC0CB) # Pink
        emb.add_field(name="!setprefix <prefix>", value="Change bot command prefix.", inline=False)
        emb.add_field(name="!setportal <channel>", value="Link primary voice portal channel.", inline=False)
        emb.add_field(name="!setleaderboard <channel>", value="Bind text leaderboard updates.", inline=False)
        emb.add_field(name="!setfgroles <roles...>", value="Register friend group roles.", inline=False)
        await interaction.response.edit_message(embed=emb, view=self)

    @discord.ui.button(label="[category] general", style=discord.ButtonStyle.grey)
    async def cat_core(self, interaction: discord.Interaction, button: discord.ui.Button):
        emb = discord.Embed(title="[category] core utilities", color=0xFFFFFF) # White
        emb.add_field(name="!lb / !fglb", value="View individual or group leaderboards.", inline=False)
        emb.add_field(name="!dep / !withdraw", value="Manage point vault balances.", inline=False)
        emb.add_field(name="!give @user <amount>", value="Transfer points directly to a user.", inline=False)
        await interaction.response.edit_message(embed=emb, view=self)

    @discord.ui.button(label="[category] economy", style=discord.ButtonStyle.grey)
    async def cat_econ(self, interaction: discord.Interaction, button: discord.ui.Button):
        emb = discord.Embed(title="[category] shop & features", color=0xFFB6C1) # Light Pink
        emb.add_field(name="!shop", value="Open privilege store listings.", inline=False)
        emb.add_field(name="!buy mute @user", value="Mute user in voice for 5 minutes.", inline=False)
        emb.add_field(name="!buy nick @user <name>", value="Change user nickname for 1 hour.", inline=False)
        await interaction.response.edit_message(embed=emb, view=self)

    @discord.ui.button(label="[category] minigames", style=discord.ButtonStyle.grey)
    async def cat_games(self, interaction: discord.Interaction, button: discord.ui.Button):
        emb = discord.Embed(title="[category] games & friend groups", color=0xFFC0CB) # Pink
        emb.add_field(name="!mines / !blackjack", value="Wager points on casino games.", inline=False)
        emb.add_field(name="!fg list", value="Display server friend groups.", inline=False)
        emb.add_field(name="!fg inv @user", value="Send group invite link contract.", inline=False)
        emb.add_field(name="!fgarena @role <bet>", value="Challenge another squad to match wagers.", inline=False)
        await interaction.response.edit_message(embed=emb, view=self)

@bot.command(name="help")
async def process_help_system(ctx):
    view = HelpView(ctx)
    emb = discord.Embed(
        title="Help Menu", 
        description="Select a module category below to view options.", 
        color=0xFFB6C1 # Light Pink
    )
    await ctx.send(embed=emb, view=view)

# ==========================================
#          ADMINISTRATIVE OPTIONS
# ==========================================

@bot.command(name="setprefix")
@commands.has_permissions(administrator=True)
async def set_prefix(ctx, new_prefix: str = None):
    if not new_prefix: 
        return await ctx.send("Provide prefix symbol.")
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_prefix"] = new_prefix
    save_json(CONFIG_FILE, config)
    await ctx.send(f"Prefix saved: `{new_prefix}`")

@bot.command(name="setportal")
@commands.has_permissions(administrator=True)
async def set_portal(ctx, channel: discord.VoiceChannel = None):
    if not channel: return
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_portal"] = channel.id
    save_json(CONFIG_FILE, config)
    await ctx.send(f"Portal linked: {channel.name}")

@bot.command(name="setleaderboard")
@commands.has_permissions(administrator=True)
async def set_leaderboard_channel(ctx, channel: discord.TextChannel = None):
    if not channel: return
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_lb_channel"] = channel.id
    config.pop(f"{ctx.guild.id}_lb_msg_id", None)
    save_json(CONFIG_FILE, config)
    await ctx.send(f"Leaderboard linked: {channel.name}")

@bot.command(name="setfgchannel")
@commands.has_permissions(administrator=True)
async def set_fg_channel(ctx, channel: discord.TextChannel = None):
    if not channel: return
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_fg_lb_channel"] = channel.id
    config.pop(f"{ctx.guild.id}_fg_msg_id", None)
    save_json(CONFIG_FILE, config)
    await ctx.send(f"Group Leaderboard linked: {channel.name}")

@bot.command(name="setroles")
@commands.has_permissions(administrator=True)
async def set_roles(ctx, r1: discord.Role, r2: discord.Role, r3: discord.Role):
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_role1"] = r1.id
    config[f"{ctx.guild.id}_role2"] = r2.id
    config[f"{ctx.guild.id}_role3"] = r3.id
    save_json(CONFIG_FILE, config)
    await ctx.send("Tier configurations synchronized.")

@bot.command(name="setfgroles")
@commands.has_permissions(administrator=True)
async def set_fg_roles(ctx, *roles: discord.Role):
    if not roles: 
        return await ctx.send("Provide valid roles.")
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
    await ctx.send(f"Registered {len(config[cache_key])} Friend Group roles.")

@bot.command(name="setvipcategory")
@commands.has_permissions(administrator=True)
async def set_vip_category(ctx, category: discord.CategoryChannel = None):
    if not category: 
        return await ctx.send("Provide valid category.")
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_vip_category_id"] = category.id
    save_json(CONFIG_FILE, config)
    await ctx.send(f"VIP category set to: `{category.name}`")

@bot.command(name="setwelcome")
@commands.has_permissions(administrator=True)
async def set_welcome_role(ctx, role: discord.Role = None):
    config = load_json(CONFIG_FILE)
    if not role:
        config.pop(f"{ctx.guild.id}_welcome_role", None)
        await ctx.send("Welcome role reset.")
    else:
        config[f"{ctx.guild.id}_welcome_role"] = role.id
        await ctx.send(f"Welcome role set to: {role.name}")
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
            return await ctx.send("Enter whole numbers.")
    if val <= 0 or data["points"] < val: 
        return await ctx.send("Invalid balance parameters.")
    await add_points(ctx.guild, ctx.author, -val)
    await modify_bank(ctx.guild.id, ctx.author.id, val)
    await ctx.send(f"Deposited `{val}` points to your vault reserves.")

@bot.command(name="withdraw")
async def economic_withdraw(ctx, amount: str):
    data = await get_user_data(ctx.guild.id, ctx.author.id)
    if amount.lower() == "all": 
        val = data["bank"]
    else:
        try: 
            val = int(amount)
        except ValueError: 
            return await ctx.send("Enter whole numbers.")
    if val <= 0 or data["bank"] < val: 
        return await ctx.send("Insufficient vault balance.")
    await modify_bank(ctx.guild.id, ctx.author.id, -val)
    await add_points(ctx.guild, ctx.author, val)
    await ctx.send(f"Withdrew `{val}` points to active balance.")

@bot.command(name="give")
async def economic_peer_give(ctx, target: discord.Member, amount: int):
    if amount <= 0: 
        return await ctx.send("Enter positive amounts.")
    source_data = await get_user_data(ctx.guild.id, ctx.author.id)
    if source_data["points"] < amount: 
        return await ctx.send("Insufficient active balance.")
    await add_points(ctx.guild, ctx.author, -amount)
    await add_points(ctx.guild, target, amount)
    await ctx.send(f"Sent `{amount}` points to {target.mention}.")

# ==========================================
#          MARKETPLACE & STORE
# ==========================================

@bot.command(name="shop")
async def display_shop_layout(ctx):
    emb = discord.Embed(title="Store Listings", color=0xFFC0CB) # Pink
    emb.add_field(name="!buy mute @user", value="Mute user in voice (5 mins).\nCost: 50k points or 10% total balance (highest).", inline=False)
    emb.add_field(name="!buy nick @user <name>", value="Change user nickname (1 hr).\nCost: Standard 10k, Mod 25k, Admin 35k.", inline=False)
    await ctx.send(embed=emb)

@bot.command(name="buy")
async def purchase_processor(ctx, item_type: str, target: discord.Member = None, *, aux_args: str = None):
    if not target: 
        return await ctx.send("Specify target user.")
    user_data = await get_user_data(ctx.guild.id, ctx.author.id)
    total_wealth = user_data["points"] + user_data["bank"]
    
    if item_type.lower() == "mute":
        cost = max(50000, math.ceil(total_wealth * 0.10))
        if user_data["points"] < cost: 
            return await ctx.send(f"Insufficient active funds. Cost: `{cost}`")
        try:
            await target.timeout(timedelta(minutes=5), reason="Store mute item purchase.")
            await add_points(ctx.guild, ctx.author, -cost)
            await ctx.send(f"Muted {target.mention} for 5 minutes. Charged `{cost}`.")
        except discord.Forbidden: 
            await ctx.send("Permissions error.")
            
    elif item_type.lower() in ["nickname", "nick"]:
        if not aux_args: 
            return await ctx.send("Specify the new nickname text.")
        if target.guild_permissions.administrator: 
            cost = max(35000, math.ceil(total_wealth * 0.10))
        elif target.guild_permissions.manage_messages: 
            cost = 25000
        else: 
            cost = 10000
            
        if user_data["points"] < cost: 
            return await ctx.send(f"Insufficient active funds. Cost: `{cost}`")
        try:
            await target.edit(nick=aux_args[:32])
            await add_points(ctx.guild, ctx.author, -cost)
            await ctx.send(f"Changed nickname of {target.mention}. Charged `{cost}`.")
        except discord.Forbidden: 
            await ctx.send("Permissions error.")

# ==========================================
#          ANONYMOUS CONFESSIONS
# ==========================================

class ConfessionSubmissionModal(discord.ui.Modal, title="Submit Confession"):
    content_input = discord.ui.TextInput(label="Confession Text", style=discord.TextStyle.long, required=True, max_length=1500)
    
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
            pub_emb = discord.Embed(title=f"Anonymous Confession #{generated_id}", description=self.content_input.value, color=0xFFB6C1) # Light Pink
            await public_channel.send(embed=pub_emb)
        if logs_channel:
            log_emb = discord.Embed(title=f"Confession Audit Logs #{generated_id}", description=self.content_input.value, color=0xFFFFFF) # White
            log_emb.add_field(name="User", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
            await logs_channel.send(embed=log_emb)
            
        await interaction.response.send_message("Confession submitted.", ephemeral=True)

class ConfessionTriggerView(discord.ui.View):
    def __init__(self, logs_channel_id):
        super().__init__(timeout=None)
        self.logs_channel_id = logs_channel_id

    @discord.ui.button(label="Submit Confession", style=discord.ButtonStyle.blurple, custom_id="trigger_confession_modal")
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
    emb = discord.Embed(title="Anonymous Confessions", description="Click button below to submit confession safely.", color=0xFFC0CB) # Pink
    await ctx.send(embed=emb, view=view)

# ==========================================
#          CASINO CASUAL ENGINE
# ==========================================

class MinesButton(discord.ui.Button):
    def __init__(self, index, row):
        super().__init__(style=discord.ButtonStyle.grey, label="?", row=row)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view: MinesGameView = self.view
        if interaction.user.id != view.ctx.author.id:
            return await interaction.response.send_message("Not your game.", ephemeral=True)
            
        if view.revealed[self.index]:
            return await interaction.response.defer()

        view.revealed[self.index] = True
        if view.grid[self.index] == "bomb":
            self.style = discord.ButtonStyle.red
            self.label = "💥"
            self.disabled = True
            view.stop_game(won=False)
            emb = discord.Embed(title="Boom!", description=f"You hit a mine. Lost `{view.bet}` points.", color=0xFFFFFF) # White
            await interaction.response.edit_message(embed=emb, view=view)
        else:
            self.style = discord.ButtonStyle.green
            self.label = "💎"
            self.disabled = True
            view.clicks += 1
            
            if view.clicks == (25 - view.bombs_count):
                view.stop_game(won=True)
                mult = view.calculate_multiplier()
                winnings = math.ceil(view.bet * mult)
                emb = discord.Embed(title="Victory!", description=f"Grid cleared! Won `{winnings}` points ({mult:.2f}x).", color=0xFFC0CB) # Pink
                await interaction.response.edit_message(embed=emb, view=view)
            else:
                mult = view.calculate_multiplier()
                emb = discord.Embed(title="Mines", description=f"Current Multiplier: `{mult:.2f}x`", color=0xFFB6C1) # Light Pink
                await interaction.response.edit_message(embed=emb, view=view)

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
        prob = num / den if den > 0 else 1.0
        return round(0.95 / max(prob, 0.0001), 2)

    def stop_game(self, won):
        for item in self.children:
            item.disabled = True
        self.stop()
        asyncio.create_task(self.payout(won))

    async def payout(self, won):
        if won:
            mult = self.calculate_multiplier()
            winnings = math.ceil(self.bet * mult) - self.bet
            await add_points(self.ctx.guild, self.ctx.author, winnings)
        else:
            await add_points(self.ctx.guild, self.ctx.author, -self.bet)

@bot.command(name="mines")
async def start_mines(ctx, bet: int, mines: int = -1):
    if mines < 1 or mines > 24: 
        mines = random.choice([1, 3, 5, 10])
    if bet <= 0: 
        return await ctx.send("Enter valid wager amount.")
        
    data = await get_user_data(ctx.guild.id, ctx.author.id)
    if data["points"] < bet: 
        return await ctx.send("Insufficient active funds.")
        
    view = MinesGameView(ctx, bet, mines)
    emb = discord.Embed(title="Mines", description=f"Mines config: `{mines}` | Multiplier starts: `1.00x`", color=0xFFB6C1) # Light Pink
    await ctx.send(embed=emb, view=view)

# ==========================================
#          BLACKJACK SYSTEM
# ==========================================

class BlackjackView(discord.ui.View):
    def __init__(self, ctx, bet):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.bet = bet
        self.deck = [2,3,4,5,6,7,8,9,10,10,10,10,11] * 4
        self.player_hand = [self.draw(), self.draw()]
        self.dealer_hand = [self.draw(), self.draw()]

    def draw(self):
        return random.choice(self.deck)

    def count(self, hand):
        val = sum(hand)
        aces = hand.count(11)
        while val > 21 and aces:
            val -= 10
            aces -= 1
        return val

    def get_embed(self, done=False):
        p_score = self.count(self.player_hand)
        d_score = self.count(self.dealer_hand)
        
        emb = discord.Embed(title="Blackjack Table", color=0xFFC0CB) # Pink
        emb.add_field(name="Your Hand", value=f"Cards: {self.player_hand}\nScore: `{p_score}`", inline=True)
        if done:
            emb.add_field(name="Dealer Hand", value=f"Cards: {self.dealer_hand}\nScore: `{d_score}`", inline=True)
        else:
            emb.add_field(name="Dealer Hand", value=f"Cards: [{self.dealer_hand[0]}, ?]", inline=True)
        return emb

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.green)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id: 
            return
        self.player_hand.append(self.draw())
        if self.count(self.player_hand) > 21:
            self.stop()
            await add_points(self.ctx.guild, self.ctx.author, -self.bet)
            emb = self.get_embed(done=True)
            emb.description = f"❌ Bust! Lost `{self.bet}` points."
            await interaction.response.edit_message(embed=emb, view=None)
        else:
            await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.red)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id: 
            return
        self.stop()
        while self.count(self.dealer_hand) < 17:
            self.dealer_hand.append(self.draw())
            
        p_tot = self.count(self.player_hand)
        d_tot = self.count(self.dealer_hand)
        emb = self.get_embed(done=True)
        
        if d_tot > 21 or p_tot > d_tot:
            await add_points(self.ctx.guild, self.ctx.author, self.bet)
            emb.description = f"🎉 Winner! Won `{self.bet}` points."
        elif p_tot < d_tot:
            await add_points(self.ctx.guild, self.ctx.author, -self.bet)
            emb.description = f"❌ Dealer wins. Lost `{self.bet}` points."
        else:
            emb.description = "👔 Push match! Bet returned."
            
        await interaction.response.edit_message(embed=emb, view=None)

@bot.command(name="blackjack")
async def blackjack_command(ctx, bet: int):
    if bet <= 0: 
        return await ctx.send("Enter valid wager amount.")
    data = await get_user_data(ctx.guild.id, ctx.author.id)
    if data["points"] < bet: 
        return await ctx.send("Insufficient active funds.")
        
    view = BlackjackView(ctx, bet)
    if view.count(view.player_hand) == 21:
        winnings = math.ceil(bet * 1.5)
        await add_points(ctx.guild, ctx.author, winnings)
        emb = view.get_embed(done=True)
        emb.description = f"🎉 Blackjack! Won `{winnings}` points."
        await ctx.send(embed=emb)
    else:
        await ctx.send(embed=view.get_embed(), view=view)

# ==========================================
#          FIXED FRIEND GROUP MINIGAMES
# ==========================================

class ArenaMatchView(discord.ui.View):
    def __init__(self, ctx, host_role, target_role, bet):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.host_role = host_role
        self.target_role = target_role
        self.bet = bet

    @discord.ui.button(label="Accept Challenge", style=discord.ButtonStyle.success)
    async def accept_match(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.target_role not in interaction.user.roles:
            return await interaction.response.send_message("You lack opponent role.", ephemeral=True)
        
        self.stop()
        winner = random.choice([self.host_role, self.target_role])
        loser = self.host_role if winner == self.target_role else self.target_role
        
        await bot.db.execute("UPDATE group_activity SET points = points + ? WHERE role_id = ?", (self.bet, str(winner.id)))
        await bot.db.execute("UPDATE group_activity SET points = max(0, points - ?) WHERE role_id = ?", (self.bet, str(loser.id)))
        await bot.db.commit()

        emb = discord.Embed(title="Match Resolved", color=0xFFC0CB) # Pink
        emb.add_field(name="Winner 🎉", value=f"{winner.mention} takes `{self.bet}` points!", inline=False)
        emb.add_field(name="Loser ❌", value=f"{loser.mention} lost wagered balance.", inline=False)
        await interaction.response.edit_message(embed=emb, view=None)

@bot.command(name="fgarena")
async def host_group_duel(ctx, target_role: discord.Role, bet: int):
    if bet <= 0: 
        return await ctx.send("Enter a valid bet.")
    config = load_json(CONFIG_FILE)
    roles_list = config.get(f"{ctx.guild.id}_fg_roles_list", [])
    
    host_role = next((ctx.guild.get_role(int(rid)) for rid in roles_list if int(rid) in [r.id for r in ctx.author.roles]), None)
    if not host_role:
        return await ctx.send("You don't belong to a Friend Group.")

    async with bot.db.execute("SELECT points FROM group_activity WHERE role_id = ?", (str(host_role.id),)) as c:
        row = await c.fetchone()
    if not row or row[0] < bet:
        return await ctx.send("Your friend group lacks enough points.")

    view = ArenaMatchView(ctx, host_role, target_role, bet)
    emb = discord.Embed(
        title="Group Duel Challenge", 
        description=f"{host_role.mention} challenged {target_role.mention} for `{bet}` points!", 
        color=0xFFFFFF # White
    )
    await ctx.send(embed=emb, view=view)

# ==========================================
#          FRIEND GROUP HANDLERS
# ==========================================

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
            await interaction.response.send_message("Not your invitation hook.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = await get_user_data(interaction.guild.id, self.target.id)
        now = datetime.now(timezone.utc)
        
        if data["last_fg_switch"]:
            prev_switch = datetime.fromisoformat(data["last_fg_switch"])
            if now - prev_switch < timedelta(days=1):
                rem = timedelta(days=1) - (now - prev_switch)
                return await interaction.response.send_message(f"Cooldown active. Wait {round(rem.total_seconds() / 3600, 1)} hrs.", ephemeral=True)
                
        self.stop()
        success = await replace_fg_role(self.target, self.target_role_id, self.roles_list)
        if success:
            await update_switch_timestamp(interaction.guild.id, self.target.id)
            emb = discord.Embed(title="Invite Accepted", description=f"{self.target.mention} joined <@&{self.target_role_id}>.", color=0xFFC0CB) # Pink
            await interaction.response.edit_message(embed=emb, view=None)
        else:
            await interaction.response.edit_message(content="❌ Switch failed. Check permissions.", view=None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        emb = discord.Embed(title="Invite Declined", description="Invitation contract rejected.", color=0xFFFFFF) # White
        await interaction.response.edit_message(embed=emb, view=None)

@bot.group(name="fg", invoke_without_command=True)
async def fg(ctx):
    await ctx.send("Use `!fg list` or `!fg inv @user`")

@fg.command(name="list")
async def list_groups(ctx):
    config = load_json(CONFIG_FILE)
    roles_list = config.get(f"{ctx.guild.id}_fg_roles_list", [])
    if not roles_list:
        return await ctx.send("No tracked groups config discovered.")
        
    emb = discord.Embed(title="Server Friend Groups", color=0xFFB6C1) # Light Pink
    for r_id in roles_list:
        role = ctx.guild.get_role(int(r_id))
        if role:
            async with bot.db.execute("SELECT points FROM group_activity WHERE role_id = ?", (str(r_id),)) as c:
                row = await c.fetchone()
            pts = row[0] if row else 0
            emb.add_field(name=role.name, value=f"Points: `{pts}` | Members: {len(role.members)}", inline=False)
            
    await ctx.send(embed=emb)

@fg.command(name="inv")
async def invite_to_group(ctx, target: discord.Member):
    config = load_json(CONFIG_FILE)
    roles_list = config.get(f"{ctx.guild.id}_fg_roles_list", [])
    
    host_role_id = None
    for r_id in roles_list:
        role = ctx.guild.get_role(int(r_id))
        if role and role in ctx.author.roles:
            host_role_id = int(r_id)
            break
            
    if not host_role_id:
        return await ctx.send("❌ You don't have a tracked group role.")
        
    view = FgInvitationView(ctx, ctx.author, target, host_role_id, roles_list)
    emb = discord.Embed(title="Group Invitation", description=f"{target.mention}, you have been invited to join <@&{host_role_id}> by {ctx.author.mention}.", color=0xFFC0CB) # Pink
    await ctx.send(embed=emb, view=view)

# ==========================================
#          FIXED RANDOM VC FEATURE
# ==========================================

@bot.command(name="randomvc")
async def join_random_voice(ctx):
    """Finds a valid occupied or empty voice channel safely to clear channel routing loops."""
    voice_channels = [vc for vc in ctx.guild.voice_channels if len(vc.members) > 0]
    
    if not voice_channels:
        voice_channels = ctx.guild.voice_channels

    if not voice_channels:
        return await ctx.send("No voice channels found.")

    selected_vc = random.choice(voice_channels)
    
    emb = discord.Embed(title="Voice Route Selection", color=0xFFB6C1) # Light Pink
    emb.add_field(name="Channel Target", value=f"🔊 {selected_vc.name}", inline=True)
    emb.add_field(name="Active Users", value=f"👥 {len(selected_vc.members)} online", inline=True)
    
    try:
        invite = await selected_vc.create_invite(max_age=300, max_uses=1)
        # Markdown hyperlinking matches minimalist framework instructions
        emb.description = f"[Click here to connect channel]({invite.url})"
    except discord.Forbidden:
        emb.description = "Could not generate invite link link due to permissions."

    await ctx.send(embed=emb)

# ==========================================
#          LEADERBOARD TEXT PIPIELINES
# ==========================================

async def draw_leaderboard(guild, is_fg=False):
    config = load_json(CONFIG_FILE)
    emb = discord.Embed(color=0xFFC0CB if is_fg else 0xFFFFFF) # Pink if group, White if user
    
    if is_fg:
        emb.title = "Friend Group Rankings"
        rows = await get_top_groups(guild.id, limit=15)
        if not rows:
            emb.description = "No rankings metrics collected."
        for idx, (r_id, pts) in enumerate(rows, 1):
            role = guild.get_role(int(r_id))
            name = role.name if role else f"Deleted Role ({r_id})"
            emb.add_field(name=f"{idx}. {name}", value=f"Points Balance: `{pts}`", inline=False)
    else:
        emb.title = "Active User Standings"
        rows = await get_top_users(guild.id, limit=10)
        if not rows:
            emb.description = "No tracking stats recorded."
        for idx, (u_id, pts) in enumerate(rows, 1):
            member = guild.get_member(int(u_id))
            name = member.name if member else f"Left User ({u_id})"
            emb.add_field(name=f"{idx}. {name}", value=f"Active Balance: `{pts}`", inline=False)
            
    return emb

@bot.command(name="leaderboard", aliases=["lb"])
async def show_user_lb(ctx):
    emb = await draw_leaderboard(ctx.guild, is_fg=False)
    await ctx.send(embed=emb)

@bot.command(name="fgleaderboard", aliases=["fglb"])
async def show_fg_lb(ctx):
    emb = await draw_leaderboard(ctx.guild, is_fg=True)
    await ctx.send(embed=emb)

# ==========================================
#          VOICE PORTALS ENGINE
# ==========================================

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot: return
    config = load_json(CONFIG_FILE)
    portal_id = config.get(f"{member.guild.id}_portal")
    
    if after.channel and after.channel.id == portal_id:
        category_id = config.get(f"{member.guild.id}_vip_category_id")
        category = member.guild.get_channel(category_id) if category_id else None
        
        try:
            new_vc = await member.guild.create_voice_channel(
                name=f"Lounge // {member.name}", 
                category=category,
                reason="Portal routing connection trigger."
            )
            await member.move_to(new_vc)
            last_voice_activity[new_vc.id] = datetime.now(timezone.utc)
        except (discord.HTTPException, discord.Forbidden):
            pass

    if before.channel:
        if before.channel.name.startswith("Lounge //") and len(before.channel.members) == 0:
            try: 
                await before.channel.delete(reason="Empty lounge cleanup cycle.")
                last_voice_activity.pop(before.channel.id, None)
            except discord.HTTPException: 
                pass

    # Tracking update state entry parameters
    if after.channel and not before.channel:
        voice_start_times[member.id] = datetime.now(timezone.utc)
    elif before.channel and not after.channel:
        start_time = voice_start_times.pop(member.id, None)
        if start_time:
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            pts = math.ceil(duration / 60) * 2
            if pts > 0:
                await add_points(member.guild, member, pts)

# ==========================================
#          BACKGROUND LOOPS
# ==========================================

@tasks.loop(seconds=30)
async def fast_update_loop():
    await bot.wait_until_ready()
    now = datetime.now(timezone.utc)
    config = load_json(CONFIG_FILE)
    
    for guild in bot.guilds:
        # Voice updates loop points distribution
        for vc in guild.voice_channels:
            if len(vc.members) >= 2:
                for m in vc.members:
                    if not m.bot and not m.voice.self_deaf:
                        await add_points(guild, m, 1)

        # Telemetry updates feed loop
        txt_id = config.get(f"{guild.id}_lb_channel")
        txt_chan = guild.get_channel(txt_id) if txt_id else None
        if txt_chan:
            emb = await draw_leaderboard(guild, is_fg=False)
            msg_id = config.get(f"{guild.id}_lb_msg_id")
            if msg_id:
                try:
                    msg = await txt_chan.fetch_message(msg_id)
                    await msg.edit(embed=emb)
                except discord.HTTPException:
                    msg = await txt_chan.send(embed=emb)
                    config[f"{guild.id}_lb_msg_id"] = msg.id
                    save_json(CONFIG_FILE, config)
            else:
                msg = await txt_chan.send(embed=emb)
                config[f"{guild.id}_lb_msg_id"] = msg.id
                save_json(CONFIG_FILE, config)

        fg_id = config.get(f"{guild.id}_fg_lb_channel")
        fg_chan = guild.get_channel(fg_id) if fg_id else None
        if fg_chan:
            emb_fg = await draw_leaderboard(guild, is_fg=True)
            fg_msg_id = config.get(f"{guild.id}_fg_msg_id")
            if fg_msg_id:
                try:
                    msg = await fg_chan.fetch_message(fg_msg_id)
                    await msg.edit(embed=emb_fg)
                except discord.HTTPException:
                    msg = await fg_chan.send(embed=emb_fg)
                    config[f"{guild.id}_fg_msg_id"] = msg.id
                    save_json(CONFIG_FILE, config)
            else:
                msg = await fg_chan.send(embed=emb_fg)
                config[f"{guild.id}_fg_msg_id"] = msg.id
                save_json(CONFIG_FILE, config)

@tasks.loop(hours=24)
async def daily_reset_loop():
    await bot.wait_until_ready()
    # Flushes standard daily progress targets once daily down paths
    await bot.db.execute("UPDATE user_activity SET daily_progress = 0, checked_in = 0")
    await bot.db.commit()

@fast_update_loop.before_loop
async def before_fast_loop(): 
    await bot.wait_until_ready()
    
@daily_reset_loop.before_loop
async def before_daily_loop(): 
    await bot.wait_until_ready()

# ==========================================
#          MATRIX START POINT
# ==========================================

TOKEN = os.getenv("DISCORD_TOKEN")
if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("Error: Missing DISCORD_TOKEN environmental string parameter value.")
