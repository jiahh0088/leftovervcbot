
import os
import random
import json
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.members = True
intents.message_content = True  

# Use a custom help command instead of the default one
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

CONFIG_FILE = "portal_config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    # Updated presence to watch /leftovers
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="/leftovers"))

@bot.command(name="help")
async def help_ui(ctx):
    """Simple and short help UI"""
    embed = discord.Embed(
        title="Portal Bot Help",
        description="A simple tool for managing the departure portal.",
        color=discord.Color.blue()
    )
    embed.add_field(name="`!setportal #channel`", value="Sets the voice channel that triggers a departure. (Admin only)", inline=False)
    embed.add_field(name="`!help`", value="Shows this menu.", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="setportal")
@commands.has_permissions(administrator=True)
async def set_portal(ctx, channel: discord.VoiceChannel = None):
    if not channel:
        await ctx.send("❌ Please tag a valid voice channel. Example: `!setportal #Departure-Gate`")
        return

    config = load_config()
    config[str(ctx.guild.id)] = channel.id
    save_config(config)

    await ctx.send(f"🍂 **Portal established.** Anyone entering **{channel.name}** will be instantly moved.")

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    if after.channel:
        config = load_config()
        portal_channel_id = config.get(str(member.guild.id))

        if portal_channel_id and after.channel.id == portal_channel_id:
            current_vc = after.channel
            guild = member.guild

            destination_vcs = [
                vc for vc in guild.voice_channels 
                if vc.id != portal_channel_id and vc.permissions_for(member).connect
            ]

            if destination_vcs:
                target_vc = random.choice(destination_vcs)
                try:
                    await member.move_to(target_vc)
                    print(f"Successfully sent {member.display_name} to {target_vc.name}")
                except discord.Forbidden:
                    try:
                        await member.send("You cannot be departed due to administrative permissions.")
                    except discord.Forbidden:
                        pass
            else:
                try:
                    await member.move_to(None) 
                except discord.Forbidden:
                    pass

                # Simplified, non-corny DM message
                try:
                    embed = discord.Embed(
                        title="Disconnected",
                        description=f"You entered the portal in **{guild.name}**, but there were no open channels left to move you to.",
                        color=discord.Color.dark_grey()
                    )
                    await member.send(embed=embed)
                except discord.Forbidden:
                    print(f"Could not send DM to {member.display_name} (DMs closed).")

TOKEN = os.getenv("DISCORD_TOKEN")

if __name__ == "__main__":
    if not TOKEN:
        print("CRITICAL ERROR: DISCORD_TOKEN environment variable is missing!")
    else:
        bot.run(TOKEN)
