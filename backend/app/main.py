from fastapi import FastAPI

app = FastAPI(title="ImageFind")


@app.get("/health")
def health():
    return {"status": "ok"}
