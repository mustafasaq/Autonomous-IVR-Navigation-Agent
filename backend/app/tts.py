import requests


#convert text to mp3 bytes for when agent connects to a human
def elevenlabs_tts_mp3(api_key:str, voice_id:str, text:str)->bytes:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_flash_v2_5",
        # tweak this if the voice sounds weird
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.7},
    }

    r = requests.post(url, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    return r.content
