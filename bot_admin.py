import aiohttp

RUNNER_URL = os.getenv("RUNNER_URL", "https://your-runner.railway.app")
RUNNER_TOKEN = os.getenv("RUNNER_TOKEN", "runner_secret_token_123")

async def deploy_userbot_to_runner(user_id, session_string, plan, expired):
    """Deploy userbot ke runner via API"""
    async with aiohttp.ClientSession() as session:
        payload = {
            "runner_token": RUNNER_TOKEN,
            "user_id": user_id,
            "session_string": session_string,
            "plan": plan,
            "expired": expired
        }
        
        try:
            async with session.post(
                f"{RUNNER_URL}/api/start_userbot",
                json=payload
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    return {"error": f"Status {resp.status}"}
        except Exception as e:
            return {"error": str(e)}

# Panggil ini setelah user selesai setup (di handle_setup_steps)
# result = await deploy_userbot_to_runner(user_id, session_string, plan, expired)
