import os
import webbrowser
from threading import Timer

import uvicorn


if __name__ == "__main__":
    mode = os.environ.get("HPLC_LAUNCH_MODE", "production").lower()
    url = "http://127.0.0.1:8765/"
    if mode == "test":
        url += "?mode=test"
    Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run("hplc_web.app:app", host="127.0.0.1", port=8765)
