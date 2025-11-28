import asyncio
import httpx

async def test():
    payload = {"text": "Testing Sesame CSM for expressive narration."}
    async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
        resp = await client.post("https://launchbrand-me--audibound-sesame-generate-speech.modal.run", json=payload)
        print(resp.status_code)
        print("length", len(resp.content))
        if resp.status_code == 200 and resp.content:
            with open("sesame_test.wav", "wb") as f:
                f.write(resp.content)
            print("Saved sesame_test.wav")

asyncio.run(test())
