import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import asyncio

API_BASE = "http://localhost:5000"  # Change if hosted remotely
TOKEN = "xxx"    # Replace with your bot token

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
session = None

@bot.event
async def on_ready():
    global session
    if session is None:
        session = aiohttp.ClientSession()

    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    print(f"Bot ready as {bot.user} (ID: {bot.user.id})")

@tree.command(name="route", description="Calculate route using gates, wormholes, and bridges")
@app_commands.describe(
    start="Start system",
    end="End system",
    bridge_type="Bridge type to allow",
    max_cynos="Maximum number of cyno jumps allowed",
    use_ansis="Allow Ansiblex jump bridges"
)
@app_commands.choices(bridge_type=[
    app_commands.Choice(name="None", value="none"),
    app_commands.Choice(name="Titan", value="titan"),
    app_commands.Choice(name="Blops", value="blops"),
    app_commands.Choice(name="Titan + Blops", value="titan,blops"),
])
async def route(
    interaction: discord.Interaction,
    start: str,
    end: str,
    bridge_type: app_commands.Choice[str],
    max_cynos: int = 3,
    use_ansis: bool = True
):
    await interaction.response.defer(thinking=True, ephemeral=True)

    query = (
        f"start={start}&end={end}"
        f"&bridge_type={bridge_type.value}"
        f"&max_cynos={max_cynos}"
        f"&use_ansis={'true' if use_ansis else 'false'}"
    )

    async with session.get(f"{API_BASE}/route?{query}") as r:
        data = await r.json()
        if r.status != 200:
            return await interaction.followup.send(f"❌ Error: {data.get('error')}", ephemeral=True)

        steps = data["steps"]
        lines = [f"**Route from `{data['from']}` to `{data['to']}`**"]
        for step in steps:
            line = f"{step['type'].upper()}: {step['system']}"
            if step["type"] == "wormhole":
                wh = step.get("info", {})
                line += f" ({wh.get('wh_type', '?')})"
            lines.append(line)

        await interaction.followup.send("\n".join(lines[:20]), ephemeral=True)

@tree.command(name="add_wh", description="Add a custom wormhole between two systems")
@app_commands.describe(
    a_desto="First system name (entry side)",
    b_desto="Second system name (exit side)",
    sig_a="Signature ID on first system",
    sig_b="Signature ID on second system",
    wh_type="Wormhole type (e.g. K162)"
)
async def add_wh(
    interaction: discord.Interaction,
    a_desto: str,
    b_desto: str,
    sig_a: str,
    sig_b: str,
    wh_type: str = "K162"
):
    await interaction.response.defer(thinking=True, ephemeral=True)

    payload = {
        "a": a_desto,
        "b": b_desto,
        "sig_a": sig_a,
        "sig_b": sig_b,
        "wh_type": wh_type,
        "max_remaining": "unknown",
        "private": False
    }

    async with session.post(f"{API_BASE}/add_wh", json=payload) as r:
        data = await r.json()
        await interaction.followup.send(data.get("message", str(data)), ephemeral=True)

@tree.command(name="del_wh", description="Delete a custom wormhole by sig ID")
async def del_wh(interaction: discord.Interaction, system_name: str, sig_id: str):
    await interaction.response.defer(thinking=True, ephemeral=True)
    payload = {"system_name": system_name, "sig_id": sig_id}
    async with session.post(f"{API_BASE}/del_wh", json=payload) as r:
        data = await r.json()
        await interaction.followup.send(data.get("message", str(data)), ephemeral=True)

@tree.command(name="list_sig", description="List wormhole sigs for a system")
async def list_sig(interaction: discord.Interaction, system_name: str):
    await interaction.response.defer(thinking=True, ephemeral=True)
    async with session.get(f"{API_BASE}/list_sig?system_name={system_name}") as r:
        data = await r.json()
        if "wormholes" not in data:
            return await interaction.followup.send(data.get("message", str(data)), ephemeral=True)

        lines = [f"**Wormholes for {system_name}:**"]
        for wh in data["wormholes"]:
            lines.append(f"{wh['a']} ↔ {wh['b']} ({wh.get('wh_type', '?')})")
        await interaction.followup.send("\n".join(lines[:20]), ephemeral=True)

@tree.command(name="titan_add", description="Add a Titan bridge system")
async def titan_add(interaction: discord.Interaction, system_name: str):
    await interaction.response.defer(thinking=True, ephemeral=True)
    async with session.post(f"{API_BASE}/titan_bridge/add", json={"system_name": system_name}) as r:
        data = await r.json()
        await interaction.followup.send(data.get("message", str(data)), ephemeral=True)

@tree.command(name="titan_remove", description="Remove a Titan bridge system")
async def titan_remove(interaction: discord.Interaction, system_name: str):
    await interaction.response.defer(thinking=True, ephemeral=True)
    async with session.post(f"{API_BASE}/titan_bridge/remove", json={"system_name": system_name}) as r:
        data = await r.json()
        await interaction.followup.send(data.get("message", str(data)), ephemeral=True)

@tree.command(name="titan_list", description="List all Titan bridge systems")
async def titan_list(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    async with session.get(f"{API_BASE}/titan_bridge/list") as r:
        data = await r.json()
        await interaction.followup.send("**Titan Bridges:**\n" + "\n".join(data["bridges"]), ephemeral=True)

@bot.event
async def on_close():
    if session:
        await session.close()

bot.run(TOKEN)