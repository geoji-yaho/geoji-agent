import os
import requests
from typing import List, Dict

# Curated list of public sample meme images (famous meme templates / reaction faces)
SAMPLE_MEME_URLS: List[Dict[str, str]] = [
    {
        "name": "meme_shocked_face",
        "url": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=500&auto=format&fit=crop&q=80",
    },
    {
        "name": "meme_frugal_thinking",
        "url": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=500&auto=format&fit=crop&q=80",
    },
    {
        "name": "meme_surprised_cat",
        "url": "https://images.unsplash.com/photo-1514888286974-6c03e2ca1dba?w=500&auto=format&fit=crop&q=80",
    },
    {
        "name": "meme_laughing_man",
        "url": "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=500&auto=format&fit=crop&q=80",
    },
]


def download_meme_samples(output_dir: str = "inputs", timeout: int = 10) -> List[str]:
    """
    Downloads sample meme images into output_dir.
    Returns a list of saved image file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    saved_paths = []

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    for item in SAMPLE_MEME_URLS:
        name = item["name"]
        url = item["url"]
        target_path = os.path.join(output_dir, f"{name}.jpg")

        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code == 200 and len(response.content) > 0:
                with open(target_path, "wb") as f:
                    f.write(response.content)
                saved_paths.append(target_path)
                print(f"[Fetcher] Saved: {target_path}")
            else:
                print(f"[Fetcher] Failed to download {name}: HTTP {response.status_code}")
        except Exception as e:
            print(f"[Fetcher] Error downloading {name}: {e}")

    return saved_paths


if __name__ == "__main__":
    download_meme_samples()
