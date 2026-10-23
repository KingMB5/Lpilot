from flask import Flask
import os
app = Flask(__name__)
@app.get("/")
def home(): return "salam"
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
