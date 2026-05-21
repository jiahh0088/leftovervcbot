
import os
import random
import json
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.members = True
intents.message_content = True  # Required for the setup prefix command

bot = commands.Bot(command_prefix="!", intents=intents)

# Simple local file storage to remember your portal settings if the bot restarts
CONFIG_FILE = "portal_config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)

DEPARTURE_QUOTES = [
    "We're still here.",
    "The world ended, and we're just the leftovers.",
    "There is no family.",
    "The sudden departure spares no one."
]

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="the 2% vanish"))

@bot.command(name="setportal")
@commands.has_permissions(administrator=True)
async def set_portal(ctx, channel: discord.VoiceChannel = None):
    """Sets the designated tracking channel for the Sudden Departure."""
    if not channel:
        await ctx.send("❌ Please tag a valid voice channel. Example: `!setportal #Departure-Gate`")
        return

    config = load_config()
    config[str(ctx.guild.id)] = channel.id
    save_config(config)

    await ctx.send(f"🍂 **The Portal has been established.** Anyone who enters **{channel.name}** will face immediate departure.")

@bot.event
async def on_voice_state_update(member, before, after):
    # Ignore bot actions to prevent infinite loops
    if member.bot:
        return

    # Check if the user just joined a channel (after.channel must exist)
    if after.channel:
        config = load_config()
        portal_channel_id = config.get(str(member.guild.id))

        # If they joined the configured portal channel...
        if portal_channel_id and after.channel.id == portal_channel_id:
            current_vc = after.channel
            guild = member.guild

            # Gather all other available voice channels they could be flung into
            destination_vcs = [
                vc for vc in guild.voice_channels 
                if vc.id != portal_channel_id and vc.permissions_for(member).connect
            ]

            if destination_vcs:
                # Target found! Fling them instantly
                target_vc = random.choice(destination_vcs)
                try:
                    await member.move_to(target_vc)
                    print(f"Successfully sent {member.display_name} to {target_vc.name}")
                except discord.Forbidden:
                    # If the bot lacks overall permissions to move this specific user
                    try:
                        await member.send("⚡ The Departure tried to take you, but administrative powers bound you to the portal.")
                    except discord.Forbidden:
                        pass
            else:
                # No channels available or accessible. Boot them and DM.
                try:
                    await member.move_to(None) # Disconnect them from the portal
                except discord.Forbidden:
                    pass

                try:
                    embed = discord.Embed(
                        title="🍂 Left Behind",
                        description=f"You stepped into the departure rift in **{guild.name}**, but there was nowhere left to send you. The world is empty.",
                        color=discord.Color.dark_grey()
                    )
                    embed.set_footer(text=random.choice(DEPARTURE_QUOTES))
                    await member.send(embed=embed)
                except discord.Forbidden:
                    # Occurs if the user has direct messages turned off for server members
                    print(f"Could not send DM to {member.display_name} because their DMs are closed.")

# Railway deployment token injection
TOKEN = os.getenv("DISCORD_TOKEN")

if __name__ == "__main__":
    if not TOKEN:
        print("CRITICAL ERROR: DISCORD_TOKEN environment variable is missing!")
    else:
        bot.run(TOKEN)
