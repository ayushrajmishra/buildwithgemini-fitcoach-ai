# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import urllib.request
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors.agent_engine_sandbox_code_executor import (
    AgentEngineSandboxCodeExecutor,
)
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from app.a2ui_utils import a2ui_callback

FIRESTORE_PROJECT_ID = "qwiklabs-gcp-03-33b2ff20a265"
CLOUD_STORAGE_BUCKET = "fitcoach-ai-media-qwiklabs-gcp-03-33b2ff20a265"

# Load AgentEngineSandboxCodeExecutor from deployment_metadata.json
_metadata_path = Path(__file__).parent.parent / "deployment_metadata.json"
_code_executor = None

if _metadata_path.exists():
    with open(_metadata_path, "r") as _f:
        _meta = json.load(_f)
        _sandbox_id = _meta.get("sandbox_resource_name")
        _agent_engine_id = _meta.get("remote_agent_runtime_id")
        if _sandbox_id:
            _code_executor = AgentEngineSandboxCodeExecutor(sandbox_resource_name=_sandbox_id)
        elif _agent_engine_id:
            _code_executor = AgentEngineSandboxCodeExecutor(agent_engine_resource_name=_agent_engine_id)


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are FitCoach AI, a personal fitness and workout coach. "
        "You help users discover workout routines, view exercises from your Firestore catalog or external exercise database, "
        "generate visual exercise illustration images, log completed workouts, and calculate personalized fitness metrics. "
        "You can also write and execute Python code in a secure Agent Engine sandbox for complex calculations. "
        "You remember all user facts, preferences, dietary restrictions, and allergies across sessions and conversations. "
        "Ensure that any user-specified allergies or dietary restrictions are remembered and strictly respected in all fitness and nutritional recommendations."
    ),
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


def _get_firestore_client() -> firestore.Client:
    return firestore.Client(project=FIRESTORE_PROJECT_ID)


def generate_workout_image(prompt: str, tool_context: ToolContext = None) -> str:
    """Generate an exercise or workout illustration image using the gemini-3.1-flash-lite-image model in global region.
    Saves the image to Playground Artifacts and uploads it directly to Cloud Storage, returning its public HTTPS URL.

    Args:
        prompt: A description of the fitness image to generate (e.g., 'a minimalist illustration of a barbell bench press').

    Returns:
        The public HTTPS URL of the uploaded image in Cloud Storage.
    """
    genai_client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT_ID, location="global")
    response = genai_client.models.generate_content(
        model="gemini-3.1-flash-lite-image",
        contents=prompt,
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )

    image_part = response.candidates[0].content.parts[0]
    image_bytes = image_part.inline_data.data
    mime_type = image_part.inline_data.mime_type or "image/jpeg"

    ext = "png" if "png" in mime_type else "jpg"
    filename = f"workout_{uuid.uuid4().hex[:8]}.{ext}"

    # 1. Save to Playground Artifacts panel if tool_context available
    if tool_context is not None:
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        tool_context.save_artifact(filename=filename, artifact=artifact_part)

    # 2. Upload bytes directly to Cloud Storage (no disk I/O)
    storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
    bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET)
    blob = bucket.blob(filename)
    blob.upload_from_string(image_bytes, content_type=mime_type)

    public_url = f"https://storage.googleapis.com/{CLOUD_STORAGE_BUCKET}/{filename}"
    return f"Image generated successfully! Public URL: {public_url}"


def fetch_external_exercises(limit: int = 5) -> list[dict]:
    """Fetch real exercise data from the WGER open-source public fitness database.

    Args:
        limit: Number of exercises to retrieve (default 5).

    Returns:
        A list of exercise dictionaries containing name, category, muscle targets, and description.
    """
    api_key = os.environ.get("WGER_API_KEY", "")
    url = f"https://wger.de/api/v2/exerciseinfo/?limit={min(limit, 10)}"
    headers = {"User-Agent": "FitCoachAI/1.0"}
    if api_key:
        headers["Authorization"] = f"Token {api_key}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            results = []
            for item in data.get("results", []):
                name = "Unknown Exercise"
                desc = ""
                for trans in item.get("translations", []):
                    if trans.get("language") == 2 or not desc:
                        name = trans.get("name", name)
                        desc = trans.get("description", "").replace("<p>", "").replace("</p>", "").strip()
                category = item.get("category", {}).get("name", "General")
                muscles = [m.get("name", "") for m in item.get("muscles", []) if m.get("name")]

                results.append({
                    "name": name,
                    "category": category,
                    "target_muscles": muscles,
                    "description": desc[:200] if desc else "No detailed description available.",
                })
            return results
    except Exception as e:
        return [{"error": f"Failed to fetch external exercises: {str(e)}"}]


def calculate_fitness_metrics(
    weight_kg: float,
    height_cm: float,
    age: int,
    gender: str = "male",
    activity_level: str = "moderate",
    fitness_goal: str = "maintenance",
) -> dict:
    """Calculate key fitness metrics including BMI, BMR (Basal Metabolic Rate), TDEE, and daily macro targets.

    Args:
        weight_kg: Weight in kilograms (e.g. 70.0).
        height_cm: Height in centimeters (e.g. 175.0).
        age: Age in years (e.g. 25).
        gender: 'male' or 'female'.
        activity_level: 'sedentary', 'light', 'moderate', 'active', or 'very_active'.
        fitness_goal: 'fat_loss', 'muscle_gain', or 'maintenance'.

    Returns:
        A dictionary containing BMI, category, BMR, TDEE, target_calories, and daily protein/carbs/fat grams.
    """
    # 1. BMI calculation
    height_m = height_cm / 100.0
    bmi = round(weight_kg / (height_m * height_m), 1)
    if bmi < 18.5:
        bmi_category = "Underweight"
    elif bmi < 25.0:
        bmi_category = "Normal weight"
    elif bmi < 30.0:
        bmi_category = "Overweight"
    else:
        bmi_category = "Obese"

    # 2. BMR calculation (Mifflin-St Jeor)
    if gender.lower().startswith("f"):
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161
    else:
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5

    # 3. TDEE multiplier
    activity_multipliers = {
        "sedentary": 1.2,
        "light": 1.375,
        "moderate": 1.55,
        "active": 1.725,
        "very_active": 1.9,
    }
    mult = activity_multipliers.get(activity_level.lower().strip(), 1.55)
    tdee = round(bmr * mult)

    # 4. Target calories based on goal
    goal_map = {
        "fat_loss": -500,
        "muscle_gain": 300,
        "maintenance": 0,
    }
    target_calories = tdee + goal_map.get(fitness_goal.lower().strip(), 0)

    # 5. Macros (Protein: 2g/kg, Fat: 25% cals, Carbs: remaining)
    protein_g = round(weight_kg * 2.0)
    fat_g = round((target_calories * 0.25) / 9.0)
    carbs_g = round((target_calories - (protein_g * 4 + fat_g * 9)) / 4.0)

    return {
        "bmi": bmi,
        "bmi_category": bmi_category,
        "bmr_kcal": round(bmr),
        "tdee_kcal": tdee,
        "target_daily_calories": target_calories,
        "macros_grams": {
            "protein_g": protein_g,
            "carbs_g": carbs_g,
            "fat_g": fat_g,
        },
    }


def search_workout_routines(category: str = "", difficulty: str = "") -> list[dict]:
    """Search available workout routines from the Firestore catalog.

    Args:
        category: Optional filter for category (e.g. 'strength', 'cardio', 'mobility').
        difficulty: Optional filter for difficulty level (e.g. 'beginner', 'intermediate', 'advanced').

    Returns:
        A list of workout routine summaries matching the criteria.
    """
    db = _get_firestore_client()
    query_ref = db.collection("workout_routines")

    if category:
        query_ref = query_ref.where("category", "==", category.lower().strip())
    if difficulty:
        query_ref = query_ref.where("difficulty", "==", difficulty.lower().strip())

    docs = query_ref.stream()
    routines = []
    for doc in docs:
        data = doc.to_dict()
        routines.append(data)

    if not routines and (category or difficulty):
        # Fallback stream all if exact query match returned empty
        all_docs = db.collection("workout_routines").stream()
        routines = [d.to_dict() for d in all_docs]

    return routines


def get_workout_routine_details(routine_id: str) -> dict:
    """Fetch complete details for a specific workout routine by ID.

    Args:
        routine_id: The unique ID of the routine (e.g., 'full_body_strength', 'hiit_cardio_blast').

    Returns:
        A dictionary containing the routine details, exercises, sets, and reps.
    """
    db = _get_firestore_client()
    doc_ref = db.collection("workout_routines").document(routine_id.strip())
    doc = doc_ref.get()

    if doc.exists:
        return doc.to_dict()
    return {"error": f"Routine '{routine_id}' not found."}


def log_completed_workout(
    routine_id: str, duration_minutes: int, notes: str = "", user_id: str = "default_user"
) -> str:
    """Log a completed workout session to Firestore.

    Args:
        routine_id: The ID of the workout routine completed.
        duration_minutes: The total duration in minutes spent working out.
        notes: Optional user notes or feedback about the workout.
        user_id: The user ID logging the workout.

    Returns:
        A confirmation message with the log entry ID.
    """
    db = _get_firestore_client()
    log_data = {
        "user_id": user_id,
        "routine_id": routine_id,
        "duration_minutes": duration_minutes,
        "notes": notes,
        "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    doc_ref = db.collection("workout_logs").document()
    doc_ref.set(log_data)
    return f"Workout logged successfully! Entry ID: {doc_ref.id}"


def get_weather(query: str) -> str:
    """Simulates a web search. Use it get information on weather.

    Args:
        query: A string containing the location to get weather information for.

    Returns:
        A string with the simulated weather information for the queried location.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        query: The name of the city or timezone query.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


async def generate_memories_callback(callback_context: CallbackContext):
    """Callback to extract and save user facts/preferences (including allergies) to Memory Bank at the end of each turn."""
    await callback_context.add_session_to_memory()
    return None


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=_code_executor,
    tools=[
        PreloadMemoryTool(),
        generate_workout_image,
        fetch_external_exercises,
        calculate_fitness_metrics,
        search_workout_routines,
        get_workout_routine_details,
        log_completed_workout,
        get_weather,
        get_current_time,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
