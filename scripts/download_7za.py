import urllib.request
import zipfile
import os
import io

def download_7za():
    url = "https://www.7-zip.org/a/7za920.zip"
    print(f"Downloading 7za from {url}...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=10)
        with zipfile.ZipFile(io.BytesIO(resp.read())) as z:
            z.extract("7za.exe", "bin")
        print("Successfully downloaded 7za.exe to bin/")
    except Exception as e:
        print(f"Failed to download 7za: {e}")

if __name__ == "__main__":
    os.makedirs("bin", exist_ok=True)
    download_7za()
