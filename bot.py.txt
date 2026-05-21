
import os
import random
import json
import discord
from discord.ext import commands, tasks

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.members = True
intents.message_content = True  

CONFIG_FILE = "portal_config.json"
ACTIVITY_FILE = "activity_data.json"

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            try: return json.load(f)
            except json.JSONDecodeError: return {}
    return {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

def get_prefix(bot_instance, message):
    if not message.guild:
        return "!"
    config = load_json(CONFIG_FILE)
    return config.get(f"{message.guild.id}_prefix", "!")

bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)

voice_start_times = {}

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="/leftovers"))
    if not fast_update_loop.is_running():
        fast_update_loop.start()

@bot.event
async def on_command_completion(ctx):
    try:
        await ctx.message.add_reaction("✔️")
    except discord.HTTPException:
        pass

@bot.command(name="help")
async def help_ui(ctx):
    prefix = ctx.prefix
    embed = discord.Embed(
        title="Activity Systems",
        description="Public tracking utilities.",
        color=discord.Color.dark_grey()
    )
    embed.add_field(name=f"`{prefix}leaderboard` (or `{prefix}lb`)", value="Display the current individual activity rankings.", inline=False)
    embed.add_field(name=f"`{prefix}fgleaderboard` (or `{prefix}fglb`)", value="Display the current group standing data.", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="setprefix")
@commands.has_permissions(administrator=True)
async def set_prefix(ctx, new_prefix: str = None):
    if not new_prefix:
        return
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_prefix"] = new_prefix
    save_json(CONFIG_FILE, config)
    await ctx.send(f"Prefix updated. The command prefix for this server is now: {new_prefix}")

@bot.command(name="setportal")
@commands.has_permissions(administrator=True)
async def set_portal(ctx, channel: discord.VoiceChannel = None):
    if not channel: return
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_portal"] = channel.id
    save_json(CONFIG_FILE, config)
    await ctx.send(f"Portal established. Anyone entering {channel.name} will be instantly moved.")

@bot.command(name="setleaderboard")
@commands.has_permissions(administrator=True)
async def set_leaderboard_channel(ctx, channel: discord.TextChannel = None):
    if not channel: return
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_lb_channel"] = channel.id
    
    if f"{ctx.guild.id}_lb_msg_id" in config:
        del config[f"{ctx.guild.id}_lb_msg_id"]
        
    save_json(CONFIG_FILE, config)
    await ctx.send(f"Live leaderboard updates targeted to {channel.name}.")

@bot.command(name="setroles")
@commands.has_permissions(administrator=True)
async def set_roles(ctx, r1: discord.Role, r2: discord.Role, r3: discord.Role):
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_role1"] = r1.id
    config[f"{ctx.guild.id}_role2"] = r2.id
    config[f"{ctx.guild.id}_role3"] = r3.id
    save_json(CONFIG_FILE, config)
    await ctx.send(f"Activity roles configured:\n1st: {r1.name}\n2nd: {r2.name}\n3rd: {r3.name}")

@bot.command(name="setfgchannel")
@commands.has_permissions(administrator=True)
async def set_fg_channel(ctx, channel: discord.TextChannel = None):
    if not channel: return
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_fg_lb_channel"] = channel.id
    
    if f"{ctx.guild.id}_fg_msg_id" in config:
        del config[f"{ctx.guild.id}_fg_msg_id"]
        
    save_json(CONFIG_FILE, config)
    await ctx.send(f"Friend Group updates targeted to {channel.name}.")

@bot.command(name="setfgroles")
@commands.has_permissions(administrator=True)
async def set_fg_roles(ctx, r1: discord.Role, r2: discord.Role = None, r3: discord.Role = None):
    config = load_json(CONFIG_FILE)
    config[f"{ctx.guild.id}_fg_role1"] = r1.id
    config[f"{ctx.guild.id}_fg_role2"] = r2.id if r2 else None
    config[f"{ctx.guild.id}_fg_role3"] = r3.id if r3 else None
    save_json(CONFIG_FILE, config)
    await ctx.send(f"Friend Group roles updated. Tracking point contributions for matching roles.")

def add_points(guild, member, points):
    guild_id = str(guild.id)
    user_id = str(member.id)
    
    config = load_json(CONFIG_FILE)
    data = load_json(ACTIVITY_FILE)
    
    if guild_id not in data: data[guild_id] = {"users": {}, "groups": {}}
    if "users" not in data[guild_id]: data[guild_id]["users"] = {}
    if "groups" not in data[guild_id]: data[guild_id]["groups"] = {}
    
    data[guild_id]["users"][user_id] = data[guild_id]["users"].get(user_id, 0) + points
    
    fg_keys = [f"{guild_id}_fg_role1", f"{guild_id}_fg_role2", f"{guild_id}_fg_role3"]
    for key in fg_keys:
        role_id = config.get(key)
        if role_id:
            role = guild.get_role(role_id)
            if role in member.roles:
                role_id_str = str(role_id)
                data[guild_id]["groups"][role_id_str] = data[guild_id]["groups"].get(role_id_str, 0) + points
                
    save_json(ACTIVITY_FILE, data)

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    ctx = await bot.get_context(message)
    if ctx.valid:
        await bot.process_commands(message)
        return

    add_points(message.guild, message.author, 1)
    await bot.process_commands(message)

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot: return

    guild_id = str(member.guild.id)
    user_id = str(member.id)
    config = load_json(CONFIG_FILE)

    if before.channel and not after.channel: 
        if user_id in voice_start_times:
            duration = discord.utils.utcnow() - voice_start_times[user_id]
            minutes = int(duration.total_seconds() / 60)
            if minutes > 0:
                add_points(member.guild, member, minutes * 2)
            del voice_start_times[user_id]
    elif not before.channel and after.channel: 
        voice_start_times[user_id] = discord.utils.utcnow()

    if after.channel:
        portal_channel_id = config.get(f"{guild_id}_portal")
        if portal_channel_id and after.channel.id == portal_channel_id:
            destination_vcs = [vc for vc in member.guild.voice_channels if vc.id != portal_channel_id and vc.permissions_for(member).connect]
            if destination_vcs:
                try: await member.move_to(random.choice(destination_vcs))
                except discord.Forbidden:
                    try: await member.send("You cannot be departed due to administrative permissions.")
                    except discord.Forbidden: pass
            else:
                try: await member.move_to(None) 
                except discord.Forbidden: pass
                try:
                    await member.send(embed=discord.Embed(title="Disconnected", description=f"You entered the portal in {member.guild.name}, but there were no open channels left.", color=discord.Color.dark_grey()))
                except discord.Forbidden: pass

@bot.command(name="leaderboard", aliases=["lb"])
async def show_leaderboard(ctx):
    await ctx.send(embed=generate_user_embed(ctx.guild))

@bot.command(name="fgleaderboard", aliases=["fglb"])
async def show_fg_leaderboard(ctx):
    await ctx.send(embed=generate_fg_embed(ctx.guild))

def generate_user_embed(guild):
    data = load_json(ACTIVITY_FILE)
    guild_id = str(guild.id)
    
    embed = discord.Embed(title=f"{guild.name.upper()} // INDIVIDUAL STANDINGS", color=discord.Color.black())
    
    users_data = data.get(guild_id, {}).get("users", {})
    if not users_data:
        embed.description = "No data active."
        return embed

    sorted_users = sorted(users_data.items(), key=lambda item: item[1], reverse=True)[:10]
    
    lb_text = ""
    for i, (u_id, score) in enumerate(sorted_users, 1):
        lb_text += f"Rank {i:02d} | <@{u_id}>\nScore: {score} points\n\n"
        
    embed.description = lb_text
    return embed

def generate_fg_embed(guild):
    data = load_json(ACTIVITY_FILE)
    guild_id = str(guild.id)
    embed = discord.Embed(title=f"{guild.name.upper()} // GROUP STANDINGS", color=discord.Color.black())
    
    group_data = data.get(guild_id, {}).get("groups", {})
    if not group_data:
        embed.description = "No group records allocated."
        return embed

    sorted_groups = sorted(group_data.items(), key=lambda item: item[1], reverse=True)[:5]
    lb_text = ""
    for index, (r_id, score) in enumerate(sorted_groups, 1):
        role = guild.get_role(int(r_id))
        role_name = f"<@&{r_id}>" if role else "Null Allocation"
        lb_text += f"Group {index:02d} | {role_name}\nIndex: {score} cumulative\n\n"
        
    embed.description = lb_text
    return embed

@tasks.loop(seconds=30)
async def fast_update_loop():
    config = load_json(CONFIG_FILE)
    data = load_json(ACTIVITY_FILE)
    config_changed = False
    
    for guild in bot.guilds:
        guild_id = str(guild.id)
        
        u_channel_id = config.get(f"{guild_id}_lb_channel")
        if u_channel_id:
            u_channel = guild.get_channel(u_channel_id)
            if u_channel:
                embed = generate_user_embed(guild)
                msg_id = config.get(f"{guild_id}_lb_msg_id")
                msg_verified = False
                
                if msg_id:
                    try:
                        msg = await u_channel.fetch_message(msg_id)
                        await msg.edit(embed=embed)
                        msg_verified = True
                    except (discord.NotFound, discord.HTTPException):
                        pass
                
                if not msg_verified:
                    try:
                        new_msg = await u_channel.send(embed=embed)
                        config[f"{guild_id}_lb_msg_id"] = new_msg.id
                        config_changed = True
                    except discord.HTTPException:
                        pass

        g_channel_id = config.get(f"{guild_id}_fg_lb_channel")
        if g_channel_id:
            g_channel = guild.get_channel(g_channel_id)
            if g_channel:
                embed = generate_fg_embed(guild)
                msg_id = config.get(f"{guild_id}_fg_msg_id")
                msg_verified = False
                
                if msg_id:
                    try:
                        msg = await g_channel.fetch_message(msg_id)
                        await msg.edit(embed=embed)
                        msg_verified = True
                    except (discord.NotFound, discord.HTTPException):
                        pass
                
                if not msg_verified:
                    try:
                        new_msg = await g_channel.send(embed=embed)
                        config[f"{guild_id}_fg_msg_id"] = new_msg.id
                        config_changed = True
                    except discord.HTTPException:
                        pass

        r1_id, r2_id, r3_id = config.get(f"{guild_id}_role1"), config.get(f"{guild_id}_role2"), config.get(f"{guild_id}_role3")
        roles = [guild.get_role(r1_id), guild.get_role(r2_id), guild.get_role(r3_id)]
        users_data = data.get(guild_id, {}).get("users", {})
        
        if users_data:
            sorted_users = sorted(users_data.items(), key=lambda item: item[1], reverse=True)[:3]
            for r in roles:
                if r:
                    for m in r.members:
                        if m.id not in [int(uid) for uid, _ in sorted_users]:
                            try: await m.remove_roles(r)
                            except: pass
            for rank, (u_id, _) in enumerate(sorted_users):
                if rank < len(roles) and roles[rank]:
                    member = guild.get_member(int(u_id))
                    if member and roles[rank] not in member.roles:
                        try: await member.add_roles(roles[rank])
                        except: pass

    if config_changed:
        save_json(CONFIG_FILE, config)

@fast_update_loop.before_loop
async def before_fast_update_loop():
    await bot.wait_until_ready()

TOKEN = os.getenv("DISCORD_TOKEN")

if __name__ == "__main__":
    if TOKEN: bot.run(TOKEN)
    else: print("CRITICAL ERROR: DISCORD_TOKEN missing!")
