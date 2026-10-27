"""OctoBot's backup stop-loss without pausing the trader (runs inside OctoBot, via a startup patch).

When the bridge is down, OctoBot's own ProfitabilityThreshold automation is the stop-loss. It used
to end with StopStrategiesAndPauseTrader, but a paused trader saves its paper account without an
ending portfolio and OctoBot rebuilds it from the profile's starting funds on the next start. The
patched StopTrading action instead waits until the sold account is saved, parks on 观望 and
restarts, and leaves a marker so the bridge can report what happened when it comes back.
"""
import asyncio
import json
import os
import time

IDLE_PROFILE = "astra_idle"
USER_DIR = os.getenv("ASTRA_OCTOBOT_USER_DIR", "/octobot/user")
MARKER = "astra_backup_stop.json"
# OctoBot saves the paper portfolio 5 s after the last change; restarting earlier loses the sale
SAVE_WAIT_S = 12


def target_profile(default_profile: str, user_dir: str | None = None) -> str:
    user_dir = user_dir or USER_DIR
    return IDLE_PROFILE if os.path.isdir(os.path.join(user_dir, "profiles", IDLE_PROFILE)) else default_profile


def write_marker(profile: str, user_dir: str | None = None) -> None:
    user_dir = user_dir or USER_DIR
    try:
        with open(os.path.join(user_dir, MARKER), "w", encoding="utf-8") as fh:
            json.dump({"ts": time.time(), "profile": profile, "action": "StopTrading"}, fh)
    except OSError:
        pass  # the stop itself matters more than the marker


async def before_restart(default_profile: str, wait_s: float = SAVE_WAIT_S) -> str:
    """Let the sold paper account reach the disk, then say which profile to restart on."""
    profile = target_profile(default_profile)
    await asyncio.sleep(wait_s)
    write_marker(profile)
    return profile
