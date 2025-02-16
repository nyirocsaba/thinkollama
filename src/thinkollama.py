import os
import re
import logging
import httpx
import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from datetime import datetime
from fastapi.responses import StreamingResponse
from ollama import Client
from contextlib import asynccontextmanager

# Load environment variables
load_dotenv()

# Read variables from .env
DEEPSEEK_URL = os.getenv("DEEPSEEK_URL", "http://127.0.0.1:11434")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-r1:14b")
MODELS_PREFIX = os.getenv("MODELS_PREFIX", "think:")

# Initialize FastAPI app
app = FastAPI()

# Initialize Ollama clients
deepseek = Client(host=DEEPSEEK_URL)
ollama = Client(host=OLLAMA_URL)

async def check_and_pull_model(retries=5, delay=10):
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{OLLAMA_URL}/api/tags")
                if response.status_code == 200:
                    models = response.json().get("models", [])
                    if not any(model.get("model") == DEEPSEEK_MODEL for model in models):
                        logging.info(f"Pulling missing model: {DEEPSEEK_MODEL}")
                        ollama.pull(DEEPSEEK_MODEL)
                    return
        except Exception as e:
            logging.error(f"Error checking/pulling model '{DEEPSEEK_MODEL}' (Attempt {attempt + 1}/{retries}): {str(e)}")
            if attempt < retries - 1:
                logging.info(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
    raise RuntimeError(f"Failed to pull DeepSeek model '{DEEPSEEK_MODEL}' after multiple attempts. Exiting application.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await check_and_pull_model()
    yield

app = FastAPI(lifespan=lifespan)

class ChatRequest(BaseModel):
    model: str
    messages: list[dict] = Field(default_factory=list)
    stream: bool = True

def extract_thinking_section(text: str) -> str:
    """Extracts content enclosed within <think>...</think> tags."""
    match = re.search(r"<think>(.*?)</think>", text, re.DOTALL)
    return match.group(1).strip() if match else ""

@app.post("/api/chat")
async def chat_completion(request: ChatRequest):
    try:
        model = request.model.replace(MODELS_PREFIX, "")
        updated_messages = request.messages.copy()

        # Use DeepSeek for reasoning if the model has the required prefix and isn't the DEEPSEEK_MODEL itself
        if request.model.startswith(MODELS_PREFIX) and model != DEEPSEEK_MODEL:
            deepseek_response = deepseek.chat(
                model=DEEPSEEK_MODEL,
                messages=request.messages,
                stream=False
            )

            # Extract CoT from <think>...</think>
            full_reasoning_text = deepseek_response.message.content.strip()
            reasoning_text = extract_thinking_section(full_reasoning_text)

            # Append hidden instructions and CoT user messages
            if reasoning_text:
                updated_messages.append(
                    {"role": "user", "content": f"Below is a hidden chain-of-thought (CoT) derived from the user's query. Use it as guidance when crafting your response.\n<think>{reasoning_text}</think>"}
                )
                logging.info(f"Extracted CoT: {reasoning_text}")

        # Streaming response handling
        if request.stream:
            def stream_ollama():
                for chunk in ollama.chat(model=model, messages=updated_messages, stream=True):
                    yield (chunk.model_dump_json() + "\n").encode("utf-8")

            return StreamingResponse(stream_ollama(), media_type="application/json")

        # Non-streaming response
        ollama_response = ollama.chat(model=model, messages=updated_messages, stream=False)

        ollama_response.model = request.model
        return ollama_response

    except Exception as e:
        logging.error(f"Error in /api/chat: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/tags")
async def get_tags():
    """Proxy request to Ollama /tags endpoint."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")

        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch tags from Ollama")

        data = response.json()
        for model in data.get("models", []):
            model["name"] = f"{MODELS_PREFIX}{model.get('name', '')}"
            model["model"] = f"{MODELS_PREFIX}{model.get('model', '')}"

        return data
    except Exception as e:
        logging.error(f"Error in /api/tags: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/version")
async def get_version():
    """Proxy request to Ollama /version endpoint."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{OLLAMA_URL}/api/version")

        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch version from Ollama")

        return response.json()

    except Exception as e:
        logging.error(f"Error in /api/version: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")